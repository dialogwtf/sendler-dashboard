import * as React from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { AlertTriangle, Maximize2 } from "lucide-react";
import { useLiveCtx, useLiveState } from "@/app/live";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { SegmentedChips } from "@/ui/Segmented";

/**
 * The one widget frame (anatomy from the «Основы» board):
 *   header 48 px — title · freshness · 1–2 frequent filters · «Настройки» · «Развернуть»
 *   body        — rows 44–60 px or a chart; the widget only renders rows
 *   footer      — «Все … →» link to the full list, or a hint about the filter
 *
 * States are drawn here, not in widgets («Состояния» board): loading = row skeleton at the same
 * height; empty = reason + action (a button to drop the filter when the filter emptied it);
 * offline = old data dimmed, its age in the header, a «Переподключаемся…» strip with «Повторить»;
 * new event = the row slides in and glows 3 s (LiveList).
 *
 * The body is a CSS container (`@container`): rows choose their S / M / L fields by their own
 * width, never the window's.
 */

export type WidgetState = "loading" | "ready" | "empty" | "error";

export interface EmptyInfo {
    title: React.ReactNode;
    hint?: React.ReactNode;
    /** Shown when the filter emptied the list: drops the filter. */
    action?: { label: React.ReactNode; onClick: () => void };
}

const NewRowsCtx = React.createContext<((n: number, toTop: () => void) => void) | null>(null);

/**
 * A widget that is a whole phone screen (the home tabs, plan step 12): no card frame, no footer —
 * the footer's «Все … →» becomes the header's «Развернуть», always shown.
 */
const PhoneFrameCtx = React.createContext(false);
export const PhoneFrame = PhoneFrameCtx.Provider;
/** The widget is a phone tab (PhoneHome): some widgets drop controls the tabs already give. */
export const usePhoneFrame = () => React.useContext(PhoneFrameCtx);

/** LiveList reports «N new rows above» through this; the header shows the pill. */
export function useReportNewRows() {
    return React.useContext(NewRowsCtx);
}

export function Widget({
    title,
    freshness,
    filters,
    settings,
    expandTo,
    footer,
    state,
    empty,
    error,
    onRetry,
    live = true,
    skeletonRows = 5,
    height,
    className,
    bodyClassName,
    children,
    labelledBy,
    forceOffline,
}: {
    title: React.ReactNode;
    freshness?: React.ReactNode;
    filters?: React.ReactNode;
    /** The settings button + popover (SettingsButton), drawn from the widget's schema. */
    settings?: React.ReactNode;
    /** Full-list route for «Развернуть». */
    expandTo?: string;
    footer?: { to: string; label: React.ReactNode } | { hint: React.ReactNode } | { node: React.ReactNode };
    state: WidgetState;
    empty?: EmptyInfo;
    error?: unknown;
    onRetry?: () => void;
    /** The widget is fed by the socket: shows the reconnect strip when it is down. */
    live?: boolean;
    skeletonRows?: number;
    /** Total widget height, px (multiple of 8): rows of a layout line up. Rows scroll inside. */
    height?: number;
    className?: string;
    bodyClassName?: string;
    children?: React.ReactNode;
    labelledBy?: string;
    /** Showcase only (/dev/ui): draw the offline state without dropping the socket. */
    forceOffline?: boolean;
}) {
    const { t } = useTranslation();
    const { status } = useLiveState();
    const offline = forceOffline || (live && (status === "reconnecting" || status === "fallback"));
    const [newRows, setNewRows] = React.useState<{ n: number; toTop: () => void } | null>(null);
    const report = React.useCallback((n: number, toTop: () => void) => setNewRows(n > 0 ? { n, toTop } : null), []);
    const titleId = React.useId();
    const phone = React.useContext(PhoneFrameCtx);

    return (
        <section
            aria-labelledby={labelledBy ?? titleId}
            style={height ? { height } : undefined}
            className={cn(
                "@container relative flex min-w-0 flex-col overflow-hidden rounded-[var(--radius-widget)] border border-line bg-surface",
                phone && "rounded-none border-0",
                className,
            )}
        >
            {phone ? (
                <PhoneHeader titleId={titleId} title={title} filters={filters} settings={settings} expandTo={expandTo} />
            ) : (
                <header className="flex h-12 shrink-0 items-center gap-2.5 border-b border-line-soft pr-2 pl-4">
                    <h2 id={titleId} className="truncate text-[14px] font-semibold whitespace-nowrap">
                        {title}
                    </h2>
                    {freshness}
                    <span className="flex-1" />
                    {newRows && (
                        <button
                            type="button"
                            onClick={newRows.toTop}
                            className="h-[30px] shrink-0 rounded-[9px] bg-brand px-3 text-[12px] font-semibold text-brand-fg"
                        >
                            {t("widget.newRows", { count: newRows.n })}
                        </button>
                    )}
                    {filters && <div className="hidden min-w-0 items-center gap-1.5 @min-[400px]:flex">{filters}</div>}
                    {settings}
                    {expandTo && (
                        <Link
                            to={expandTo}
                            className="hidden size-9 shrink-0 place-items-center rounded-lg text-fg-2 hover:bg-surface-2 hover:text-fg sm:grid"
                        >
                            <Maximize2 className="size-4" aria-hidden="true" />
                            <span className="sr-only">{t("widget.expand")}</span>
                        </Link>
                    )}
                </header>
            )}

            {/* Narrow widget (S): the frequent filters move to a row of their own. */}
            {filters && !phone && (
                <div className="flex shrink-0 flex-wrap items-center gap-1.5 border-b border-line-soft px-3 py-2 @min-[400px]:hidden">
                    {filters}
                </div>
            )}

            {offline && state !== "loading" && <OfflineStrip />}

            <div className={cn("@container relative flex min-h-0 flex-1 flex-col overflow-hidden", bodyClassName)}>
                {/* Phone tab: «N new» floats over the list instead of taking room in the header. */}
                {phone && newRows && (
                    <button
                        type="button"
                        onClick={newRows.toTop}
                        className="absolute top-2 left-1/2 z-10 h-8 -translate-x-1/2 rounded-full bg-brand px-3.5 text-[12px] font-semibold text-brand-fg shadow-lg shadow-black/30"
                    >
                        {t("widget.newRows", { count: newRows.n })}
                    </button>
                )}
                {state === "loading" && <SkeletonRows n={skeletonRows} />}
                {state === "empty" && empty && <EmptyBody {...empty} />}
                {state === "error" && <ErrorBody error={error} onRetry={onRetry} />}
                {state === "ready" && (
                    <div className={cn("flex min-h-0 flex-1 flex-col transition-opacity animate-in fade-in duration-300", offline && "opacity-55")}>
                        <NewRowsCtx.Provider value={report}>{children}</NewRowsCtx.Provider>
                    </div>
                )}
            </div>

            {footer && !phone && (
                <footer className="shrink-0 border-t border-line-soft px-4 py-2.5 text-[12px]">
                    {"to" in footer ? (
                        <Link to={footer.to} className="font-semibold text-brand-text hover:underline pointer-coarse:inline-flex pointer-coarse:min-h-11 pointer-coarse:items-center">
                            {footer.label}
                        </Link>
                    ) : "hint" in footer ? (
                        <span className="text-fg-3">{footer.hint}</span>
                    ) : (
                        footer.node
                    )}
                </footer>
            )}
        </section>
    );
}

/**
 * The header of a phone tab: no title (the tab above names it) and no freshness (the app header
 * has the live dot) — one row of the widget's filters as chips, scrolling sideways with a fade
 * at the edge when they don't fit, then settings and «Развернуть». Nothing to show — no row.
 */
function PhoneHeader({
    titleId,
    title,
    filters,
    settings,
    expandTo,
}: {
    titleId: string;
    title: React.ReactNode;
    filters?: React.ReactNode;
    settings?: React.ReactNode;
    expandTo?: string;
}) {
    const { t } = useTranslation();
    const heading = <h2 id={titleId} className="sr-only">{title}</h2>;
    if (!filters && !settings && !expandTo) return heading;
    return (
        <header className="flex h-[52px] shrink-0 items-center border-b border-line-soft pr-1">
            {heading}
            <div className="flex h-full min-w-0 flex-1 items-center overflow-x-auto overflow-y-hidden pr-6 pl-4 [mask-image:linear-gradient(to_right,black_calc(100%-24px),transparent)] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
                {filters && <SegmentedChips value={true}>{filters}</SegmentedChips>}
            </div>
            {settings}
            {expandTo && (
                <Link to={expandTo} className="grid size-11 shrink-0 place-items-center rounded-lg text-fg-2 hover:bg-surface-2 hover:text-fg">
                    <Maximize2 className="size-4" aria-hidden="true" />
                    <span className="sr-only">{t("widget.expand")}</span>
                </Link>
            )}
        </header>
    );
}

function OfflineStrip() {
    const { t } = useTranslation();
    const { socket } = useLiveCtx();
    const { status } = useLiveState();
    return (
        <div
            role="status"
            className="mx-3 mt-2.5 flex items-center gap-2 rounded-lg bg-down-soft px-2.5 py-2 text-[12px] text-down-soft-fg"
        >
            <AlertTriangle className="size-3.5 shrink-0" aria-hidden="true" />
            <span className="flex-1">{status === "fallback" ? t("widget.fallback") : t("widget.reconnecting")}</span>
            {status !== "fallback" && (
                <button type="button" onClick={() => socket.retryNow()} className="font-semibold hover:underline">
                    {t("widget.retry")}
                </button>
            )}
        </div>
    );
}

/** Row skeleton instead of a spinner: two bars per row, the same height as real rows. */
export function SkeletonRows({ n = 5, rowHeight = 56 }: { n?: number; rowHeight?: number }) {
    const widths = [[60, 40], [70, 35], [55, 45], [65, 30], [50, 38], [62, 42]];
    return (
        <div aria-hidden="true" className="flex flex-col">
            {Array.from({ length: n }, (_, i) => (
                <div key={i} className="flex flex-col justify-center gap-2 px-4" style={{ height: rowHeight }}>
                    <div className="skeleton h-2.5" style={{ width: `${widths[i % widths.length][0]}%` }} />
                    <div className="skeleton h-2.5" style={{ width: `${widths[i % widths.length][1]}%` }} />
                </div>
            ))}
        </div>
    );
}

function EmptyBody({ title, hint, action }: EmptyInfo) {
    return (
        <div className="flex flex-1 flex-col items-center justify-center gap-2.5 p-6 text-center">
            <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="var(--empty-icon)" strokeWidth="1.6" aria-hidden="true">
                <path d="M3 12h4l3-7 4 14 3-7h4" />
            </svg>
            <b className="font-semibold">{title}</b>
            {hint && <span className="text-[12px] text-fg-2">{hint}</span>}
            {action && (
                <Button variant="secondary" size="sm" className="pointer-coarse:h-11 pointer-coarse:px-4" onClick={action.onClick}>
                    {action.label}
                </Button>
            )}
        </div>
    );
}

function ErrorBody({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
    const { t } = useTranslation();
    const code = (error as { code?: string } | null)?.code;
    return (
        <div className="flex flex-1 flex-col items-center justify-center gap-2.5 p-6 text-center">
            <AlertTriangle className="size-8 text-fg-3" aria-hidden="true" />
            <b className="font-semibold">{code === "rate_limited" ? t("widget.rateLimited") : t("widget.errorTitle")}</b>
            <span className="text-[12px] text-fg-2">{t("widget.errorHint")}</span>
            {onRetry && (
                <Button variant="secondary" size="sm" className="pointer-coarse:h-11 pointer-coarse:px-4" onClick={onRetry}>
                    {t("widget.retry")}
                </Button>
            )}
        </div>
    );
}

/** Catches a crashing widget so one bad row never blanks the page. */
export class WidgetBoundary extends React.Component<
    { title: React.ReactNode; children: React.ReactNode },
    { error: unknown }
> {
    state = { error: null as unknown };

    static getDerivedStateFromError(error: unknown) {
        return { error };
    }

    componentDidCatch(error: unknown, info: React.ErrorInfo) {
        console.error("widget crashed:", error, info.componentStack);
    }

    render() {
        if (this.state.error !== null) {
            return (
                <Widget title={this.props.title} state="error" error={this.state.error} live={false} onRetry={() => this.setState({ error: null })} />
            );
        }
        return this.props.children;
    }
}
