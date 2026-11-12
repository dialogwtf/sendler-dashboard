import { QueryClient } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FLUSH_MS, LiveSync } from "./live";
import { applyCandle, applyMove, applyNew, bucketStart, moveKey } from "./reducers";
import type { Candles, Launch, Move, NewItem, SmartTrade, SmartWallet, Trade, WsEvent } from "./types";
import { DashSocket } from "./ws";

const ts = "2026-11-09T15:00:30.000000Z";

const tradeEv = (key: string, over: Partial<WsEvent<"trade">["d"]> = {}): WsEvent<"trade"> => ({
    ch: "trade", ts,
    d: { token: "x.near", side: "buy", near: 2, usd: 7, px: 2e-20, wallet: "w.near", venue: "Rhea", key, ...over },
});

function setup() {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
    const socket = new DashSocket({ url: "wss://x", createSocket: () => { throw new Error("no network in tests"); } });
    const live = new LiveSync(qc, socket);
    return { qc, live };
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe("trades", () => {
    it("prepends to the token's trade list once, even if delivered twice", () => {
        const { qc, live } = setup();
        qc.setQueryData<Trade[]>(["token", "x.near", "trades", 50], []);
        live.handle(tradeEv("k1"));
        live.handle(tradeEv("k1")); // same trade again (e.g. via "trade" and "trade:x.near" as separate objects)
        vi.advanceTimersByTime(FLUSH_MS);
        expect(qc.getQueryData<Trade[]>(["token", "x.near", "trades", 50])!.map((t) => t._id)).toEqual(["k1"]);
    });

    it("batches: nothing changes before the flush", () => {
        const { qc, live } = setup();
        qc.setQueryData<Trade[]>(["token", "x.near", "trades", 50], []);
        live.handle(tradeEv("k1"));
        expect(qc.getQueryData<Trade[]>(["token", "x.near", "trades", 50])).toEqual([]);
        vi.advanceTimersByTime(FLUSH_MS);
        expect(qc.getQueryData<Trade[]>(["token", "x.near", "trades", 50])).toHaveLength(1);
    });

    it("updates the current candle and counts a duplicate trade once", () => {
        const { qc, live } = setup();
        const key = ["token", "x.near", "candles", "1m", 500];
        qc.setQueryData<Candles>(key, {
            decimals: 18,
            candles: [{ t: "2026-11-09T15:00:00Z", o: 1e-20, h: 1e-20, l: 1e-20, c: 1e-20, v: 1, v_usd: 3, buys: 1, sells: 0 }],
        });
        live.handle(tradeEv("k1"));
        live.handle(tradeEv("k1"));
        vi.advanceTimersByTime(FLUSH_MS);
        const c = qc.getQueryData<Candles>(key)!.candles;
        expect(c).toHaveLength(1);
        expect(c[0]).toMatchObject({ h: 2e-20, c: 2e-20, v: 3, buys: 2 });
    });

    it("smart feed takes only top-200 wallets, with their rank", () => {
        const { qc, live } = setup();
        qc.setQueryData<SmartWallet[]>(["smart", "top", 200], [
            { _id: "smart.near", rank: 3, roi: 0.4, pnl: 100, at: ts, win_rate: 0.5, tokens: 3, invested: 10, wins: 2 },
        ]);
        qc.setQueryData<SmartTrade[]>(["smart", "feed", 50], []);
        live.handle(tradeEv("k1", { wallet: "nobody.near" }));
        live.handle(tradeEv("k2", { wallet: "smart.near" }));
        vi.advanceTimersByTime(FLUSH_MS);
        const feed = qc.getQueryData<SmartTrade[]>(["smart", "feed", 50])!;
        expect(feed.map((r) => r._id)).toEqual(["k2"]);
        expect(feed[0].smart.rank).toBe(3);
    });
});

describe("snapshot + buffered events", () => {
    it("re-applies events that arrived while the REST snapshot was in flight", async () => {
        const { qc, live } = setup();
        let release!: (rows: Trade[]) => void;
        const p = qc.fetchQuery({
            queryKey: ["token", "x.near", "trades", 50],
            queryFn: () => new Promise<Trade[]>((r) => { release = r; }),
        });
        live.handle(tradeEv("k-live")); // arrives before the snapshot lands, cache still empty
        vi.advanceTimersByTime(FLUSH_MS);
        const older = { ...(await import("./reducers")).tradeFromEvent(tradeEv("k-old").d, ts) };
        release([older]);
        await p;
        expect(qc.getQueryData<Trade[]>(["token", "x.near", "trades", 50])!.map((t) => t._id)).toEqual(["k-live", "k-old"]);
    });

    it("re-applying the same snapshot twice does not duplicate rows", async () => {
        const { qc, live } = setup();
        live.handle(tradeEv("k1"));
        vi.advanceTimersByTime(FLUSH_MS);
        for (let i = 0; i < 2; i++) {
            await qc.fetchQuery({ queryKey: ["token", "x.near", "trades", 50], queryFn: async () => [] as Trade[], staleTime: 0 });
        }
        expect(qc.getQueryData<Trade[]>(["token", "x.near", "trades", 50])).toHaveLength(1);
    });
});

describe("launches", () => {
    const launchEv = (token: string, stage = "started", pad = "nearly"): WsEvent<"launch"> => ({
        ch: "launch", ts, d: { token, pad, stage, creator: "dev.near", symbol: "NEW", at: ts },
    });

    it("new token → stub on top and one throttled refetch; stage change updates in place", () => {
        const { qc, live } = setup();
        const key = ["launches", { pad: ["nearly", "justhoot"], hideFeeFarm: true, limit: 50 }];
        const fetches = vi.fn(async () => [] as Launch[]);
        qc.setQueryData<Launch[]>(key, []);
        qc.getQueryCache().build(qc, { queryKey: key, queryFn: fetches });
        live.handle(launchEv("a.near"));
        live.handle(launchEv("b.near"));
        live.handle(launchEv("c.near", "started", "intear")); // filtered out by pad
        vi.advanceTimersByTime(FLUSH_MS);
        expect(qc.getQueryData<Launch[]>(key)!.map((r) => r.token)).toEqual(["b.near", "a.near"]);
        live.handle(launchEv("a.near", "ready"));
        vi.advanceTimersByTime(FLUSH_MS);
        expect(qc.getQueryData<Launch[]>(key)!.find((r) => r.token === "a.near")!.stage).toBe("ready");
    });

    const row = (over: Partial<Launch> = {}): Launch => ({
        token: "x.near", pad: "nearly", symbol: "X", name: null, creator: "dev.near", creator_facts: null,
        stage: "trading", started: ts, ready: null, dev_buy: "0", tax: 1, fee_farm: false, pool: null,
        price_near: 1e-6, x: 2, x_max: 2.5, cap_near: 1000, cap_usd: 3000, trades: 4, volume_near: 10,
        last_trade: "2026-11-09T15:00:00Z", links: null, backfill: false, ...over,
    });
    const launchKey = ["launches", { pad: ["nearly"], hideFeeFarm: true, hideBlocked: false, limit: 50 }];

    it("a trade moves its launch row: price, growth, cap, counters — once per trade", () => {
        const { qc, live } = setup();
        qc.setQueryData<Launch[]>(launchKey, [row()]);
        qc.setQueryData(["meta", "x.near"], { symbol: "X", name: null, decimals: 18, icon: null, bot_code: "c" });
        // px is NEAR per raw unit: 3e-24 × 10^18 = 3e-6 per token — the price tripled
        live.handle(tradeEv("k1", { px: 3e-24, near: 5 }));
        live.handle(tradeEv("k1", { px: 3e-24, near: 5 }));
        vi.advanceTimersByTime(FLUSH_MS);
        const r = qc.getQueryData<Launch[]>(launchKey)![0];
        expect(r.price_near).toBeCloseTo(3e-6, 12);
        expect(r.x).toBeCloseTo(6, 9);
        expect(r.x_max).toBeCloseTo(6, 9);
        expect(r.cap_near).toBeCloseTo(3000, 6);
        expect(r.cap_usd).toBeCloseTo(9000, 6);
        expect(r).toMatchObject({ trades: 5, volume_near: 15, last_trade: ts });
    });

    it("a trade in a row without a price or decimals refetches the list soon; a full one later", () => {
        const { qc, live } = setup();
        // invalidateQueries refetches only watched queries; the call itself is what the sync owns
        const fetches = vi.spyOn(qc, "invalidateQueries").mockResolvedValue();
        qc.setQueryData<Launch[]>(launchKey, [row({ price_near: null, x: null, cap_near: null, cap_usd: null })]);
        live.handle(tradeEv("k1"));
        vi.advanceTimersByTime(FLUSH_MS);
        const r = qc.getQueryData<Launch[]>(launchKey)![0];
        expect(r).toMatchObject({ price_near: null, cap_usd: null, trades: 5 });
        vi.advanceTimersByTime(2_000);
        expect(fetches).toHaveBeenCalledTimes(1);

        qc.setQueryData<Launch[]>(launchKey, [row()]); // what the refetch brought: the row has its price now
        qc.setQueryData(["meta", "x.near"], { symbol: "X", name: null, decimals: 18, icon: null, bot_code: "c" });
        live.handle(tradeEv("k2", { px: 1e-24 }));
        vi.advanceTimersByTime(FLUSH_MS + 2_000);
        expect(fetches).toHaveBeenCalledTimes(1); // complete update: the refetch waits 10 s
        vi.advanceTimersByTime(8_000);
        expect(fetches).toHaveBeenCalledTimes(2);
    });

    it("trades of tokens not in the list and late trades leave rows alone", () => {
        const { qc, live } = setup();
        const rows = [row({ last_trade: "2026-11-09T15:01:00Z" })];
        qc.setQueryData<Launch[]>(launchKey, rows);
        live.handle(tradeEv("k1", { token: "other.near" }));
        live.handle(tradeEv("k2")); // ts 15:00:30 — older than the row's last trade
        vi.advanceTimersByTime(FLUSH_MS);
        expect(qc.getQueryData<Launch[]>(launchKey)).toBe(rows);
    });
});

describe("live header state", () => {
    it("block height and tx/s from network events, tx/s only after 20 s", () => {
        const { live } = setup();
        const net = (height: number, txs: number): WsEvent<"network"> => ({
            ch: "network", ts, d: { height, blocks: 2, txs, receipts: 10 },
        });
        live.handle(net(100, 20));
        vi.advanceTimersByTime(FLUSH_MS);
        expect(live.getState().height).toBe(100);
        expect(live.getState().tps).toBeNull();
        for (let i = 1; i <= 10; i++) {
            vi.advanceTimersByTime(2000 - FLUSH_MS);
            live.handle(net(100 + i * 2, 20));
            vi.advanceTimersByTime(FLUSH_MS);
        }
        expect(live.getState().height).toBe(120);
        expect(live.getState().tps).toBeCloseTo(10, 5); // 20 txs every 2 s
        live.handle(net(119, 999)); // older height: ignored
        vi.advanceTimersByTime(FLUSH_MS);
        expect(live.getState().height).toBe(120);
    });
});

describe("reducers", () => {
    it("moves: threshold, kind, dedupe by tx+from+to+amount", () => {
        const e = { kind: "transfer", token: "near", symbol: null, amount: "1", usd: 15_000, from: "a", to: "b", ts, tx: "T" };
        let rows: Move[] | undefined = [];
        rows = applyMove(rows, e, { minUsd: 10_000 });
        rows = applyMove(rows, e, { minUsd: 10_000 });
        expect(rows).toHaveLength(1);
        expect(applyMove([], { ...e, usd: 5_000 }, { minUsd: 10_000 })).toEqual([]);
        expect(applyMove([], e, { minUsd: 0, kind: "swap" })).toEqual([]);
        expect(moveKey(e)).toBe("T|a|b|1");
        // With the event's id (= /moves _id): a REST row and the socket event are the same move.
        const rest = [{ ...e, _id: "R:t0" }];
        expect(applyMove(rest, { ...e, id: "R:t0", tx: "other" }, { minUsd: 0 })).toBe(rest);
        expect(applyMove([], { ...e, id: "R:t0" }, { minUsd: 0 })![0]._id).toBe("R:t0");
    });

    it("new: ft refines a contract already shown instead of adding a row", () => {
        const rows: NewItem[] = [{ _id: "contract:t.near", kind: "contract", id: "t.near", by: "x", tx: null, first_seen: ts }];
        const next = applyNew(rows, { kind: "ft", id: "t.near" }, ts, {})!;
        expect(next).toHaveLength(1);
        expect(next[0].token_kind).toBe("ft");
        expect(applyNew([], { kind: "pool", id: "p" }, ts, { kind: "contract" })).toEqual([]);
    });

    it("candles: a new bucket opens at the previous close; late events ignored", () => {
        const data: Candles = {
            decimals: 0,
            candles: [{ t: "2026-11-09T15:00:00Z", o: 1, h: 1, l: 1, c: 1, v: 1, v_usd: 1, buys: 1, sells: 0 }],
        };
        const next = applyCandle(data, tradeEv("k", { px: 3 }).d, "2026-11-09T15:01:10Z", "1m")!;
        expect(next.candles[1]).toMatchObject({ t: "2026-11-09T15:01:00Z", o: 1, h: 3, l: 1, c: 3 });
        expect(applyCandle(next, tradeEv("k2", { px: 9 }).d, "2026-11-09T15:00:59Z", "1m")).toBe(next);
        expect(bucketStart("2026-11-09T15:59:59.9Z", "1h")).toBe("2026-11-09T15:00:00Z");
    });
});
