import { useQuery } from "@tanstack/react-query";
import { api } from "./client";
import type { Label, TokenMeta } from "./types";

/**
 * Collects single-id requests made in the same tick (a widget renders 50 rows at once) into
 * one call of up to `max` ids. Each id still gets its own react-query entry, so the cache,
 * staleTime and dedupe work per token / per account.
 */
export class Batcher<T> {
    private queue = new Map<string, { resolve: (v: T | null) => void; reject: (e: unknown) => void }[]>();
    private timer: ReturnType<typeof setTimeout> | null = null;
    private readonly fetchMany: (ids: string[]) => Promise<Record<string, T | null>>;
    private readonly max: number;
    private readonly waitMs: number;

    constructor(fetchMany: (ids: string[]) => Promise<Record<string, T | null>>, max = 100, waitMs = 20) {
        this.fetchMany = fetchMany;
        this.max = max;
        this.waitMs = waitMs;
    }

    load(id: string): Promise<T | null> {
        return new Promise((resolve, reject) => {
            const waiting = this.queue.get(id);
            if (waiting) waiting.push({ resolve, reject });
            else this.queue.set(id, [{ resolve, reject }]);
            if (!this.timer) this.timer = setTimeout(() => this.flush(), this.waitMs);
        });
    }

    private flush() {
        this.timer = null;
        const all = [...this.queue.entries()];
        this.queue.clear();
        for (let i = 0; i < all.length; i += this.max) {
            const chunk = all.slice(i, i + this.max);
            this.fetchMany(chunk.map(([id]) => id)).then(
                (res) => {
                    for (const [id, waiters] of chunk) for (const w of waiters) w.resolve(res[id] ?? null);
                },
                (err) => {
                    for (const [, waiters] of chunk) for (const w of waiters) w.reject(err);
                },
            );
        }
    }
}

const metaBatcher = new Batcher<TokenMeta>((ids) => api.tokensMeta(ids));
const labelBatcher = new Batcher<Label>((ids) => api.labels(ids));

const META_STALE = 300_000;
const LABEL_STALE = 300_000;
/**
 * A fresh launch is on screen before the indexer has its ft_metadata: no logo, no decimals (so no
 * live price). Ask again every 15 s while that lasts, up to ~5 min — not 5 min of a blank logo.
 */
const META_MISS_RETRY_MS = 15_000;
const META_MISS_TRIES = 20;

/** Symbol, decimals, icon and bot code of a token — batched with every other row on screen. */
export function useTokenMeta(token: string | null | undefined) {
    return useQuery({
        queryKey: ["meta", token],
        queryFn: () => metaBatcher.load(token!),
        enabled: !!token,
        staleTime: META_STALE,
        gcTime: 30 * 60_000,
        refetchInterval: (q) => {
            const d = q.state.data;
            const missing = !d || d.decimals === null || d.decimals === undefined;
            return missing && q.state.dataUpdateCount < META_MISS_TRIES ? META_MISS_RETRY_MS : false;
        },
    });
}

/** Account label for rows the socket delivered without one (moves). */
export function useAccountLabel(account: string | null | undefined, known?: Label | null) {
    return useQuery({
        queryKey: ["label", account],
        queryFn: () => labelBatcher.load(account!),
        enabled: !!account && known === undefined,
        staleTime: LABEL_STALE,
        gcTime: 30 * 60_000,
    });
}
