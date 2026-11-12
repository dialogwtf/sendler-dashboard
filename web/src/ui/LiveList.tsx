import * as React from "react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import { useReportNewRows } from "./Widget";

const GLOW_MS = 3000;
/** Scrolled further than this from the top = «the user is reading below». */
const AT_TOP_PX = 8;
/** Rows rendered before the browser is idle (outside full-list pages). */
const FIRST_ROWS = 20;

/**
 * A scrolling list of live rows («Состояния» board, «Новое событие»):
 * - at the top: a new row slides in from above and glows for 3 s;
 * - scrolled down: the list does not jump (scroll position compensated by hand — Safari has no
 *   scroll anchoring), and the header shows «↑ N новых»; a click returns to the top.
 * The first render never glows.
 *
 * `page` (full-list pages): the list has its natural height and the page scrolls; the same
 * compensation is done on the window, and «↑ N новых» floats over the page (the widget's header
 * is off screen by then).
 */
export function LiveList<T>({
    items,
    getKey,
    renderRow,
    className,
    ariaLive,
    page = false,
    footer,
}: {
    items: readonly T[];
    getKey: (item: T) => string;
    renderRow: (item: T, opts: { isNew: boolean }) => React.ReactNode;
    className?: string;
    /** For the launch feed: polite announcements, no more than once in 10 s (see LiveAnnouncer). */
    ariaLive?: boolean;
    page?: boolean;
    /** After the rows, inside the scroller (the «older rows» control). */
    footer?: React.ReactNode;
}) {
    const { t } = useTranslation();
    const scroller = React.useRef<HTMLDivElement>(null);
    const firstSeen = React.useRef<Map<string, number> | null>(null);
    const prevTopKey = React.useRef<string | null>(null);
    const prevHeight = React.useRef(0);
    const [unseen, setUnseen] = React.useState<Set<string>>(() => new Set());
    const report = useReportNewRows();
    const [, force] = React.useReducer((x: number) => x + 1, 0);

    // First screen (plan step 10): a widget shows ~10 rows of its 50 — render 20 at once, the
    // rest when the browser is idle. Full-list pages render everything (the reader scrolls).
    const [all, setAll] = React.useState(page);
    React.useEffect(() => {
        if (all) return;
        const ric = window.requestIdleCallback;
        if (ric) {
            const id = ric(() => setAll(true), { timeout: 2000 });
            return () => window.cancelIdleCallback(id);
        }
        const id = setTimeout(() => setAll(true), 1200);
        return () => clearTimeout(id);
    }, [all]);
    const shown = all ? items : items.slice(0, FIRST_ROWS);

    // Remember when each key first appeared (render-phase bookkeeping, no state).
    const now = Date.now();
    if (firstSeen.current === null) {
        firstSeen.current = new Map(items.map((it) => [getKey(it), 0])); // initial rows: no glow
    } else {
        for (const it of items) {
            const k = getKey(it);
            if (!firstSeen.current.has(k)) firstSeen.current.set(k, now);
        }
    }

    /** The reader is below the top of the list. */
    const readingBelow = React.useCallback(() => {
        const el = scroller.current;
        if (!el) return false;
        return page ? el.getBoundingClientRect().top < -AT_TOP_PX : el.scrollTop > AT_TOP_PX;
    }, [page]);

    // Keep the reading position when rows are added above, and count them.
    React.useLayoutEffect(() => {
        const el = scroller.current;
        if (!el) return;
        const topKey = items.length ? getKey(items[0]) : null;
        const height = page ? el.offsetHeight : el.scrollHeight;
        if (readingBelow() && prevTopKey.current !== null && topKey !== prevTopKey.current) {
            const added = height - prevHeight.current;
            if (added > 0) {
                if (page) window.scrollBy(0, added);
                else el.scrollTop += added;
            }
            const fresh: string[] = [];
            for (const it of items) {
                const k = getKey(it);
                if (k === prevTopKey.current) break;
                fresh.push(k);
            }
            if (fresh.length) setUnseen((s) => new Set([...s, ...fresh]));
        }
        prevTopKey.current = topKey;
        prevHeight.current = height;
    }, [items, getKey, page, readingBelow]);

    const toTop = React.useCallback(() => {
        const el = scroller.current;
        const behavior: ScrollBehavior = window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth";
        if (page && el) window.scrollTo({ top: Math.max(0, window.scrollY + el.getBoundingClientRect().top - 120), behavior });
        else el?.scrollTo({ top: 0, behavior });
        setUnseen(new Set());
    }, [page]);

    React.useEffect(() => {
        if (!page) report?.(unseen.size, toTop);
    }, [unseen.size, report, toTop, page]);
    React.useEffect(() => () => report?.(0, () => {}), [report]);

    const onScroll = React.useCallback(() => {
        if (!readingBelow()) setUnseen((s) => (s.size ? new Set() : s));
    }, [readingBelow]);

    React.useEffect(() => {
        if (!page) return;
        window.addEventListener("scroll", onScroll, { passive: true });
        return () => window.removeEventListener("scroll", onScroll);
    }, [page, onScroll]);

    // Browser scroll anchoring would shift the page too — on top of the compensation above.
    React.useEffect(() => {
        if (!page) return;
        const root = document.documentElement;
        const prev = root.style.overflowAnchor;
        root.style.overflowAnchor = "none";
        return () => { root.style.overflowAnchor = prev; };
    }, [page]);

    // Drop the glow class after 3 s (one timer per batch of new rows).
    const newest = Math.max(0, ...[...firstSeen.current.values()]);
    React.useEffect(() => {
        const left = newest + GLOW_MS - Date.now();
        if (newest === 0 || left <= 0) return;
        const timer = setTimeout(force, left + 50);
        return () => clearTimeout(timer);
    }, [newest]);

    // Forget keys that left the list so the map stays bounded.
    React.useEffect(() => {
        const keep = new Set(items.map(getKey));
        for (const k of firstSeen.current!.keys()) if (!keep.has(k)) firstSeen.current!.delete(k);
    }, [items, getKey]);

    return (
        <div
            ref={scroller}
            onScroll={page ? undefined : onScroll}
            className={cn(!page && "min-h-0 flex-1 overflow-y-auto overscroll-contain [overflow-anchor:none]", className)}
            aria-live={ariaLive ? "off" : undefined}
        >
            {shown.map((it) => {
                const k = getKey(it);
                const at = firstSeen.current!.get(k) ?? 0;
                const isNew = at > 0 && now - at < GLOW_MS;
                return (
                    <div key={k} className={cn(isNew && "row-new")}>
                        {renderRow(it, { isNew })}
                    </div>
                );
            })}
            {footer}
            {page && unseen.size > 0 && (
                <button
                    type="button"
                    onClick={toTop}
                    className="fixed top-4 left-1/2 z-30 h-9 -translate-x-1/2 rounded-full bg-brand px-4 text-[13px] font-semibold text-brand-fg shadow-lg"
                >
                    {t("widget.newRows", { count: unseen.size })}
                </button>
            )}
        </div>
    );
}
