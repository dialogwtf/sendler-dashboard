import type { BaseChannel, EventMap, WsEvent } from "./types";

/**
 * One WebSocket per tab — wss://api.sendler.xyz/v2/dash/ws, no token.
 *
 * - Widgets subscribe to channels ("launch", "trade:<token>"); the manager ref-counts them and
 *   sends only the difference. A token channel is not sent while its base channel is on
 *   (the base already carries it); events are routed to token listeners by `d.token`.
 * - The server keeps no history and never replays. After every (re)connect the manager emits
 *   `synced` once the subscription is acknowledged — the data layer then takes REST snapshots
 *   and re-applies buffered events with dedupe (live.ts), so there is no gap.
 * - `{"op":"ping"}` every 30 s: Cloudflare closes silent sockets after 100 s.
 * - Drop → «Нет связи», reconnect after 1 → 2 → 4 … 30 s with jitter, back to 1 s on success.
 * - Close 4429 (3 sockets from this IP) / 4503 (server full) → no reconnect loop: fallback to
 *   REST polling and try the socket again every 2 minutes.
 * - Tab hidden for more than 60 s → close (frees one of the 3 sockets per IP); visible → reconnect.
 *
 * Replies and events: a reply carries `ok` or `error`, an event carries `ch`, `ts`, `d`.
 * `unknown_channel` names the bad channels in `bad` and rejects the whole `sub` (no `ok` follows):
 * the manager drops them for good and re-sends the rest, so one bad channel never blocks the sync.
 */

export type SocketStatus =
    | "idle"        // nothing subscribed yet / closed by us
    | "connecting"
    | "open"        // connected and subscribed
    | "reconnecting"
    | "fallback"    // 4429 / 4503: REST polling, socket retried every 2 min
    | "hidden";     // closed because the tab is in the background

export const MAX_SUBS = 20;
export const PING_MS = 30_000;
export const BACKOFF_MAX_MS = 30_000;
export const FALLBACK_RETRY_MS = 120_000;
export const HIDDEN_CLOSE_MS = 60_000;
const CLOSE_TOO_MANY = 4429;
const CLOSE_FULL = 4503;

type Listener = (e: WsEvent) => void;

interface SocketLike {
    readyState: number;
    onopen: ((ev: unknown) => void) | null;
    onclose: ((ev: { code: number }) => void) | null;
    onerror: ((ev: unknown) => void) | null;
    onmessage: ((ev: { data: unknown }) => void) | null;
    send(data: string): void;
    close(code?: number): void;
}

export interface SocketDeps {
    url: string;
    createSocket?: (url: string) => SocketLike;
    random?: () => number;
    now?: () => number;
    /** Page visibility source; omitted in tests that don't care. */
    doc?: Pick<Document, "visibilityState" | "addEventListener" | "removeEventListener">;
    log?: (msg: string, extra?: unknown) => void;
}

const OPEN = 1;

export function baseOf(channel: string): string {
    const i = channel.indexOf(":");
    return i < 0 ? channel : channel.slice(0, i);
}

export class DashSocket {
    private readonly deps: Required<Omit<SocketDeps, "doc">> & { doc?: SocketDeps["doc"] };
    private ws: SocketLike | null = null;
    private readonly listeners = new Map<string, Set<Listener>>();
    /** What the server currently has. */
    private serverSubs = new Set<string>();
    private flushQueued = false;
    private attempt = 0;
    private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    private pingTimer: ReturnType<typeof setInterval> | null = null;
    private hiddenTimer: ReturnType<typeof setTimeout> | null = null;
    private awaitingSync = false;
    private stopped = false;
    /** Channels the server refused (unknown_channel) — never asked for again. */
    private readonly rejected = new Set<string>();

    status: SocketStatus = "idle";
    /** Last event or reply from the server, ms (local clock) — for «данные N назад». */
    lastMessageAt = 0;
    /** When the connection was lost (for the age shown in the header), 0 while connected. */
    lostAt = 0;

    private readonly statusListeners = new Set<(s: SocketStatus) => void>();
    private readonly syncListeners = new Set<() => void>();

    constructor(deps: SocketDeps) {
        this.deps = {
            createSocket: (url) => new WebSocket(url) as unknown as SocketLike,
            random: Math.random,
            now: Date.now,
            log: () => {},
            ...deps,
        };
        this.deps.doc?.addEventListener("visibilitychange", this.onVisibility);
    }

    // --- public API ------------------------------------------------------------------------

    /** Listen to a channel ("trade" or "trade:<token>"). Returns the unsubscribe function. */
    subscribe<C extends BaseChannel>(channel: C | `${C}:${string}`, fn: (e: WsEvent<C>) => void): () => void {
        let set = this.listeners.get(channel);
        if (!set) {
            set = new Set();
            this.listeners.set(channel, set);
        }
        set.add(fn as Listener);
        this.scheduleFlush();
        this.ensureConnected();
        return () => {
            const s = this.listeners.get(channel);
            if (!s) return;
            s.delete(fn as Listener);
            if (s.size === 0) this.listeners.delete(channel);
            this.scheduleFlush();
        };
    }

    onStatus(fn: (s: SocketStatus) => void): () => void {
        this.statusListeners.add(fn);
        return () => this.statusListeners.delete(fn);
    }

    /** Fired when a (re)connected socket's subscription is acknowledged: take REST snapshots now. */
    onSynced(fn: () => void): () => void {
        this.syncListeners.add(fn);
        return () => this.syncListeners.delete(fn);
    }

    /** «Повторить» button: reconnect now, skipping the backoff wait. */
    retryNow() {
        if (this.status === "open" || this.status === "connecting") return;
        this.attempt = 0;
        this.clearReconnect();
        this.connect();
    }

    stop() {
        this.stopped = true;
        this.clearReconnect();
        this.clearHidden();
        this.deps.doc?.removeEventListener("visibilitychange", this.onVisibility);
        this.teardown();
        this.setStatus("idle");
    }

    /** Channels the server should have, given current listeners (≤ MAX_SUBS). */
    wantedChannels(): string[] {
        const bases = new Set<string>();
        const tokens: string[] = [];
        for (const ch of this.listeners.keys()) {
            if (this.rejected.has(ch)) continue;
            if (ch.includes(":")) tokens.push(ch);
            else bases.add(ch);
        }
        const out = [...bases].sort();
        for (const ch of tokens.sort()) if (!bases.has(baseOf(ch))) out.push(ch);
        if (out.length > MAX_SUBS) {
            this.deps.log("ws: too many channels, extra ones dropped", out.slice(MAX_SUBS));
            return out.slice(0, MAX_SUBS);
        }
        return out;
    }

    // --- connection ------------------------------------------------------------------------

    private ensureConnected() {
        if (this.stopped || this.ws || this.reconnectTimer) return;
        if (this.status === "fallback" || this.status === "hidden") return;
        if (this.deps.doc?.visibilityState === "hidden") return;
        this.connect();
    }

    private connect() {
        if (this.stopped) return;
        this.teardown();
        this.setStatus(this.lostAt ? "reconnecting" : "connecting");
        let ws: SocketLike;
        try {
            ws = this.deps.createSocket(this.deps.url);
        } catch (e) {
            this.deps.log("ws: create failed", e);
            this.scheduleReconnect();
            return;
        }
        this.ws = ws;
        ws.onopen = () => {
            this.serverSubs = new Set();
            this.awaitingSync = true;
            const want = this.wantedChannels();
            if (want.length) {
                this.serverSubs = new Set(want);
                this.send({ op: "sub", ch: want });
            } else {
                this.send({ op: "ping" }); // nothing to subscribe: an ack still marks the sync
            }
            this.pingTimer = setInterval(() => this.send({ op: "ping" }), PING_MS);
        };
        ws.onmessage = (ev) => this.onMessage(ev.data);
        ws.onerror = () => { /* onclose follows */ };
        ws.onclose = (ev) => this.onClose(ev.code);
    }

    private onMessage(raw: unknown) {
        if (typeof raw !== "string") return;
        let msg: unknown;
        try {
            msg = JSON.parse(raw);
        } catch {
            return;
        }
        if (!msg || typeof msg !== "object") return;
        this.lastMessageAt = this.deps.now();
        const m = msg as Record<string, unknown>;
        if ("ok" in m || "error" in m) {
            if ("error" in m) {
                this.deps.log("ws: error reply", m);
                if (m.error === "unknown_channel" && Array.isArray(m.bad)) {
                    for (const ch of m.bad) if (typeof ch === "string") this.rejected.add(ch);
                    // The whole sub was refused: ask again for what is left.
                    const want = this.wantedChannels();
                    this.serverSubs = new Set(want);
                    if (want.length) this.send({ op: "sub", ch: want });
                    else if (this.awaitingSync) this.send({ op: "ping" });
                }
                return;
            }
            if (this.awaitingSync && "ok" in m) {
                this.awaitingSync = false;
                this.attempt = 0;
                this.lostAt = 0;
                this.setStatus("open");
                this.flush(); // listeners that changed while the first sub was in flight
                for (const fn of this.syncListeners) fn();
            }
            return;
        }
        if (typeof m.ch !== "string" || !m.d || typeof m.d !== "object") return;
        this.dispatch(m as unknown as WsEvent);
    }

    private dispatch(e: WsEvent) {
        const base = baseOf(e.ch);
        const token = (e.d as { token?: unknown }).token;
        const targets: string[] = [e.ch];
        // A base-channel event also feeds the listeners of its token channel.
        if (e.ch === base && typeof token === "string") targets.push(`${base}:${token}`);
        for (const ch of targets) {
            const set = this.listeners.get(ch);
            if (!set) continue;
            for (const fn of [...set]) {
                try {
                    fn(e);
                } catch (err) {
                    this.deps.log("ws: listener failed", err);
                }
            }
        }
    }

    private onClose(code: number) {
        this.teardown();
        if (this.stopped || this.status === "hidden") return;
        if (!this.lostAt) this.lostAt = this.deps.now();
        if (code === CLOSE_TOO_MANY || code === CLOSE_FULL) {
            this.attempt = 0;
            this.setStatus("fallback");
            this.clearReconnect();
            this.reconnectTimer = setTimeout(() => {
                this.reconnectTimer = null;
                this.connect();
            }, FALLBACK_RETRY_MS);
            return;
        }
        this.scheduleReconnect();
    }

    /** 1 → 2 → 4 … 30 s, each with ±50% jitter around the step. */
    backoffMs(attempt: number): number {
        const step = Math.min(BACKOFF_MAX_MS, 1000 * 2 ** attempt);
        return Math.round(step * (0.5 + this.deps.random()));
    }

    private scheduleReconnect() {
        this.setStatus("reconnecting");
        this.clearReconnect();
        const delay = Math.min(BACKOFF_MAX_MS, this.backoffMs(this.attempt));
        this.attempt += 1;
        this.reconnectTimer = setTimeout(() => {
            this.reconnectTimer = null;
            this.connect();
        }, delay);
    }

    private teardown() {
        if (this.pingTimer) clearInterval(this.pingTimer);
        this.pingTimer = null;
        const ws = this.ws;
        this.ws = null;
        this.serverSubs = new Set();
        this.awaitingSync = false;
        if (ws) {
            ws.onopen = ws.onmessage = ws.onerror = ws.onclose = null;
            try {
                ws.close(1000);
            } catch {
                /* already closed */
            }
        }
    }

    private clearReconnect() {
        if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
        this.reconnectTimer = null;
    }

    private clearHidden() {
        if (this.hiddenTimer) clearTimeout(this.hiddenTimer);
        this.hiddenTimer = null;
    }

    private readonly onVisibility = () => {
        const doc = this.deps.doc;
        if (!doc || this.stopped) return;
        if (doc.visibilityState === "hidden") {
            this.clearHidden();
            this.hiddenTimer = setTimeout(() => {
                this.hiddenTimer = null;
                this.clearReconnect();
                this.teardown();
                if (!this.lostAt) this.lostAt = this.deps.now();
                this.setStatus("hidden");
            }, HIDDEN_CLOSE_MS);
            return;
        }
        this.clearHidden();
        if (this.status === "hidden") {
            this.attempt = 0;
            this.connect();
        }
    };

    // --- subscription diff -----------------------------------------------------------------

    private scheduleFlush() {
        if (this.flushQueued) return;
        this.flushQueued = true;
        queueMicrotask(() => {
            this.flushQueued = false;
            this.flush();
        });
    }

    private flush() {
        const ws = this.ws;
        if (!ws || ws.readyState !== OPEN || this.awaitingSync) return; // onopen subscribes everything
        const want = new Set(this.wantedChannels());
        const add = [...want].filter((c) => !this.serverSubs.has(c));
        const drop = [...this.serverSubs].filter((c) => !want.has(c));
        if (drop.length) this.send({ op: "unsub", ch: drop });
        if (add.length) this.send({ op: "sub", ch: add });
        this.serverSubs = want;
    }

    private send(msg: { op: "sub" | "unsub"; ch: string[] } | { op: "ping" }) {
        const ws = this.ws;
        if (!ws || ws.readyState !== OPEN) return;
        try {
            ws.send(JSON.stringify(msg));
        } catch (e) {
            this.deps.log("ws: send failed", e);
        }
    }

    private setStatus(s: SocketStatus) {
        if (this.status === s) return;
        this.status = s;
        for (const fn of this.statusListeners) fn(s);
    }
}

export type { EventMap };
