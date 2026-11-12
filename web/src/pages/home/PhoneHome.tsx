import * as React from "react";
import { useTranslation } from "react-i18next";
import { ChevronDown, ChevronUp, Settings2 } from "lucide-react";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { PHONE_LAYOUT_ID, PHONE_TABS, setPhoneTab, setTabPrefs, usePhoneTab, usePhoneTabs, useTabPrefs } from "@/layouts/phone";
import { serverNow } from "@/lib/clock";
import { cn } from "@/lib/utils";
import { PhoneFrame } from "@/ui/Widget";
import { Placed } from "@/ui/WidgetGrid";
import { REGISTRY } from "@/widgets/registry";
import { useWidgetSettings } from "@/widgets/settings";
import type { LayoutItem, WidgetDef } from "@/widgets/types";

/** Scroll quiet this long = the swipe has landed on a panel. */
const SETTLE_MS = 90;
/** Holding a tab this long opens «Настроить вкладки». */
const LONG_PRESS_MS = 500;

const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/**
 * Phone home (plan step 12): a row of tabs and one widget per screen, switched by a tap on the
 * tab or by a swipe. The pager is a native horizontal scroll-snap — the browser does the finger
 * tracking and the axis lock, so a widget's own vertical list scrolls as before. The page itself
 * does not scroll (Shell gives the home page a fixed-height column on the phone).
 *
 * Only the open panel and its neighbours are mounted: a swipe shows a live neighbour, the other
 * widgets cost nothing — less work than the old column of seven.
 */
export function PhoneHome() {
    const { t } = useTranslation();
    const tabs = usePhoneTabs();
    const stored = usePhoneTab();
    const active = Math.max(0, tabs.findIndex((x) => x.id === stored));
    const pager = React.useRef<HTMLDivElement>(null);
    const bar = React.useRef<HTMLDivElement>(null);
    /** The panel under the finger while swiping (the tab highlight follows it at once). */
    const [shown, setShown] = React.useState(active);
    const [height, setHeight] = React.useState<number>();
    const [editing, setEditing] = React.useState(false);
    const activeRef = React.useRef(active);
    activeRef.current = active;
    const settle = React.useRef<ReturnType<typeof setTimeout> | null>(null);
    const baseId = React.useId();

    // When each tab was last looked at (server clock, like the rows' times). The page load counts
    // as a look: badges show what came after it, not the history.
    const [loadedAt] = React.useState(serverNow);
    const [seen, setSeen] = React.useState<Record<string, number>>({});
    const left = React.useRef(tabs[active].id);
    React.useEffect(() => {
        const prev = left.current;
        const id = tabs[active].id;
        left.current = id;
        const now = serverNow();
        setSeen((s) => ({ ...s, [prev]: now, [id]: now }));
    }, [active, tabs]);

    // Panel height = the pager's; on resize (rotation, the browser's bars) stay on the open panel.
    React.useLayoutEffect(() => {
        const el = pager.current;
        if (!el) return;
        const fit = () => {
            setHeight(el.clientHeight);
            el.scrollLeft = activeRef.current * el.clientWidth;
        };
        fit();
        const ro = new ResizeObserver(fit);
        ro.observe(el);
        return () => ro.disconnect();
    }, []);

    // Tabs reordered or hidden: the open tab keeps its place under the finger.
    React.useLayoutEffect(() => {
        const el = pager.current;
        if (el) el.scrollLeft = active * el.clientWidth;
        setShown(active);
        if (tabs[active].id !== stored) setPhoneTab(tabs[active].id);
    }, [tabs]); // eslint-disable-line react-hooks/exhaustive-deps -- only on a new tab list

    React.useEffect(() => () => {
        if (settle.current) clearTimeout(settle.current);
    }, []);

    // Keep the highlighted tab in view, centred.
    React.useEffect(() => {
        const row = bar.current;
        const chip = row?.children[shown] as HTMLElement | undefined;
        if (!row || !chip) return;
        row.scrollTo({
            left: chip.offsetLeft - (row.clientWidth - chip.offsetWidth) / 2,
            behavior: reducedMotion() ? "auto" : "smooth",
        });
    }, [shown, tabs]);

    // The row's edges fade only where more tabs are hidden: a hint to scroll, never over the last tab.
    const [edges, setEdges] = React.useState({ left: false, right: false });
    const measureEdges = React.useCallback(() => {
        const row = bar.current;
        if (!row) return;
        const left = row.scrollLeft > 1;
        const right = row.scrollLeft + row.clientWidth < row.scrollWidth - 1;
        setEdges((e) => (e.left === left && e.right === right ? e : { left, right }));
    }, []);
    React.useLayoutEffect(() => {
        const row = bar.current;
        if (!row) return;
        measureEdges();
        const ro = new ResizeObserver(measureEdges);
        ro.observe(row);
        return () => ro.disconnect();
    }, [measureEdges, tabs]);
    const fade = `linear-gradient(90deg, ${edges.left ? "transparent, #000 24px" : "#000, #000"}, ${edges.right ? "#000 calc(100% - 40px), transparent" : "#000"})`;

    const onScroll = () => {
        const el = pager.current;
        if (!el || !el.clientWidth) return;
        const i = Math.min(tabs.length - 1, Math.max(0, Math.round(el.scrollLeft / el.clientWidth)));
        setShown(i);
        if (settle.current) clearTimeout(settle.current);
        settle.current = setTimeout(() => {
            settle.current = null;
            setPhoneTab(tabs[i].id);
        }, SETTLE_MS);
    };

    const open = (i: number) => {
        const el = pager.current;
        if (!el) return;
        // A far jump is instant: a smooth scroll would mount every widget on the way.
        const smooth = Math.abs(i - shown) === 1 && !reducedMotion();
        setShown(i);
        setPhoneTab(tabs[i].id);
        el.scrollTo({ left: i * el.clientWidth, behavior: smooth ? "smooth" : "auto" });
    };

    const onKeyDown = (e: React.KeyboardEvent) => {
        const dir = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
        if (!dir || !(e.target as HTMLElement).matches("[role=tab]")) return;
        e.preventDefault();
        const i = (shown + dir + tabs.length) % tabs.length;
        open(i);
        (bar.current?.children[i] as HTMLElement | undefined)?.focus();
    };

    const press = useLongPress(() => setEditing(true));

    return (
        <div className="flex min-h-0 flex-1 flex-col">
            <div className="flex shrink-0 items-center border-b border-surface-2">
                <div
                    ref={bar}
                    role="tablist"
                    aria-label={t("tabs.label")}
                    onKeyDown={onKeyDown}
                    onScroll={measureEdges}
                    style={{ maskImage: fade, WebkitMaskImage: fade }}
                    className="flex min-w-0 flex-1 overflow-x-auto px-1 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
                >
                    {tabs.map((item, i) => {
                        const on = i === shown;
                        return (
                            <button
                                key={item.id}
                                id={`${baseId}-tab-${item.id}`}
                                type="button"
                                role="tab"
                                aria-selected={i === active}
                                aria-controls={`${baseId}-panel-${item.id}`}
                                tabIndex={i === active ? 0 : -1}
                                {...press.handlers}
                                onClick={() => {
                                    if (!press.consumed()) open(i);
                                }}
                                className={cn(
                                    // Underline variant: text tabs, the open one marked by a brand bar under its label.
                                    "relative inline-flex h-11 shrink-0 items-center gap-1.5 px-3 text-[14px] font-semibold whitespace-nowrap transition-colors select-none [-webkit-touch-callout:none]",
                                    "after:absolute after:inset-x-3 after:bottom-0 after:h-0.5 after:rounded-full after:bg-brand after:transition-opacity",
                                    on ? "text-fg after:opacity-100" : "text-fg-3 after:opacity-0 hover:text-fg-2",
                                )}
                            >
                                {t(`tabs.${item.type}`)}
                                {i !== shown && i !== active && <Badge item={item} since={seen[item.id] ?? loadedAt} />}
                            </button>
                        );
                    })}
                </div>
                <button
                    type="button"
                    onClick={() => setEditing(true)}
                    className="grid size-11 shrink-0 place-items-center text-fg-3 hover:text-fg"
                    aria-haspopup="dialog"
                >
                    <Settings2 className="size-[18px]" aria-hidden="true" />
                    <span className="sr-only">{t("tabs.edit")}</span>
                </button>
            </div>
            <div
                ref={pager}
                onScroll={onScroll}
                className="flex min-h-0 flex-1 snap-x snap-mandatory overflow-x-auto overflow-y-hidden overscroll-x-contain [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
            >
                {tabs.map((item, i) => {
                    const live = i === active || Math.abs(i - shown) <= 1;
                    return (
                        <div
                            key={item.id}
                            id={`${baseId}-panel-${item.id}`}
                            role="tabpanel"
                            aria-labelledby={`${baseId}-tab-${item.id}`}
                            // Off-screen panels: no focus, no screen reader.
                            inert={i !== active}
                            className="h-full w-full shrink-0 snap-start snap-always"
                        >
                            {live && height !== undefined && (
                                <PhoneFrame value={true}>
                                    <Placed layoutId={PHONE_LAYOUT_ID} item={item} height={height} />
                                </PhoneFrame>
                            )}
                        </div>
                    );
                })}
            </div>
            <TabsSheet open={editing} onOpenChange={setEditing} />
        </div>
    );
}

/** A hold on a tab: the menu instead of the tap that follows it; a scroll of the row cancels it. */
function useLongPress(onLong: () => void) {
    const timer = React.useRef<ReturnType<typeof setTimeout> | null>(null);
    const fired = React.useRef(false);
    const cancel = () => {
        if (timer.current) clearTimeout(timer.current);
        timer.current = null;
    };
    React.useEffect(() => cancel, []);
    return {
        handlers: {
            onPointerDown: (e: React.PointerEvent) => {
                if (e.pointerType === "mouse") return;
                fired.current = false;
                cancel();
                timer.current = setTimeout(() => {
                    fired.current = true;
                    onLong();
                }, LONG_PRESS_MS);
            },
            onPointerUp: cancel,
            onPointerLeave: cancel,
            onPointerCancel: cancel,
            // Android opens the context menu on a hold.
            onContextMenu: (e: React.MouseEvent) => e.preventDefault(),
        },
        /** True once after a hold: the click it ends with is swallowed. */
        consumed: () => {
            const was = fired.current;
            fired.current = false;
            return was;
        },
    };
}

/** «Настроить вкладки» (plan step 12.5): order with arrows, show / hide, back to default. */
function TabsSheet({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
    const { t } = useTranslation();
    const prefs = useTabPrefs();
    const byId = new Map(PHONE_TABS.map((x) => [x.id, x]));
    const visible = prefs.order.filter((id) => !prefs.hidden.includes(id)).length;
    const move = (i: number, dir: -1 | 1) => {
        const order = [...prefs.order];
        [order[i], order[i + dir]] = [order[i + dir], order[i]];
        setTabPrefs({ ...prefs, order });
    };
    const toggle = (id: string, show: boolean) =>
        setTabPrefs({ ...prefs, hidden: show ? prefs.hidden.filter((x) => x !== id) : [...prefs.hidden, id] });
    const arrow = "grid size-11 shrink-0 place-items-center rounded-lg text-fg-2 hover:bg-surface-2 hover:text-fg disabled:opacity-30 disabled:hover:bg-transparent";
    return (
        <Sheet open={open} onOpenChange={onOpenChange}>
            <SheetContent side="bottom" className="max-h-[85dvh] pb-[var(--safe-bottom)]">
                <SheetHeader>
                    <SheetTitle>{t("tabs.edit")}</SheetTitle>
                    <SheetDescription>{t("tabs.editHint")}</SheetDescription>
                </SheetHeader>
                <ul className="flex flex-col overflow-y-auto px-4">
                    {prefs.order.map((id, i) => {
                        const name = t(`tabs.${byId.get(id)!.type}`);
                        const shown = !prefs.hidden.includes(id);
                        return (
                            <li key={id} className="flex min-h-12 items-center gap-1 border-b border-row-line">
                                <label className="flex min-h-11 flex-1 cursor-pointer items-center gap-3 text-[14px] font-medium">
                                    <input
                                        type="checkbox"
                                        checked={shown}
                                        disabled={shown && visible === 1}
                                        onChange={(e) => toggle(id, e.target.checked)}
                                        className="size-5 accent-[var(--brand)]"
                                    />
                                    <span className={shown ? undefined : "text-fg-3"}>{name}</span>
                                </label>
                                <button type="button" className={arrow} disabled={i === 0} onClick={() => move(i, -1)} aria-label={t("tabs.up", { name })}>
                                    <ChevronUp className="size-5" aria-hidden="true" />
                                </button>
                                <button type="button" className={arrow} disabled={i === prefs.order.length - 1} onClick={() => move(i, 1)} aria-label={t("tabs.down", { name })}>
                                    <ChevronDown className="size-5" aria-hidden="true" />
                                </button>
                            </li>
                        );
                    })}
                </ul>
                <div className="flex justify-end px-4 py-3">
                    <button type="button" onClick={() => setTabPrefs(null)} className="h-11 rounded-[10px] bg-surface-2 px-4 text-[14px] font-semibold text-fg hover:bg-line">
                        {t("tabs.reset")}
                    </button>
                </div>
            </SheetContent>
        </Sheet>
    );
}

/** «N новых» on a tab the reader is not on (plan step 12.4): rows newer than the last look. */
function Badge({ item, since }: { item: LayoutItem; since: number }) {
    const def = REGISTRY[item.type];
    if (!def.usePulse) return null;
    return <PulseBadge def={def} item={item} since={since} />;
}

function PulseBadge({ def, item, since }: { def: WidgetDef; item: LayoutItem; since: number }) {
    const { t } = useTranslation();
    // The widget's own settings (shared with it live): the badge counts what the tab would show.
    const [settings] = useWidgetSettings(`${PHONE_LAYOUT_ID}:${item.id}`, { ...def.defaults, ...item.settings });
    const usePulse = def.usePulse!;
    const n = usePulse(settings).filter((at) => at > since).length;
    if (!n) return null;
    return (
        <span className="grid h-4 min-w-4 place-items-center rounded-full bg-brand-soft px-[5px] text-[10.5px] font-bold text-brand-soft-fg tabular-nums">
            <span aria-hidden="true">{n > 9 ? "9+" : n}</span>
            <span className="sr-only">{t("tabs.newCount", { count: n })}</span>
        </span>
    );
}
