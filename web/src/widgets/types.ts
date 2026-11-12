import type * as React from "react";

/**
 * Widget contract (plan «Устройство кода»): a widget is a self-contained module with a registry
 * entry; the frame and the grid know nothing about it. Drag-and-drop, custom layouts and new
 * widgets in phase 3 plug in without rework.
 */

export type Size = "S" | "M" | "L";

/** Settings field kinds the settings popover can draw from a schema. */
export type SettingField =
    | { kind: "segment"; key: string; label: string; options: { value: string; label: string }[] }
    | { kind: "checks"; key: string; label: string; options: { value: string; label: string }[] }
    | { kind: "toggle"; key: string; label: string }
    | { kind: "chips"; key: string; label: string; options: { value: string; label: string }[] };

export type SettingsSchema = SettingField[];

export interface WidgetProps<S> {
    settings: S;
    setSettings: (patch: Partial<S>) => void;
    size: Size;
    /** Total height in px set by the layout (multiple of 8); undefined = natural height (phone). */
    height?: number;
    /**
     * The full-list page (`/launches`, `/smart`, …): natural height, the page scrolls, older rows
     * load at the end, all filters shown, no «Развернуть» / «Все …» links.
     */
    full?: boolean;
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export interface WidgetDef<S extends Record<string, unknown> = any> {
    type: WidgetType;
    /** i18n key of the title. */
    title: string;
    size: { default: Size; min: Size };
    settings: SettingsSchema;
    defaults: S;
    /** WebSocket channels the widget needs with these settings (documentation + preloading). */
    channels: (s: S) => string[];
    Component: React.ComponentType<WidgetProps<S>>;
    /** Full list route for the footer «Все … →» and «Развернуть». */
    fullList?: string;
    /**
     * Phone tab badge (plan step 12.4): epoch ms of the rows the widget shows with these settings,
     * from the same caches and channels. Absent = no badge (polled widgets: movers, top, network).
     */
    usePulse?: (s: S) => number[];
    /** Allowed values of string settings: the full-list page reads settings from the address. */
    choices?: Partial<Record<keyof S, readonly string[]>>;
}

export type WidgetType = "now" | "launches" | "movers" | "smart" | "moves" | "network" | "new" | "top";

/** One placed widget of a layout (phase 3 adds x / y for drag-and-drop). */
export interface LayoutItem {
    id: string;
    type: WidgetType;
    size: Size;
    /** Columns of 12 at ≥ 1280 px. S = 3, M = 4–6, L = 8–12. */
    span: number;
    /** Total widget height, px, multiple of 8. */
    h: number;
    settings?: Record<string, unknown>;
}
