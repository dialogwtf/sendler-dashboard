import * as React from "react";
import { cn } from "@/lib/utils";
import { type Trend, trendOf } from "@/lib/format";

const TREND_CLASS: Record<Trend, string> = { up: "text-up-fg", down: "text-down-fg", flat: "text-fg-2" };

/**
 * A number in a row: tabular figures so live updates never jump in width. `trend` colours it;
 * the text itself must already carry ▲/▼ or a sign (format.change) — colour is never alone.
 */
export function Num({
    children,
    trend,
    value,
    strong,
    className,
    title,
}: {
    children: React.ReactNode;
    trend?: Trend;
    /** Derive the trend from a signed value instead. */
    value?: number | null;
    strong?: boolean;
    className?: string;
    title?: string;
}) {
    const tr = trend ?? (value !== undefined ? trendOf(value) : undefined);
    return (
        <span
            title={title}
            className={cn("whitespace-nowrap tabular-nums", tr && TREND_CLASS[tr], strong && "font-semibold", className)}
        >
            {children}
        </span>
    );
}

/** Large number of a widget or page head (Space Grotesk 22–26 / 600). */
export function BigNum({ children, className }: { children: React.ReactNode; className?: string }) {
    return <span className={cn("font-display text-[24px] font-semibold leading-tight tabular-nums", className)}>{children}</span>;
}
