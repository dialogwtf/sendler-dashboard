/**
 * Pure functions: a WebSocket event + the cached REST data → new cached data. Every reducer is
 * idempotent (dedupe by key), so buffered events can be re-applied to a fresh REST snapshot.
 * Widgets never know whether an update came from the socket or from REST.
 */
import { MAX_ROWS } from "./queries";
import type {
    Candle, Candles, Launch, LaunchEvent, Move, MoveEvent, Network, NetworkEvent, NewEvent, NewItem,
    SmartTrade, SmartWallet, TokenInfo, Trade, TradeEvent,
} from "./types";

const cap = <T>(rows: T[]) => (rows.length > MAX_ROWS ? rows.slice(0, MAX_ROWS) : rows);

// --- launches ----------------------------------------------------------------------------------

/** Placeholder row for a launch the socket announced before REST has it. */
export interface StubLaunch extends Launch {
    _stub: true;
}

export function stubLaunch(e: LaunchEvent): StubLaunch {
    return {
        _stub: true,
        token: e.token, pad: e.pad, symbol: e.symbol, name: null, creator: e.creator, creator_facts: null,
        stage: e.stage, started: e.at, ready: null, dev_buy: null, tax: null, fee_farm: null, pool: null,
        price_near: null, x: null, x_max: null, cap_near: null, cap_usd: null, trades: 0, volume_near: 0,
        last_trade: null, links: null, backfill: false,
    };
}

/**
 * New token → stub row on top (if its pad passes the filter); known token → stage updated.
 * Returns `refetch: true` when the caller should refetch /launches (throttled): a stub has no
 * numbers at all, and a new stage (trading, ready) usually comes with a price the row lacks.
 */
export function applyLaunch(
    rows: Launch[] | undefined,
    e: LaunchEvent,
    filter: { pad: string[] },
): { rows: Launch[] | undefined; refetch: boolean } {
    if (!rows) return { rows, refetch: false };
    const i = rows.findIndex((r) => r.token === e.token);
    if (i >= 0) {
        if (rows[i].stage === e.stage) return { rows, refetch: false };
        const next = rows.slice();
        next[i] = { ...rows[i], stage: e.stage };
        return { rows: next, refetch: true };
    }
    if (filter.pad.length && !filter.pad.includes(e.pad)) return { rows, refetch: false };
    return { rows: cap([stubLaunch(e), ...rows]), refetch: true };
}

/**
 * A trade in a launch the list shows: price, growth, cap, trades and volume of its row.
 * Growth and cap scale with the price, so they need the row's price from REST and the token's
 * decimals (px is NEAR per raw unit); without them only the counters move and `complete` is
 * false — the caller refetches soon. Not idempotent: the caller dedupes trades by key.
 */
export function applyLaunchTrade(
    rows: Launch[] | undefined,
    e: TradeEvent,
    ts: string,
    decimals: number | null | undefined,
): { rows: Launch[] | undefined; hit: boolean; complete: boolean } {
    if (!rows) return { rows, hit: false, complete: true };
    const i = rows.findIndex((r) => r.token === e.token);
    if (i < 0) return { rows, hit: false, complete: true };
    const row = rows[i];
    if (row.last_trade && Date.parse(row.last_trade) > Date.parse(ts)) return { rows, hit: false, complete: true };
    const next: Launch = { ...row, trades: row.trades + 1, volume_near: row.volume_near + e.near, last_trade: ts };
    const price = decimals !== null && decimals !== undefined && e.px > 0 ? e.px * 10 ** decimals : null;
    const old = row.price_near;
    const complete = price !== null && old !== null && old > 0;
    if (complete) {
        const k = price / old;
        next.price_near = price;
        if (row.x !== null) {
            next.x = row.x * k;
            next.x_max = Math.max(row.x_max ?? next.x, next.x);
        }
        if (row.cap_near !== null) next.cap_near = row.cap_near * k;
        if (row.cap_usd !== null) next.cap_usd = row.cap_usd * k;
    }
    const out = rows.slice();
    out[i] = next;
    return { rows: out, hit: true, complete };
}

// --- trades ------------------------------------------------------------------------------------

/** A trade built from the socket: REST fields the event lacks are empty. */
export function tradeFromEvent(e: TradeEvent, ts: string): Trade {
    const receipt = e.key.split(":")[0] ?? "";
    return {
        _id: e.key, block: 0, ts, token: e.token, side: e.side, near: e.near, usd: e.usd, tok: "",
        px: e.px, pool: "", receipt, tx: "", venue: e.venue, wallet: e.wallet, signer: "",
    };
}

export function applyTrade(rows: Trade[] | undefined, e: TradeEvent, ts: string): Trade[] | undefined {
    if (!rows || rows.some((r) => r._id === e.key)) return rows;
    return cap([tradeFromEvent(e, ts), ...rows]);
}

/** Smart-money feed: only wallets of the top-200; the row carries their rank. */
export function applySmartTrade(
    rows: SmartTrade[] | undefined,
    e: TradeEvent,
    ts: string,
    top: SmartWallet[] | undefined,
    symbol: string | null,
): SmartTrade[] | undefined {
    if (!rows || !top) return rows;
    const w = top.find((x) => x._id === e.wallet);
    if (!w || rows.some((r) => r._id === e.key)) return rows;
    const row: SmartTrade = {
        ...tradeFromEvent(e, ts),
        smart: { _id: w._id, rank: w.rank, roi: w.roi, pnl: w.pnl },
        symbol,
    };
    return cap([row, ...rows]);
}

const INTERVAL_MS = { "1m": 60_000, "1h": 3_600_000, "1d": 86_400_000 } as const;

/** Bucket start of a time for a candle interval (UTC), ISO like the API ("…:00Z"). */
export function bucketStart(ts: string, i: keyof typeof INTERVAL_MS): string {
    const ms = Date.parse(ts);
    const start = Math.floor(ms / INTERVAL_MS[i]) * INTERVAL_MS[i];
    return new Date(start).toISOString().replace(".000Z", "Z");
}

/**
 * The current candle from a trade: update h/l/c/v of the last bucket or open a new one.
 * Not idempotent by itself — the caller dedupes trades by key before calling.
 */
export function applyCandle(data: Candles | undefined, e: TradeEvent, ts: string, i: keyof typeof INTERVAL_MS): Candles | undefined {
    if (!data) return data;
    const t = bucketStart(ts, i);
    const candles = data.candles;
    const last = candles[candles.length - 1];
    if (last && Date.parse(last.t) > Date.parse(t)) return data; // late event for a closed bucket
    const buy = e.side === "buy";
    if (last && last.t === t) {
        const next: Candle = {
            ...last,
            h: Math.max(last.h, e.px),
            l: Math.min(last.l, e.px),
            c: e.px,
            v: last.v + e.near,
            v_usd: last.v_usd === null || e.usd === null ? last.v_usd : last.v_usd + e.usd,
            buys: last.buys + (buy ? 1 : 0),
            sells: last.sells + (buy ? 0 : 1),
        };
        return { ...data, candles: [...candles.slice(0, -1), next] };
    }
    const open = last ? last.c : e.px;
    const fresh: Candle = {
        t, o: open, h: Math.max(open, e.px), l: Math.min(open, e.px), c: e.px, v: e.near, v_usd: e.usd,
        buys: buy ? 1 : 0, sells: buy ? 0 : 1,
    };
    return { ...data, candles: [...candles, fresh] };
}

/** Token header: price and last trade from a trade (price per token = px × 10^decimals). */
export function applyTokenPrice(info: TokenInfo | undefined, e: TradeEvent, ts: string): TokenInfo | undefined {
    if (!info || info.decimals === null) return info;
    if (info.last_trade && Date.parse(info.last_trade) > Date.parse(ts)) return info;
    return { ...info, price_near: e.px * 10 ** info.decimals, last_trade: ts };
}

// --- moves -------------------------------------------------------------------------------------

/** Dedupe key of a move: the row's _id (the event's `id`); old events without one — tx+from+to+amount. */
export const moveKey = (m: Pick<Move, "tx" | "from" | "to" | "amount"> & { _id?: string; id?: string }) =>
    m._id ?? m.id ?? `${m.tx}|${m.from}|${m.to}|${m.amount}`;

export function applyMove(
    rows: Move[] | undefined,
    e: MoveEvent,
    filter: { minUsd: number; kind?: string; token?: string },
): Move[] | undefined {
    if (!rows) return rows;
    if (e.usd < filter.minUsd) return rows;
    if (filter.kind && e.kind !== filter.kind) return rows;
    if (filter.token && e.token !== filter.token) return rows;
    const k = moveKey(e);
    if (rows.some((r) => moveKey(r) === k)) return rows;
    const { id, ...rest } = e;
    const row: Move = { ...rest, ...(id ? { _id: id } : {}), symbol: e.symbol ?? null };
    return cap([row, ...rows]);
}

// --- new contracts and pools -------------------------------------------------------------------

export function applyNew(rows: NewItem[] | undefined, e: NewEvent, ts: string, filter: { kind?: string }): NewItem[] | undefined {
    if (!rows) return rows;
    // ft / nft refines the type of a contract already shown.
    const tokenKind = e.kind === "ft" || e.kind === "nft" ? (e.kind as "ft" | "nft") : null;
    if (tokenKind) {
        const i = rows.findIndex((r) => r.kind === "contract" && r.id === e.id);
        if (i >= 0) {
            if (rows[i].token_kind === tokenKind) return rows;
            const next = rows.slice();
            next[i] = { ...rows[i], token_kind: tokenKind };
            return next;
        }
        // The /new API lists ft/nft as contracts with token_kind; a filter on ft/nft shows them too.
        if (filter.kind !== e.kind) return rows;
    } else if (filter.kind && filter.kind !== e.kind) {
        return rows;
    }
    const kind = tokenKind ? "contract" : e.kind;
    const _id = `${kind}:${e.id}`;
    if (rows.some((r) => r._id === _id)) return rows;
    const row: NewItem = {
        _id, kind, id: e.id, by: e.by ?? null, tx: null, first_seen: ts,
        ...(tokenKind ? { token_kind: tokenKind } : {}),
    };
    return cap([row, ...rows]);
}

// --- network -----------------------------------------------------------------------------------

/**
 * Adds a ~2 s slice to the current 1-minute point of the 1h series and to `today`.
 * Dedupe by height: `lastHeight` is the height already counted.
 */
export function applyNetwork(data: Network | undefined, e: NetworkEvent, ts: string, lastHeight: number): Network | undefined {
    if (!data || e.height <= lastHeight || data.interval !== "1m") return data;
    const t = bucketStart(ts, "1m");
    const series = data.series;
    const last = series[series.length - 1];
    let nextSeries = series;
    if (last && last.t === t) {
        nextSeries = [...series.slice(0, -1), {
            ...last, blocks: last.blocks + e.blocks, txs: last.txs + e.txs, receipts: last.receipts + e.receipts,
        }];
    } else if (!last || Date.parse(last.t) < Date.parse(t)) {
        nextSeries = [...series.slice(series.length >= 60 ? 1 : 0), {
            t, blocks: e.blocks, txs: e.txs, receipts: e.receipts, tgas: 0, burnt_near: 0, new_accounts: 0, signers: 0,
        }];
    }
    const today = data.today
        ? { ...data.today, blocks: data.today.blocks + e.blocks, txs: data.today.txs + e.txs, receipts: data.today.receipts + e.receipts }
        : data.today;
    return { ...data, series: nextSeries, today };
}
