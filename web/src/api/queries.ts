import { keepPreviousData, queryOptions } from "@tanstack/react-query";
import { api } from "./client";
import type { CandleInterval, MoveKind, NewKind, TopSort } from "./types";

/**
 * Query keys and staleTime per endpoint. staleTime = the endpoint's Cache-Control max-age:
 * asking more often only gets the same cached answer.
 *
 * Live data comes from the WebSocket straight into these caches (ws/apply.ts via setQueryData),
 * so REST is fetched on mount, on reconnect, and polled only by widgets without a channel
 * (movers, top) — or by everyone while the socket is in fallback (see `pollEvery` in live.ts).
 */
export const STALE = {
    launches: 2_000,
    movers: 10_000,
    smartFeed: 5_000,
    smartTop: 60_000,
    moves: 2_000,
    network: 10_000,
    newItems: 10_000,
    top: 30_000,
    token: 5_000,
    candles: 5_000,
    trades: 2_000,
    holders: 30_000,
    creator: 60_000,
    labels: 300_000,
    health: 10_000,
} as const;

/** Lists keep at most this many rows (also the API's limit cap). */
export const MAX_ROWS = 200;

export const qk = {
    launches: (p: { pad: string[]; hideFeeFarm: boolean; hideBlocked: boolean; limit: number }) => ["launches", p] as const,
    movers: (period: "10m" | "1h" | "24h", limit: number) => ["movers", period, limit] as const,
    smartFeed: (limit: number) => ["smart", "feed", limit] as const,
    smartTop: (limit: number) => ["smart", "top", limit] as const,
    moves: (p: { minUsd: number; kind?: MoveKind; token?: string; limit: number }) => ["moves", p] as const,
    network: (period: "1h" | "24h" | "7d") => ["network", period] as const,
    newItems: (p: { kind?: NewKind; limit: number }) => ["new", p] as const,
    top: (period: "1h" | "24h" | "7d", sort: TopSort, limit: number) => ["top", period, sort, limit] as const,
    token: (id: string) => ["token", id] as const,
    candles: (id: string, i: CandleInterval, limit: number) => ["token", id, "candles", i, limit] as const,
    trades: (id: string, limit: number) => ["token", id, "trades", limit] as const,
    holders: (id: string, limit: number) => ["token", id, "holders", limit] as const,
    creator: (account: string) => ["creator", account] as const,
    label: (account: string) => ["label", account] as const,
    health: () => ["health"] as const,
};

export const launchesQuery = (p: { pad: string[]; hideFeeFarm: boolean; hideBlocked?: boolean; limit?: number }) => {
    const limit = p.limit ?? 50;
    const hideBlocked = p.hideBlocked ?? false;
    return queryOptions({
        queryKey: qk.launches({ pad: p.pad, hideFeeFarm: p.hideFeeFarm, hideBlocked, limit }),
        queryFn: ({ signal }) =>
            api.launches({ pad: p.pad, hide_fee_farm: p.hideFeeFarm, hide_blocked: hideBlocked || undefined, limit }, signal),
        staleTime: STALE.launches,
        placeholderData: keepPreviousData,
    });
};

export const moversQuery = (period: "10m" | "1h" | "24h", limit = 20) =>
    queryOptions({
        queryKey: qk.movers(period, limit),
        queryFn: ({ signal }) => api.movers({ period, limit }, signal),
        staleTime: STALE.movers,
        placeholderData: keepPreviousData,
    });

export const smartFeedQuery = (limit = 50) =>
    queryOptions({
        queryKey: qk.smartFeed(limit),
        queryFn: ({ signal }) => api.smartFeed({ limit }, signal),
        staleTime: STALE.smartFeed,
    });

export const smartTopQuery = (limit = 200) =>
    queryOptions({
        queryKey: qk.smartTop(limit),
        queryFn: ({ signal }) => api.smartTop({ limit }, signal),
        staleTime: STALE.smartTop,
    });

export const movesQuery = (p: { minUsd: number; kind?: MoveKind; token?: string; limit?: number }) => {
    const limit = p.limit ?? 50;
    return queryOptions({
        queryKey: qk.moves({ minUsd: p.minUsd, kind: p.kind, token: p.token, limit }),
        queryFn: ({ signal }) => api.moves({ min_usd: p.minUsd, kind: p.kind, token_id: p.token, limit }, signal),
        staleTime: STALE.moves,
        placeholderData: keepPreviousData,
    });
};

export const networkQuery = (period: "1h" | "24h" | "7d") =>
    queryOptions({
        queryKey: qk.network(period),
        queryFn: ({ signal }) => api.network({ period }, signal),
        staleTime: STALE.network,
        placeholderData: keepPreviousData,
    });

export const newItemsQuery = (p: { kind?: NewKind; limit?: number }) => {
    const limit = p.limit ?? 50;
    return queryOptions({
        queryKey: qk.newItems({ kind: p.kind, limit }),
        queryFn: ({ signal }) => api.newItems({ kind: p.kind, limit }, signal),
        staleTime: STALE.newItems,
        placeholderData: keepPreviousData,
    });
};

export const topQuery = (period: "1h" | "24h" | "7d", sort: TopSort, limit = 20) =>
    queryOptions({
        queryKey: qk.top(period, sort, limit),
        queryFn: ({ signal }) => api.tokensTop({ period, sort, limit }, signal),
        staleTime: STALE.top,
        placeholderData: keepPreviousData,
    });

export const tokenQuery = (id: string) =>
    queryOptions({
        queryKey: qk.token(id),
        queryFn: ({ signal }) => api.token(id, signal),
        staleTime: STALE.token,
    });

export const candlesQuery = (id: string, i: CandleInterval, limit = 500) =>
    queryOptions({
        queryKey: qk.candles(id, i, limit),
        queryFn: ({ signal }) => api.candles(id, { i, limit }, signal),
        staleTime: STALE.candles,
        placeholderData: keepPreviousData,
    });

export const tradesQuery = (id: string, limit = 50) =>
    queryOptions({
        queryKey: qk.trades(id, limit),
        queryFn: ({ signal }) => api.trades(id, { limit }, signal),
        staleTime: STALE.trades,
    });

/** One request of the API's maximum (100): the list shows 50 and reveals the rest without a refetch. */
export const holdersQuery = (id: string, limit = 100) =>
    queryOptions({
        queryKey: qk.holders(id, limit),
        queryFn: ({ signal }) => api.holders(id, limit, signal),
        staleTime: STALE.holders,
    });

export const creatorQuery = (account: string) =>
    queryOptions({
        queryKey: qk.creator(account),
        queryFn: ({ signal }) => api.creator(account, signal),
        staleTime: STALE.creator,
    });

export const healthQuery = () =>
    queryOptions({
        queryKey: qk.health(),
        queryFn: ({ signal }) => api.health(signal),
        staleTime: STALE.health,
        refetchInterval: STALE.health,
    });
