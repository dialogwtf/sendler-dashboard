import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { BACKOFF_MAX_MS, DashSocket, FALLBACK_RETRY_MS, HIDDEN_CLOSE_MS, PING_MS, type SocketStatus } from "./ws";
import type { WsEvent } from "./types";

class FakeSocket {
    static all: FakeSocket[] = [];
    readyState = 0;
    sent: unknown[] = [];
    closedWith: number | null = null;
    onopen: ((ev: unknown) => void) | null = null;
    onclose: ((ev: { code: number }) => void) | null = null;
    onerror: ((ev: unknown) => void) | null = null;
    onmessage: ((ev: { data: unknown }) => void) | null = null;
    url: string;
    constructor(url: string) {
        this.url = url;
        FakeSocket.all.push(this);
    }
    send(data: string) {
        this.sent.push(JSON.parse(data));
    }
    close(code = 1000) {
        this.closedWith = code;
        this.readyState = 3;
    }
    // test helpers
    open() {
        this.readyState = 1;
        this.onopen?.({});
    }
    msg(m: unknown) {
        this.onmessage?.({ data: JSON.stringify(m) });
    }
    ack() {
        this.msg({ ok: true, subs: [] });
    }
    drop(code = 1006) {
        this.readyState = 3;
        this.onclose?.({ code });
    }
}

const last = () => FakeSocket.all[FakeSocket.all.length - 1];
const tick = () => Promise.resolve(); // let queued microtasks (subscription flush) run

function makeDoc() {
    const handlers = new Set<() => void>();
    return {
        visibilityState: "visible" as DocumentVisibilityState,
        addEventListener: (_: string, h: () => void) => handlers.add(h),
        removeEventListener: (_: string, h: () => void) => handlers.delete(h),
        set(v: DocumentVisibilityState) {
            this.visibilityState = v;
            for (const h of handlers) h();
        },
    };
}

function make(opts: { random?: () => number; doc?: ReturnType<typeof makeDoc> } = {}) {
    const statuses: SocketStatus[] = [];
    const s = new DashSocket({
        url: "wss://test/ws",
        createSocket: (url) => new FakeSocket(url),
        random: opts.random ?? (() => 0.5), // no jitter: delay = step
        doc: opts.doc as never,
    });
    s.onStatus((st) => statuses.push(st));
    return { s, statuses };
}

const trade = (token: string, key: string): WsEvent => ({
    ch: "trade", ts: "2026-11-09T15:00:00Z",
    d: { token, side: "buy", near: 1, usd: 3, px: 1e-20, wallet: "w.near", venue: "Rhea", key },
});

beforeEach(() => {
    FakeSocket.all = [];
    vi.useFakeTimers();
});
afterEach(() => vi.useRealTimers());

describe("subscriptions", () => {
    it("connects lazily and subscribes everything on open", async () => {
        const { s } = make();
        expect(FakeSocket.all).toHaveLength(0);
        s.subscribe("launch", () => {});
        s.subscribe("network", () => {});
        await tick();
        expect(FakeSocket.all).toHaveLength(1);
        last().open();
        expect(last().sent).toEqual([{ op: "sub", ch: ["launch", "network"] }]);
    });

    it("sends only the difference, ref-counted", async () => {
        const { s } = make();
        const a = s.subscribe("trade:x.near", () => {});
        s.subscribe("move", () => {});
        await tick();
        last().open();
        last().ack();
        last().sent = [];
        const b = s.subscribe("trade:x.near", () => {}); // same channel, second widget
        await tick();
        expect(last().sent).toEqual([]);
        a();
        await tick();
        expect(last().sent).toEqual([]); // still one listener
        b();
        await tick();
        expect(last().sent).toEqual([{ op: "unsub", ch: ["trade:x.near"] }]);
    });

    it("does not send a token channel while its base channel is on", async () => {
        const { s } = make();
        s.subscribe("trade", () => {});
        s.subscribe("trade:x.near", () => {});
        s.subscribe("move:y.near", () => {});
        expect(s.wantedChannels()).toEqual(["trade", "move:y.near"]);
    });

    it("caps at 20 channels", () => {
        const { s } = make();
        for (let i = 0; i < 25; i++) s.subscribe(`trade:t${i}.near` as `trade:${string}`, () => {});
        expect(s.wantedChannels()).toHaveLength(20);
    });

    it("routes a base event to token listeners by d.token", async () => {
        const { s } = make();
        const base = vi.fn();
        const mine = vi.fn();
        const other = vi.fn();
        s.subscribe("trade", base);
        s.subscribe("trade:x.near", mine);
        s.subscribe("trade:y.near", other);
        await tick();
        last().open();
        last().ack();
        last().msg(trade("x.near", "k1"));
        expect(base).toHaveBeenCalledTimes(1);
        expect(mine).toHaveBeenCalledTimes(1);
        expect(other).not.toHaveBeenCalled();
    });

    it("an unknown channel is dropped and the rest re-subscribed, so the sync still happens", async () => {
        const { s } = make();
        const synced = vi.fn();
        s.onSynced(synced);
        s.subscribe("launch", () => {});
        s.subscribe("trade:bad token" as `trade:${string}`, () => {});
        await tick();
        last().open();
        last().sent = [];
        last().msg({ error: "unknown_channel", bad: ["trade:bad token"] });
        expect(last().sent).toEqual([{ op: "sub", ch: ["launch"] }]);
        last().ack();
        expect(synced).toHaveBeenCalledTimes(1);
        expect(s.wantedChannels()).toEqual(["launch"]);
    });

    it("tells replies from events by ok/error (an old API sent ch on errors too)", async () => {
        const { s } = make();
        const fn = vi.fn();
        s.subscribe("trade", fn);
        await tick();
        last().open();
        last().msg({ error: "unknown_channel", ch: ["bogus"], bad: ["bogus"] });
        last().msg({ error: "bad_json" });
        expect(fn).not.toHaveBeenCalled();
    });

    it("emits synced once the subscription is acknowledged, status open", async () => {
        const { s, statuses } = make();
        const synced = vi.fn();
        s.onSynced(synced);
        s.subscribe("launch", () => {});
        await tick();
        last().open();
        expect(synced).not.toHaveBeenCalled();
        last().ack();
        expect(synced).toHaveBeenCalledTimes(1);
        expect(statuses).toEqual(["connecting", "open"]);
    });

    it("pings every 30 s", async () => {
        const { s } = make();
        s.subscribe("launch", () => {});
        await tick();
        last().open();
        last().ack();
        last().sent = [];
        vi.advanceTimersByTime(PING_MS);
        expect(last().sent).toEqual([{ op: "ping" }]);
    });
});

describe("reconnect", () => {
    it("backs off 1 → 2 → 4 … 30 s and resets after a good connection", async () => {
        const { s, statuses } = make();
        s.subscribe("launch", () => {});
        await tick();
        last().open();
        last().ack();

        const delays: number[] = [];
        for (let i = 0; i < 7; i++) {
            const before = FakeSocket.all.length;
            last().drop();
            let waited = 0;
            while (FakeSocket.all.length === before) {
                vi.advanceTimersByTime(100);
                waited += 100;
            }
            delays.push(waited);
        }
        expect(delays).toEqual([1000, 2000, 4000, 8000, 16000, BACKOFF_MAX_MS, BACKOFF_MAX_MS]);
        expect(statuses).toContain("reconnecting");

        // A good connection resets the step.
        last().open();
        last().ack();
        const before = FakeSocket.all.length;
        last().drop();
        vi.advanceTimersByTime(1000);
        expect(FakeSocket.all.length).toBe(before + 1);
    });

    it("re-subscribes everything and syncs again after a reconnect", async () => {
        const { s } = make();
        const synced = vi.fn();
        s.onSynced(synced);
        s.subscribe("launch", () => {});
        s.subscribe("trade:x.near", () => {});
        await tick();
        last().open();
        last().ack();
        last().drop();
        vi.advanceTimersByTime(1000);
        last().open();
        expect(last().sent).toEqual([{ op: "sub", ch: ["launch", "trade:x.near"] }]);
        last().ack();
        expect(synced).toHaveBeenCalledTimes(2);
    });

    it("jitter stays within half to one and a half of the step", () => {
        const lo = make({ random: () => 0 }).s;
        const hi = make({ random: () => 0.999 }).s;
        expect(lo.backoffMs(2)).toBe(2000);
        expect(hi.backoffMs(2)).toBeLessThanOrEqual(6000);
        expect(hi.backoffMs(2)).toBeGreaterThan(5900);
    });

    it("«Повторить» reconnects at once", async () => {
        const { s } = make();
        s.subscribe("launch", () => {});
        await tick();
        last().open();
        last().ack();
        last().drop();
        const before = FakeSocket.all.length;
        s.retryNow();
        expect(FakeSocket.all.length).toBe(before + 1);
    });
});

describe("4429 / 4503", () => {
    it.each([4429, 4503])("close %i → fallback, no loop, retry in 2 min", async (code) => {
        const { s, statuses } = make();
        s.subscribe("launch", () => {});
        await tick();
        last().open();
        last().drop(code);
        expect(s.status).toBe("fallback");
        const before = FakeSocket.all.length;
        vi.advanceTimersByTime(FALLBACK_RETRY_MS - 1);
        expect(FakeSocket.all.length).toBe(before);
        vi.advanceTimersByTime(1);
        expect(FakeSocket.all.length).toBe(before + 1);
        expect(statuses).toContain("fallback");
    });

    it("a new widget does not open a socket while in fallback", async () => {
        const { s } = make();
        s.subscribe("launch", () => {});
        await tick();
        last().open();
        last().drop(4429);
        const before = FakeSocket.all.length;
        s.subscribe("move", () => {});
        await tick();
        expect(FakeSocket.all.length).toBe(before);
    });
});

describe("hidden tab", () => {
    it("closes after 60 s hidden and reconnects when visible", async () => {
        const doc = makeDoc();
        const { s } = make({ doc });
        s.subscribe("launch", () => {});
        await tick();
        last().open();
        last().ack();
        doc.set("hidden");
        vi.advanceTimersByTime(HIDDEN_CLOSE_MS - 1);
        expect(last().closedWith).toBeNull();
        vi.advanceTimersByTime(1);
        expect(last().closedWith).toBe(1000);
        expect(s.status).toBe("hidden");
        const before = FakeSocket.all.length;
        vi.advanceTimersByTime(10 * 60_000);
        expect(FakeSocket.all.length).toBe(before); // no reconnect while hidden
        doc.set("visible");
        expect(FakeSocket.all.length).toBe(before + 1);
    });

    it("a short hide keeps the socket", async () => {
        const doc = makeDoc();
        const { s } = make({ doc });
        s.subscribe("launch", () => {});
        await tick();
        last().open();
        last().ack();
        doc.set("hidden");
        vi.advanceTimersByTime(30_000);
        doc.set("visible");
        vi.advanceTimersByTime(60_000);
        expect(last().closedWith).toBeNull();
        expect(s.status).toBe("open");
    });
});
