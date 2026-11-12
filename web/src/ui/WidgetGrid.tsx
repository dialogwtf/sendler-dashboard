import * as React from "react";
import { useTranslation } from "react-i18next";
import { height8, span8 } from "@/layouts/presets";
import { REGISTRY } from "@/widgets/registry";
import { useWidgetSettings } from "@/widgets/settings";
import type { LayoutItem } from "@/widgets/types";
import { useMediaQuery } from "@/lib/useMediaQuery";
import { WidgetBoundary } from "./Widget";

/**
 * The layout grid («Основы» board, «Сетка и размеры»):
 *   ≥ 1280 px  — 12 columns, gap 16; S = 3, M = 4–6, L = 8–12
 *   768–1279   — 8 columns; L takes the row, S stretches to 4
 *   < 768      — one column in layout order
 * Phase 2: the layout is an array in code (layouts/presets.ts); phase 3 adds drag-and-drop.
 */
export function WidgetGrid({ layoutId, items }: { layoutId: string; items: LayoutItem[] }) {
    const wide = useMediaQuery("(min-width: 768px)");
    const full = useMediaQuery("(min-width: 1280px)");
    return (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-8 xl:grid-cols-12">
            {items.map((item) => (
                <div
                    key={item.id}
                    className="min-w-0 md:[grid-column:span_var(--s8)/span_var(--s8)] xl:[grid-column:span_var(--s12)/span_var(--s12)]"
                    style={{ "--s8": span8(item), "--s12": item.span } as React.CSSProperties}
                >
                    <Placed layoutId={layoutId} item={item} height={full ? item.h : wide ? height8(item) : item.h} />
                </div>
            ))}
        </div>
    );
}

/** One widget of a layout with its settings (`<layout>:<id>` in localStorage). */
export function Placed({ layoutId, item, height }: { layoutId: string; item: LayoutItem; height?: number }) {
    const { t } = useTranslation();
    const def = REGISTRY[item.type];
    const [settings, setSettings] = useWidgetSettings(`${layoutId}:${item.id}`, { ...def.defaults, ...item.settings });
    const C = def.Component;
    return (
        <WidgetBoundary title={t(def.title)}>
            <C settings={settings} setSettings={setSettings} size={item.size} height={height} />
        </WidgetBoundary>
    );
}
