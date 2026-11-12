import * as React from "react";
import { cn } from "@/lib/utils";

/**
 * Phone tab header (Widget in PhoneFrame): the widget's filters are second-level under the tabs,
 * so they draw as small outlined chips in the tabs' round shape — never a second row of big pills,
 * never the desktop's boxed control. The active chip is tinted with the brand, not filled white
 * like the active tab, so the two levels never read as one.
 */
const ChipsCtx = React.createContext(false);
export const SegmentedChips = ChipsCtx.Provider;

/**
 * The widget header's segmented control (Main board `.tabs`): period, sort, kind.
 * A radio group: arrows move the choice, the active option is announced.
 */
export function Segmented<T extends string>({
    value,
    options,
    onChange,
    label,
    className,
}: {
    value: T;
    options: { value: T; label: string }[];
    onChange: (v: T) => void;
    /** Accessible name of the group («Период»). */
    label: string;
    className?: string;
}) {
    const chips = React.useContext(ChipsCtx);
    const move = (dir: 1 | -1) => {
        const i = options.findIndex((o) => o.value === value);
        const next = options[(i + dir + options.length) % options.length];
        onChange(next.value);
    };
    return (
        <div
            role="radiogroup"
            aria-label={label}
            className={cn(
                chips
                    // A second group in the same row (sort · period) sits behind a thin divider.
                    ? "flex shrink-0 items-center gap-1.5 [[role=radiogroup]+&]:ml-1 [[role=radiogroup]+&]:border-l [[role=radiogroup]+&]:border-line [[role=radiogroup]+&]:pl-2.5"
                    : "flex shrink-0 gap-0.5 rounded-lg bg-inset p-0.5",
                className,
            )}
            onKeyDown={(e) => {
                if (e.key === "ArrowRight" || e.key === "ArrowDown") { e.preventDefault(); move(1); }
                if (e.key === "ArrowLeft" || e.key === "ArrowUp") { e.preventDefault(); move(-1); }
            }}
        >
            {options.map((o) => {
                const on = o.value === value;
                return (
                    <button
                        key={o.value}
                        type="button"
                        role="radio"
                        aria-checked={on}
                        tabIndex={on ? 0 : -1}
                        onClick={() => onChange(o.value)}
                        className={cn(
                            chips
                                // 32 px to the eye, 44 px to the finger (the ::after reaches the row's edges).
                                ? "relative h-8 rounded-full border px-3 text-[12px] font-semibold whitespace-nowrap transition-colors after:absolute after:inset-x-0 after:-inset-y-1.5 after:content-['']"
                                : "rounded-md px-[9px] py-[5px] text-[12px] font-medium whitespace-nowrap pointer-coarse:min-h-11 pointer-coarse:min-w-11 pointer-coarse:px-3 pointer-coarse:text-[13px]",
                            chips
                                ? on ? "border-transparent bg-brand-soft text-brand-soft-fg" : "border-line text-fg-2 hover:text-fg"
                                : on ? "bg-line text-fg" : "text-fg-2 hover:text-fg",
                        )}
                    >
                        {o.label}
                    </button>
                );
            })}
        </div>
    );
}
