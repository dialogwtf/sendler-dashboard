import type { LayoutItem } from "@/widgets/types";
import type { LayoutId } from "./choice";

/**
 * Preset layouts. «Мемы» follows the Main board row by row; «Сеть» is not on the boards —
 * the same widgets ordered for «что происходит в NEAR» (network and money first).
 * Spans are for the 12-column grid (≥ 1280 px); the grid maps them to 8 columns and one column.
 */
export const PRESETS: Record<LayoutId, LayoutItem[]> = {
    memes: [
        { id: "launches", type: "launches", size: "L", span: 8, h: 608 },
        { id: "movers", type: "movers", size: "M", span: 4, h: 608 },
        { id: "smart", type: "smart", size: "M", span: 4, h: 416 },
        { id: "moves", type: "moves", size: "M", span: 4, h: 416 },
        { id: "network", type: "network", size: "M", span: 4, h: 416 },
        { id: "new", type: "new", size: "M", span: 6, h: 416 },
        { id: "top", type: "top", size: "M", span: 6, h: 416 },
    ],
    network: [
        { id: "network", type: "network", size: "L", span: 8, h: 416 },
        { id: "moves", type: "moves", size: "M", span: 4, h: 416 },
        { id: "new", type: "new", size: "M", span: 6, h: 416 },
        { id: "top", type: "top", size: "M", span: 6, h: 416 },
        { id: "smart", type: "smart", size: "M", span: 6, h: 416 },
        { id: "launches", type: "launches", size: "M", span: 6, h: 416 },
    ],
};

/** Columns at 768–1279 px (8-column grid): L takes the row, S and M take half. */
export function span8(item: Pick<LayoutItem, "size" | "span">): number {
    if (item.size === "L" || item.span >= 8) return 8;
    return 4;
}

/** Height at 768–1279 px: half-width widgets pair up differently than on 12 columns, so S and M share one height. */
export const HALF_HEIGHT_8 = 416;

export function height8(item: Pick<LayoutItem, "size" | "span" | "h">): number {
    return span8(item) === 8 ? item.h : HALF_HEIGHT_8;
}
