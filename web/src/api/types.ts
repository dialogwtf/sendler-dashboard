/**
 * Response types of https://api.sendler.xyz/v2/dash, written against live responses
 * (OpenAPI tag «Sendler Dash» has no response schemas). Every endpoint answers {"data": …},
 * errors {"detail": "<code>"}. Times are ISO UTC strings unless noted.
 *
 * Strings from the chain (symbol, name, links, accounts) are untrusted: render as text only.
 * Unknown enum values (a new pad, stage, kind) must render as they are, never crash a widget —
 * hence `| (string & {})` on the open sets.
 */

type Open<T extends string> = T | (string & {});

export type Pad = Open<"nearly" | "justhoot" | "intear">;
export type Stage = Open<"started" | "funding" | "live" | "ready">;
export type Side = "buy" | "sell";
export type Venue = Open<"Rhea DCL" | "Rhea" | "Intear">;
export type LabelKind = Open<
    | "dex" | "aggregator" | "bridge" | "launchpad" | "token" | "contract" | "hub" | "exchange" | "team"
    | "wallet" | "implicit"
>;

/**
 * Account label: `label` is a proper name only (Rhea DCL, Omni Bridge, Binance, Nearly) or null;
 * what the account is comes from `kind`, worded by the site's own dictionaries.
 * (Until the 2026-11-09 indexer deploy old rows may still carry Russian descriptions.)
 */
export interface Label {
    label: string | null;
    kind: LabelKind;
}

export interface CreatorFacts {
    launches: number;
    dumped: number;
    blocked_by: number;
    linked: boolean;
    /** In the sniper's shared auto-block; /launches?hide_blocked=true hides them. */
    shared_block?: boolean;
}

/** A row of /launches (also `launch` in /tokens/{id} and `recent` in /creators/{account}). */
export interface Launch {
    token: string;
    pad: Pad;
    symbol: string | null;
    name: string | null;
    creator: string | null;
    creator_facts: CreatorFacts | null;
    stage: Stage;
    started: string | null;
    ready: string | null;
    /** Raw NEAR yocto as a string, "0" = no dev buy; null = unknown (backfilled launches). */
    dev_buy: string | null;
    tax: number | null;
    /**
     * Launch «on the bot tax» (no dev buy, all or nearly all tax to the creator). A flag; the
     * caption is built from dev_buy and tax in the site's language. Older API builds sent a
     * Russian explanation string instead of `true` — read it with `isFeeFarm`.
     */
    fee_farm: boolean | string | null;
    pool: string | null;
    /** Price per whole token in NEAR. */
    price_near: number | null;
    /** Growth from the first trade's price; x_max — the best moment. */
    x: number | null;
    x_max: number | null;
    /** Only Nearly and JustHoot (supply 1e9); null for Intear. */
    cap_near: number | null;
    cap_usd: number | null;
    trades: number;
    volume_near: number;
    last_trade: string | null;
    links: Record<string, string> | string[] | null;
    /** Restored from history: dev buy, tax and fee_farm are unknown («—», not 0). */
    backfill: boolean;
}

export interface Mover extends Launch {
    x_period: number | null;
    volume_period_near: number;
    volume_period_usd: number | null;
    buys_period: number;
    sells_period: number;
    /** Candle closes over the period, NEAR per token — for the sparkline. */
    spark?: number[];
}

export interface TopToken {
    token: string;
    symbol: string | null;
    decimals?: number | null;
    price_near: number | null;
    /** Ratio, 1.0 = unchanged. */
    growth: number | null;
    volume_near: number;
    volume_usd: number | null;
    trades: number;
    /** Unique wallets over the period. */
    traders?: number;
}

export interface TokenInfo {
    token: string;
    symbol: string | null;
    name: string | null;
    decimals: number | null;
    /** data:image/* from ft_metadata — <img src> only, never inline SVG. */
    icon: string | null;
    price_near: number | null;
    price_usd: number | null;
    last_trade: string | null;
    /** Fraction, 0.12 = +12%. */
    change_24h: number | null;
    volume_24h_near: number;
    volume_24h_usd: number;
    trades_24h: number;
    traders_24h: number;
    launch: Launch | null;
    smart_24h: { buy: number; sell: number; wallets: number };
    /** Older API builds don't send it; null — no smart wallet bought the token with NEAR. */
    smart_pos?: SmartPosition | null;
}

/**
 * Where the top-200 smart wallets stand in a token (swaps NEAR ↔ token, last 45 days; a pair not
 * with NEAR, e.g. aiNEAR, is not counted yet).
 */
export interface SmartPosition {
    /** Bought the token for ≥ 0.1 NEAR; `holding` — of them still hold more than 2% of it. */
    wallets: number;
    holding: number;
    /** Share of the bought tokens not sold, 0..1. */
    held: number;
    invested_near: number;
    /** Volume-weighted entry, NEAR per whole token; x — current price / entry. */
    entry_near: number | null;
    x: number | null;
}

/**
 * /tokens/{id}/holders: balances of a Nearly / JustHoot launch token, counted by the indexer
 * from its NEP-141 events. Shares are 0..1 of the supply.
 */
export type HolderTag = Open<"pool" | "contract" | "exchange" | "hub" | "creator" | "linked" | "smart">;

export interface Holder {
    account: string;
    /** Raw units as a string. */
    bal: string;
    /** Whole tokens (already divided by decimals). */
    amount: number;
    share: number;
    label: Label | null;
    tags: HolderTag[];
}

export interface HoldersReady {
    available: true;
    holders: number;
    /** The mint was seen live: the count is exact from the start. */
    complete: boolean;
    /** Reconciled with the FastNear top 100 only: `holders` is a lower bound («≥ N»). */
    top_only: boolean;
    updated_at: string | null;
    supply: string | null;
    decimals: number | null;
    creator: string | null;
    /** The 10 largest wallets (no pools / contracts). */
    top10_share: number | null;
    dev_share: number | null;
    /** Linked to the creator by the sniper's wallet graph. */
    linked_share: number | null;
    /** Pools, launchpads, the token contract. */
    contracts_share: number | null;
    /** Holder count per hour, 30 days. */
    series: { t: string; n: number }[];
    rows: Holder[];
}

export interface HoldersUnavailable {
    available: false;
    /** not_tracked — not a Nearly / JustHoot launch; pending — still being counted. */
    reason: Open<"not_tracked" | "pending">;
}

export type HoldersInfo = HoldersReady | HoldersUnavailable;

export interface Candle {
    t: string;
    /** NEAR per RAW unit; per token = × 10^decimals. */
    o: number;
    h: number;
    l: number;
    c: number;
    v: number;
    v_usd: number | null;
    buys: number;
    sells: number;
    traders?: number;
}

export interface Candles {
    decimals: number | null;
    candles: Candle[];
}

export type TradeTag = Open<"smart" | "creator">;

/** A row of /tokens/{id}/trades and /smart/feed. */
export interface Trade {
    _id: string;
    block: number;
    ts: string;
    token: string;
    side: Side;
    near: number;
    usd: number | null;
    /** Raw u128 string. */
    tok: string;
    /** NEAR per raw unit. */
    px: number;
    pool: string;
    receipt: string;
    tx: string;
    venue: Venue;
    wallet: string;
    signer: string;
    wallet_label?: Label | null;
    tags?: TradeTag[];
    decimals?: number | null;
}

export interface SmartRef {
    _id: string;
    rank: number;
    roi: number;
    pnl: number;
}

export interface SmartTrade extends Trade {
    smart: SmartRef;
    symbol: string | null;
}

export interface SmartWallet {
    /** The wallet. */
    _id: string;
    at: string;
    rank: number;
    pnl: number;
    roi: number;
    win_rate: number;
    tokens: number;
    invested: number;
    wins: number;
}

export type MoveKind = Open<"transfer" | "swap">;

export interface Move {
    _id?: string;
    kind: MoveKind;
    token: string;
    symbol: string | null;
    /** Raw u128 string. */
    amount: string;
    decimals?: number | null;
    usd: number;
    from: string;
    to: string;
    signer?: string;
    from_label?: Label | null;
    to_label?: Label | null;
    ts: string;
    tx: string;
    side?: Side;
    near?: number;
    venue?: Venue;
}

export interface NetworkPoint {
    t: string;
    i?: string;
    blocks: number;
    txs: number;
    receipts: number;
    tgas: number;
    burnt_near: number;
    new_accounts: number;
    signers: number;
}

export interface Network {
    /** 1m for period=1h, otherwise 1h. */
    interval: "1m" | "1h";
    series: NetworkPoint[];
    today: NetworkPoint | null;
    tps: number | null;
    near_usd?: number | null;
}

export type NewKind = Open<"ft" | "nft" | "pool" | "contract">;

export interface NewItem {
    _id: string;
    kind: NewKind;
    id: string;
    by: string | null;
    tx: string | null;
    first_seen: string;
    token_kind?: "ft" | "nft" | null;
    venue?: Venue;
    tokens?: string[];
    pool_kind?: string;
}

export interface CreatorHistory {
    token: string;
    pad: Pad;
    /** ISO UTC (older API builds: unix seconds — Age / secondsSince accept both). */
    started: string | number;
    dumped: boolean;
    first_sell_s: number | null;
    sold_share: number | null;
    sold_near: number | null;
}

export interface CreatorLink {
    root?: string;
    peer?: string;
    kind?: string;
    hop?: number;
    near?: number;
    /** ISO UTC (older builds: unix seconds). */
    at?: string | number;
    [k: string]: unknown;
}

export interface Creator {
    account: string;
    /** Code for bot links (ww_<code>). */
    bot_code?: string;
    launches: number;
    dumped: number;
    history: CreatorHistory[];
    recent: Launch[];
    links: CreatorLink[];
    blocked_by: number;
    blocked_reasons: string[];
}

export interface Health {
    height: number;
    behind_s: number;
    /** NEAR price in $. */
    near_usd: number | null;
    ws_clients: number;
}

/** /tokens/meta?ids=… — per token, null when unknown. Icon: <img src> only. */
export interface TokenMeta {
    symbol: string | null;
    name: string | null;
    decimals: number | null;
    icon: string | null;
    bot_code: string;
}

export interface SearchToken {
    token: string;
    symbol: string | null;
    name: string | null;
    decimals: number | null;
    volume_24h_near: number;
    bot_code: string;
}

export interface SearchAccount {
    account: string;
    label: Label | null;
    launches: number;
    smart: { rank: number; roi: number; pnl: number } | null;
    bot_code: string;
}

export interface SearchResult {
    tokens: SearchToken[];
    accounts: SearchAccount[];
}

export type Labels = Record<string, Label | null>;

export type Period = "10m" | "1h" | "24h" | "7d";
export type CandleInterval = "1m" | "1h" | "1d";
export type TopSort = "volume" | "growth" | "trades";

// --- WebSocket ---------------------------------------------------------------------------------

export interface LaunchEvent {
    token: string;
    pad: Pad;
    stage: Stage;
    creator: string | null;
    symbol: string | null;
    at: string;
}

export interface TradeEvent {
    token: string;
    side: Side;
    near: number;
    usd: number | null;
    px: number;
    wallet: string;
    venue: Venue;
    /** = _id of the trade in REST: dedupe key. */
    key: string;
}

/** The /moves row without labels; `id` = that row's _id (dedupe key). */
export type MoveEvent = Omit<Move, "_id" | "from_label" | "to_label"> & { id?: string };

export interface NewEvent {
    kind: NewKind;
    id: string;
    by?: string | null;
}

export interface NetworkEvent {
    height: number;
    blocks: number;
    txs: number;
    receipts: number;
}

export interface EventMap {
    launch: LaunchEvent;
    trade: TradeEvent;
    move: MoveEvent;
    new: NewEvent;
    network: NetworkEvent;
}

export type BaseChannel = keyof EventMap;

export interface WsEvent<C extends BaseChannel = BaseChannel> {
    /** Channel as subscribed: "trade" or "trade:<token>". */
    ch: string;
    ts: string;
    d: EventMap[C];
}

/** fee_farm as a flag, for both the current (boolean) and the older (text) API. */
export function isFeeFarm(v: Launch["fee_farm"]): boolean {
    return v === true || (typeof v === "string" && v.length > 0);
}
