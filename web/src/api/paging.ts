import * as React from "react";
import { useInfiniteQuery } from "@tanstack/react-query";

/**
 * Older pages of a live list (full-list pages, plan step 7).
 *
 * The head — the newest rows — is the widget's own query: the socket keeps it live. Older rows
 * come from `?before=<ISO time of the last row>` under a key of their own ("older", …), which the
 * socket layer never touches, so they stay put while the head grows.
 *
 * The API filters `ts < before` and rows of one block share a time, so a cut exactly at the last
 * row's time would lose the rest of its block: the cursor goes 1 ms later and the duplicates it
 * brings back are dropped by key. A page that brings nothing new ends the list (no loop).
 */

export const OLDER_PAGE = 100;

/** The `before` for the page after a row with this time: 1 ms later, see above. */
export function cursorAfter(ts: string | number | null | undefined): string | null {
    if (ts === null || ts === undefined || ts === "") return null;
    const ms = typeof ts === "number" ? ts * 1000 : Date.parse(ts);
    return Number.isFinite(ms) ? new Date(ms + 1).toISOString() : null;
}

/** Head rows, then older pages, without repeats; the first occurrence (the freshest copy) wins. */
export function mergeRows<T>(head: readonly T[], pages: readonly (readonly T[])[], keyOf: (r: T) => string): T[] {
    const seen = new Set<string>();
    const out: T[] = [];
    for (const list of [head, ...pages]) {
        for (const r of list) {
            const k = keyOf(r);
            if (seen.has(k)) continue;
            seen.add(k);
            out.push(r);
        }
    }
    return out;
}

/** Whether the page after `pages` is worth asking for. */
export function hasMoreAfter<T>(pages: readonly (readonly T[])[], head: readonly T[], pageSize: number, keyOf: (r: T) => string): boolean {
    const last = pages[pages.length - 1];
    if (!last) return true;
    if (last.length < pageSize) return false;
    // Nothing new on the last page (all of it shared one time with rows we have) — stop.
    const before = new Set(mergeRows(head, pages.slice(0, -1), keyOf).map(keyOf));
    return last.some((r) => !before.has(keyOf(r)));
}

export interface Older<T> {
    rows: T[];
    /** More rows exist (as far as we know). */
    hasMore: boolean;
    loading: boolean;
    error: unknown;
    loadMore: () => void;
}

export function useOlderRows<T>({
    id,
    head,
    headLimit,
    enabled,
    keyOf,
    tsOf,
    fetchPage,
    pageSize = OLDER_PAGE,
}: {
    /** What the list is: kind + filters. A change starts over. */
    id: readonly unknown[];
    /** The live head (undefined while it loads). */
    head: readonly T[] | undefined;
    /** The head's REST limit: a head shorter than that is the whole list. */
    headLimit: number;
    /** Off in widgets on the home grid: they show only the head. */
    enabled: boolean;
    keyOf: (r: T) => string;
    tsOf: (r: T) => string | number | null | undefined;
    fetchPage: (before: string, limit: number, signal: AbortSignal) => Promise<T[]>;
    pageSize?: number;
}): Older<T> {
    const idKey = JSON.stringify(id);
    // The cursor of the first older page is fixed when it is first asked for: the head moves on.
    const [start, setStart] = React.useState<{ id: string; cursor: string } | null>(null);
    const cursor = start && start.id === idKey ? start.cursor : null;
    const q = useInfiniteQuery({
        queryKey: ["older", ...id, cursor],
        enabled: enabled && cursor !== null,
        initialPageParam: cursor ?? "",
        queryFn: ({ pageParam, signal }) => fetchPage(pageParam, pageSize, signal),
        getNextPageParam: (last: T[], all: T[][], param: string) => {
            if (!hasMoreAfter(all, head ?? [], pageSize, keyOf)) return undefined;
            const next = cursorAfter(tsOf(last[last.length - 1]));
            return next && next !== param ? next : undefined;
        },
        staleTime: Infinity,
        gcTime: 5 * 60_000,
        retry: 1,
    });
    const pages = React.useMemo(() => (cursor ? (q.data?.pages ?? []) : []), [cursor, q.data]);
    const rows = React.useMemo(() => mergeRows(head ?? [], pages, keyOf), [head, pages, keyOf]);
    const headFull = (head?.length ?? 0) >= headLimit;
    const hasMore = enabled && (cursor === null ? headFull : q.hasNextPage || q.isPending);
    const loading = cursor !== null && (q.isFetching || q.isPending);

    const loadMore = React.useCallback(() => {
        if (!enabled) return;
        if (cursor === null) {
            const last = head?.[head.length - 1];
            const c = last ? cursorAfter(tsOf(last)) : null;
            if (c && (head?.length ?? 0) >= headLimit) setStart({ id: idKey, cursor: c });
            return;
        }
        if (q.isError) {
            void q.refetch();
            return;
        }
        if (q.hasNextPage && !q.isFetchingNextPage) void q.fetchNextPage();
    }, [enabled, cursor, head, headLimit, idKey, tsOf, q]);

    return { rows, hasMore, loading, error: cursor !== null ? q.error : null, loadMore };
}
