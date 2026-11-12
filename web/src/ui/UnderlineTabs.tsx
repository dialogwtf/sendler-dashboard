import * as React from "react";
import { cn } from "@/lib/utils";

/**
 * Text tabs with a brand bar under the open one (the phone home's tab row, variant A of the
 * «переключалка вкладок» canvas). 44 px tall; arrows move between tabs. The panel is the caller's:
 * give it `id={panelId(baseId, value)}`, `role="tabpanel"` and `aria-labelledby={tabId(baseId, value)}`.
 */
export function UnderlineTabs<T extends string>({
    value,
    options,
    onChange,
    label,
    baseId,
    className,
}: {
    value: T;
    options: { value: T; label: string }[];
    onChange: (v: T) => void;
    /** Accessible name of the tab row. */
    label: string;
    baseId: string;
    className?: string;
}) {
    const row = React.useRef<HTMLDivElement>(null);
    const onKeyDown = (e: React.KeyboardEvent) => {
        const dir = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
        if (!dir) return;
        e.preventDefault();
        const i = (options.findIndex((o) => o.value === value) + dir + options.length) % options.length;
        onChange(options[i].value);
        (row.current?.children[i] as HTMLElement | undefined)?.focus();
    };
    return (
        <div ref={row} role="tablist" aria-label={label} onKeyDown={onKeyDown} className={cn("flex min-w-0 px-1", className)}>
            {options.map((o) => {
                const on = o.value === value;
                return (
                    <button
                        key={o.value}
                        id={tabId(baseId, o.value)}
                        type="button"
                        role="tab"
                        aria-selected={on}
                        aria-controls={panelId(baseId, o.value)}
                        tabIndex={on ? 0 : -1}
                        onClick={() => onChange(o.value)}
                        className={cn(
                            "relative inline-flex h-11 shrink-0 items-center px-3 text-[14px] font-semibold whitespace-nowrap transition-colors",
                            "after:absolute after:inset-x-3 after:bottom-0 after:h-0.5 after:rounded-full after:bg-brand after:transition-opacity",
                            on ? "text-fg after:opacity-100" : "text-fg-3 after:opacity-0 hover:text-fg-2",
                        )}
                    >
                        {o.label}
                    </button>
                );
            })}
        </div>
    );
}

export const tabId = (baseId: string, value: string) => `${baseId}-tab-${value}`;
export const panelId = (baseId: string, value: string) => `${baseId}-panel-${value}`;
