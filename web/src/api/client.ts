import { observeServerDate } from "@/lib/clock";
import type {
    CandleInterval, Candles, Creator, Health, HoldersInfo, Labels, Launch, Mover, Move, MoveKind, Network, NewItem,
    NewKind, Period, SearchResult, SmartTrade, SmartWallet, TokenInfo, TokenMeta, TopSort, TopToken, Trade,
} from "./types";

export const API_BASE = ((import.meta.env.VITE_DASH_API as string | undefined) || "https://api.sendler.xyz/v2/dash")
    .replace(/\/$/, "");

export class ApiError extends Error {
    readonly status: number;
    readonly code: string;
    constructor(status: number, code: string) {
        super(`${status} ${code}`);
        this.status = status;
        this.code = code;
    }
}

/**
 * The API limits 300 requests a minute per IP — an office or a mobile carrier can share one IP.
 * After a 429 every request waits out a 60 s pause instead of retrying in a loop.
 */
const PAUSE_429_MS = 60_000;
let pausedUntil = 0;

export function rateLimitedFor(now = Date.now()): number {
    return Math.max(0, pausedUntil - now);
}

type Query = Record<string, string | number | boolean | null | undefined | string[]>;

function qs(params: Query | undefined): string {
    if (!params) return "";
    const sp = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) {
        if (v === null || v === undefined || v === "") continue;
        sp.set(k, Array.isArray(v) ? v.join(",") : String(v));
    }
    const s = sp.toString();
    return s ? `?${s}` : "";
}

export async function apiGet<T>(path: string, params?: Query, signal?: AbortSignal): Promise<T> {
    const wait = rateLimitedFor();
    if (wait > 0) throw new ApiError(429, "rate_limited");
    const res = await fetch(`${API_BASE}${path}${qs(params)}`, { signal, headers: { Accept: "application/json" } });
    observeServerDate(res.headers.get("Date"));
    if (res.status === 429) {
        pausedUntil = Date.now() + PAUSE_429_MS;
        throw new ApiError(429, "rate_limited");
    }
    let body: unknown = null;
    try {
        body = await res.json();
    } catch {
        /* non-JSON error page from the proxy */
    }
    if (!res.ok) {
        const detail = (body as { detail?: unknown } | null)?.detail;
        throw new ApiError(res.status, typeof detail === "string" ? detail : `http_${res.status}`);
    }
    return (body as { data: T }).data;
}

const enc = encodeURIComponent;

/** One function per endpoint; parameters as the API names them. */
export const api = {
    health: (s?: AbortSignal) => apiGet<Health>("/health", undefined, s),

    launches: (
        p: { pad?: string[]; hide_fee_farm?: boolean; hide_blocked?: boolean; limit?: number; before?: string },
        s?: AbortSignal,
    ) =>
        apiGet<Launch[]>("/launches", p, s),

    movers: (p: { period: Extract<Period, "10m" | "1h" | "24h">; limit?: number }, s?: AbortSignal) =>
        apiGet<Mover[]>("/launches/movers", p, s),

    tokensTop: (p: { period: Extract<Period, "1h" | "24h" | "7d">; sort: TopSort; limit?: number }, s?: AbortSignal) =>
        apiGet<TopToken[]>("/tokens/top", p, s),

    token: (id: string, s?: AbortSignal) => apiGet<TokenInfo>(`/tokens/${enc(id)}`, undefined, s),

    candles: (id: string, p: { i: CandleInterval; limit?: number; from?: number; to?: number }, s?: AbortSignal) =>
        apiGet<Candles>(`/tokens/${enc(id)}/candles`, p, s),

    trades: (id: string, p: { limit?: number; before?: string }, s?: AbortSignal) =>
        apiGet<Trade[]>(`/tokens/${enc(id)}/trades`, p, s),

    /** Nearly / JustHoot launches only; limit ≤ 100. */
    holders: (id: string, limit: number, s?: AbortSignal) =>
        apiGet<HoldersInfo>(`/tokens/${enc(id)}/holders`, { limit }, s),

    creator: (account: string, s?: AbortSignal) => apiGet<Creator>(`/creators/${enc(account)}`, undefined, s),

    smartTop: (p: { limit?: number }, s?: AbortSignal) => apiGet<SmartWallet[]>("/smart/top", p, s),

    smartFeed: (p: { limit?: number; before?: string }, s?: AbortSignal) => apiGet<SmartTrade[]>("/smart/feed", p, s),

    moves: (
        p: { min_usd?: number; kind?: MoveKind; token_id?: string; limit?: number; before?: string },
        s?: AbortSignal,
    ) =>
        apiGet<Move[]>("/moves", p, s),

    network: (p: { period: Extract<Period, "1h" | "24h" | "7d"> }, s?: AbortSignal) =>
        apiGet<Network>("/network", p, s),

    newItems: (p: { kind?: NewKind; limit?: number; before?: string }, s?: AbortSignal) => apiGet<NewItem[]>("/new", p, s),

    /** Up to 100 accounts per call. */
    labels: (accounts: string[], s?: AbortSignal) => apiGet<Labels>("/labels", { accounts }, s),

    /** Up to 100 tokens per call; unknown ones come back as null. */
    tokensMeta: (ids: string[], s?: AbortSignal) =>
        apiGet<Record<string, TokenMeta | null>>("/tokens/meta", { ids }, s),

    /** From 2 characters: tokens by ticker / address, accounts with label and smart rank. */
    search: (q: string, limit = 10, s?: AbortSignal) => apiGet<SearchResult>("/search", { q, limit }, s),
};
