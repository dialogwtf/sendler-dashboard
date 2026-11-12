import { useSyncExternalStore } from "react";
import type { LayoutItem } from "@/widgets/types";
import { PRESETS } from "./presets";

/**
 * Phone home (plan step 12): one widget per screen with tabs on top instead of the widgets in a
 * column. The layouts «Мемы» / «Сеть» are a desktop thing — on the phone the tab order is its own.
 * The phone keeps the «Мемы» settings keys (`memes:<id>`), so filters chosen on the phone before
 * the tabs stay.
 */
export const PHONE_LAYOUT_ID = "memes";

/** «Сейчас» first (plan step 12.3), then the «Мемы» order. Phone sizes are unused: a tab is the screen. */
export const PHONE_TABS: LayoutItem[] = [{ id: "now", type: "now", size: "L", span: 12, h: 608 }, ...PRESETS.memes];

const TAB_KEY = "dash:mtab";
const ORDER_KEY = "dash:mtabs";

/** The reader's own order and hidden tabs (plan step 12.5). Phase 3 moves it to the server. */
export interface TabPrefs {
    order: string[];
    hidden: string[];
}

const DEFAULT_PREFS: TabPrefs = { order: PHONE_TABS.map((t) => t.id), hidden: [] };

/**
 * Stored prefs made safe: unknown ids dropped, tabs added after they were saved go to the end
 * (shown), and at least one tab stays visible.
 */
export function normalizePrefs(raw: unknown): TabPrefs {
    const known = new Set(PHONE_TABS.map((t) => t.id));
    const r = (raw && typeof raw === "object" ? raw : {}) as Partial<Record<keyof TabPrefs, unknown>>;
    const ids = (v: unknown) => (Array.isArray(v) ? v.filter((x): x is string => typeof x === "string" && known.has(x)) : []);
    const order = [...new Set(ids(r.order))];
    for (const t of PHONE_TABS) if (!order.includes(t.id)) order.push(t.id);
    let hidden = [...new Set(ids(r.hidden))];
    if (hidden.length >= order.length) hidden = [];
    return { order, hidden };
}

/** The tabs to draw, in the reader's order. */
export function visibleTabs(prefs: TabPrefs): LayoutItem[] {
    const byId = new Map(PHONE_TABS.map((t) => [t.id, t]));
    return prefs.order.filter((id) => !prefs.hidden.includes(id)).map((id) => byId.get(id)!);
}

const listeners = new Set<() => void>();
const subscribe = (fn: () => void) => {
    listeners.add(fn);
    return () => listeners.delete(fn);
};
const emit = () => listeners.forEach((fn) => fn());

function readTab(): string {
    try {
        const v = localStorage.getItem(TAB_KEY);
        if (v && PHONE_TABS.some((t) => t.id === v)) return v;
    } catch { /* private mode */ }
    return PHONE_TABS[0].id;
}

function readPrefs(): TabPrefs {
    try {
        const raw = localStorage.getItem(ORDER_KEY);
        return raw ? normalizePrefs(JSON.parse(raw)) : DEFAULT_PREFS;
    } catch {
        return DEFAULT_PREFS;
    }
}

const browser = typeof window !== "undefined";
let currentTab: string = browser ? readTab() : PHONE_TABS[0].id;
let prefs: TabPrefs = browser ? readPrefs() : DEFAULT_PREFS;
let tabs: LayoutItem[] = visibleTabs(prefs);

/** The open tab survives a trip to a token page and a reload. */
export function setPhoneTab(id: string) {
    if (id === currentTab) return;
    currentTab = id;
    try {
        localStorage.setItem(TAB_KEY, id);
    } catch { /* private mode */ }
    emit();
}

export function setTabPrefs(next: TabPrefs | null) {
    prefs = next ? normalizePrefs(next) : DEFAULT_PREFS;
    tabs = visibleTabs(prefs);
    try {
        if (next) localStorage.setItem(ORDER_KEY, JSON.stringify(prefs));
        else localStorage.removeItem(ORDER_KEY);
    } catch { /* private mode */ }
    emit();
}

export function usePhoneTab(): string {
    return useSyncExternalStore(subscribe, () => currentTab);
}

export function useTabPrefs(): TabPrefs {
    return useSyncExternalStore(subscribe, () => prefs);
}

/** Visible tabs in order (a stable array until the prefs change). */
export function usePhoneTabs(): LayoutItem[] {
    return useSyncExternalStore(subscribe, () => tabs);
}
