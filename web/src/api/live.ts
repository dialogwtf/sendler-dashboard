import { notifyManager, type Query, type QueryClient } from "@tanstack/react-query";
import {
    applyCandle, applyLaunch, applyLaunchTrade, applyMove, applyNetwork, applyNew, applySmartTrade, applyTokenPrice,
    applyTrade,
} from "./reducers";
import type { DashSocket, SocketStatus } from "./ws";
import { baseOf } from "./ws";
import type {
    CandleInterval, Candles, EventMap, Launch, Move, Network, NewItem, SmartTrade, SmartWallet, TokenInfo, TokenMeta,
    Trade, WsEvent,
} from "./types";

/**
 * Socket events → react-query caches (setQueryData), so widgets don't care where data came from.
 *
 * - Events are queued and applied in one batch every 250 ms (peaks: 10+ launches and hundreds
 *   of trades a minute).
 * - The last 60 s of events stay buffered. When any REST snapshot lands (mount, reconnect sync,
 *   polling), the buffer is re-applied to it with dedupe — no gap between snapshot and stream.
 * - After a (re)connect is acknowledged, every query with a live channel is refetched.
 */

export const FLUSH_MS = 250;
const BUFFER_MS = 60_000;
const BUFFER_MAX = 3000;
const LAUNCH_REFETCH_MS = 2_000;
/**
 * A trade the launch row already took in full (price, growth, cap) still refetches the list,
 * less often: the server's numbers (main pool price, creator facts, stage) are the truth.
 */
const LAUNCH_TRADE_REFETCH_MS = 10_000;
const SEEN_TRADES_MAX = 5000;

type AnyEvent = WsEvent;
type Key = readonly unknown[];

/** Live header facts: block height (from `network`) and the socket status. */
export interface LiveState {
    status: SocketStatus;
    height: number | null;
    heightAt: number;
    /** When the connection was lost, ms; 0 while live. */
    lostAt: number;
    /** Transactions per second over the last minute of `network` events; null until 20 s are seen. */
    tps: number | null;
}

const TPS_WINDOW_MS = 60_000;
const TPS_MIN_MS = 20_000;

export class LiveSync {
    private queue: AnyEvent[] = [];
    private buffer: { at: number; e: AnyEvent }[] = [];
    private flushTimer: ReturnType<typeof setTimeout> | null = null;
    private readonly seenEvents = new WeakSet<object>();
    private readonly seenTrades = new Set<string>();
    private lastHeight = 0;
    private launchRefetch: ReturnType<typeof setTimeout> | null = null;
    private launchRefetchDue = 0;
    /** (arrival time, txs) of recent network slices, for the live tx/s. */
    private txSlices: { at: number; txs: number }[] = [];
    private state: LiveState;
    private readonly stateListeners = new Set<() => void>();
    private readonly unsubs: (() => void)[] = [];
    /** When each query's latest fetch started — a snapshot is only gap-free if it began after the sub. */
    private readonly fetchStartedAt = new Map<string, number>();

    private readonly qc: QueryClient;
    private readonly now: () => number;

    constructor(qc: QueryClient, socket: DashSocket, now: () => number = Date.now) {
        this.qc = qc;
        this.now = now;
        this.state = { status: socket.status, height: null, heightAt: 0, lostAt: 0, tps: null };
        this.unsubs.push(
            socket.onStatus((status) => this.setState({ status, lostAt: socket.lostAt })),
            socket.onSynced(() => this.resync()),
            qc.getQueryCache().subscribe((ev) => {
                if (ev.type === "removed") {
                    this.fetchStartedAt.delete(ev.query.queryHash);
                    return;
                }
                if (ev.type !== "updated") return;
                if (ev.action.type === "fetch") this.fetchStartedAt.set(ev.query.queryHash, this.now());
                if (ev.action.type === "success" && !ev.action.manual) this.replayInto(ev.query);
            }),
        );
    }

    dispose() {
        for (const u of this.unsubs) u();
        if (this.flushTimer) clearTimeout(this.flushTimer);
        if (this.launchRefetch) clearTimeout(this.launchRefetch);
    }

    // --- external store for React (useSyncExternalStore) ------------------------------------

    getState = () => this.state;

    subscribeState = (fn: () => void) => {
        this.stateListeners.add(fn);
        return () => this.stateListeners.delete(fn);
    };

    private setState(patch: Partial<LiveState>) {
        this.state = { ...this.state, ...patch };
        for (const fn of this.stateListeners) fn();
    }

    // --- events -----------------------------------------------------------------------------

    /** The one listener every widget channel subscribes with. */
    readonly handle = (e: AnyEvent) => {
        // The same event reaches us once per matching channel ("trade" and "trade:<id>").
        if (this.seenEvents.has(e)) return;
        this.seenEvents.add(e);
        this.queue.push(e);
        const at = this.now();
        this.buffer.push({ at, e });
        if (this.buffer.length > BUFFER_MAX || at - this.buffer[0].at > BUFFER_MS) {
            this.buffer = this.buffer.filter((b) => at - b.at <= BUFFER_MS).slice(-BUFFER_MAX);
        }
        if (!this.flushTimer) this.flushTimer = setTimeout(this.flush, FLUSH_MS);
    };

    readonly flush = () => {
        this.flushTimer = null;
        const batch = this.queue;
        this.queue = [];
        if (!batch.length) return;
        notifyManager.batch(() => {
            for (const e of batch) this.apply(e);
        });
    };

    private apply(e: AnyEvent) {
        switch (baseOf(e.ch)) {
            case "launch":
                return this.forQueries(["launches"], (q) => this.applyTo(q, e, false));
            case "trade": {
                const d = e.d as EventMap["trade"];
                if (this.seenTrades.has(d.key)) return;
                this.seenTrades.add(d.key);
                if (this.seenTrades.size > SEEN_TRADES_MAX) {
                    const first = this.seenTrades.values().next().value;
                    if (first !== undefined) this.seenTrades.delete(first);
                }
                this.forQueries(["token", d.token], (q) => this.applyTo(q, e, false));
                this.forQueries(["smart", "feed"], (q) => this.applyTo(q, e, false));
                this.forQueries(["launches"], (q) => this.applyTo(q, e, false));
                return;
            }
            case "move":
                return this.forQueries(["moves"], (q) => this.applyTo(q, e, false));
            case "new":
                return this.forQueries(["new"], (q) => this.applyTo(q, e, false));
            case "network": {
                const d = e.d as EventMap["network"];
                this.forQueries(["network", "1h"], (q) => this.applyTo(q, e, false));
                if (d.height > this.lastHeight) {
                    const at = this.now();
                    this.lastHeight = d.height;
                    this.txSlices.push({ at, txs: d.txs });
                    this.txSlices = this.txSlices.filter((x) => at - x.at <= TPS_WINDOW_MS);
                    const span = at - this.txSlices[0].at;
                    // The first slice counts the ~2 s before it arrived, so the window is span + 2 s.
                    const tps = span >= TPS_MIN_MS
                        ? this.txSlices.slice(1).reduce((sum, x) => sum + x.txs, 0) / (span / 1000)
                        : null;
                    this.setState({ height: d.height, heightAt: at, tps });
                }
                return;
            }
        }
    }

    /**
     * Applies one event to one cached query. `replay` = re-applying the buffer to a fresh REST
     * snapshot: only idempotent updates (no candle/network increments, no new launch stubs).
     */
    private applyTo(q: Query, e: AnyEvent, replay: boolean) {
        const key = q.queryKey as Key;
        const set = <T>(fn: (old: T | undefined) => T | undefined) => this.qc.setQueryData<T>(key, fn);
        const base = baseOf(e.ch);

        if (base === "launch" && key[0] === "launches") {
            const filter = key[1] as { pad: string[] };
            let refetch = false;
            set<Launch[]>((old) => {
                const r = applyLaunch(old, e.d as EventMap["launch"], replay ? { pad: ["\u0000"] } : filter);
                refetch = r.refetch;
                return r.rows;
            });
            if (refetch && !replay) this.scheduleLaunchRefetch(LAUNCH_REFETCH_MS);
            return;
        }

        if (base === "trade") {
            const d = e.d as EventMap["trade"];
            if (key[0] === "launches") {
                // Counters are increments: a fresh snapshot already has the buffered trades.
                if (replay) return;
                let hit = false;
                let complete = true;
                set<Launch[]>((old) => {
                    const r = applyLaunchTrade(old, d, e.ts, this.decimalsOf(d.token));
                    hit = r.hit;
                    complete = r.complete;
                    return r.rows;
                });
                if (hit) this.scheduleLaunchRefetch(complete ? LAUNCH_TRADE_REFETCH_MS : LAUNCH_REFETCH_MS);
                return;
            }
            if (key[0] === "token" && key[1] === d.token) {
                if (key.length === 2) return set<TokenInfo>((old) => applyTokenPrice(old, d, e.ts));
                if (key[2] === "trades") return set<Trade[]>((old) => applyTrade(old, d, e.ts));
                if (key[2] === "candles" && !replay) {
                    return set<Candles>((old) => applyCandle(old, d, e.ts, key[3] as CandleInterval));
                }
                return;
            }
            if (key[0] === "smart" && key[1] === "feed") {
                const top = this.qc.getQueriesData<SmartWallet[]>({ queryKey: ["smart", "top"] })
                    .map(([, v]) => v).find(Boolean);
                return set<SmartTrade[]>((old) => applySmartTrade(old, d, e.ts, top, this.symbolOf(d.token)));
            }
            return;
        }

        if (base === "move" && key[0] === "moves") {
            const filter = key[1] as { minUsd: number; kind?: string; token?: string };
            return set<Move[]>((old) => applyMove(old, e.d as EventMap["move"], filter));
        }

        if (base === "new" && key[0] === "new") {
            const filter = key[1] as { kind?: string };
            return set<NewItem[]>((old) => applyNew(old, e.d as EventMap["new"], e.ts, filter));
        }

        if (base === "network" && key[0] === "network" && key[1] === "1h" && !replay) {
            const d = e.d as EventMap["network"];
            return set<Network>((old) => applyNetwork(old, d, e.ts, this.lastHeight));
        }
    }

    private forQueries(prefix: Key, fn: (q: Query) => void) {
        for (const q of this.qc.getQueryCache().findAll({ queryKey: prefix })) {
            if (q.state.data !== undefined) fn(q);
        }
    }

    /** Ticker for a smart-feed row built from a socket event: from any cache that knows it. */
    private symbolOf(token: string): string | null {
        const info = this.qc.getQueryData<TokenInfo>(["token", token]);
        if (info?.symbol) return info.symbol;
        for (const [, rows] of this.qc.getQueriesData<Launch[]>({ queryKey: ["launches"] })) {
            const hit = rows?.find((r) => r.token === token);
            if (hit?.symbol) return hit.symbol;
        }
        for (const [, rows] of this.qc.getQueriesData<SmartTrade[]>({ queryKey: ["smart", "feed"] })) {
            const hit = rows?.find((r) => r.token === token);
            if (hit?.symbol) return hit.symbol;
        }
        return null;
    }

    /** Decimals of a token from any cache that knows them (row meta, token page). */
    private decimalsOf(token: string): number | null {
        const meta = this.qc.getQueryData<TokenMeta | null>(["meta", token]);
        if (meta && meta.decimals !== null) return meta.decimals;
        return this.qc.getQueryData<TokenInfo>(["token", token])?.decimals ?? null;
    }

    private replayInto(q: Query) {
        if (!this.buffer.length) return;
        const k0 = (q.queryKey as Key)[0];
        if (!["launches", "token", "smart", "moves", "new"].includes(k0 as string)) return;
        notifyManager.batch(() => {
            for (const { e } of this.buffer) this.applyTo(q, e, true);
        });
    }

    /** One refetch of /launches within `ms`; a sooner request moves a later one up, never back. */
    private scheduleLaunchRefetch(ms: number) {
        const due = this.now() + ms;
        if (this.launchRefetch) {
            if (this.launchRefetchDue <= due) return;
            clearTimeout(this.launchRefetch);
        }
        this.launchRefetchDue = due;
        this.launchRefetch = setTimeout(() => {
            this.launchRefetch = null;
            this.launchRefetchDue = 0;
            void this.qc.invalidateQueries({ queryKey: ["launches"] });
        }, ms);
    }

    /**
     * After a (re)connect is acknowledged: fresh snapshots for everything a channel feeds whose
     * last fetch began before the subscription (an in-flight one is cancelled and restarted).
     */
    private resync() {
        const syncAt = this.now();
        this.txSlices = [];
        this.setState({ lostAt: 0, tps: null });
        const stale = (q: Query) => (this.fetchStartedAt.get(q.queryHash) ?? 0) < syncAt;
        for (const prefix of [["launches"], ["token"], ["smart", "feed"], ["moves"], ["new"], ["network"]]) {
            void this.qc.invalidateQueries({ queryKey: prefix, predicate: stale });
        }
    }
}
