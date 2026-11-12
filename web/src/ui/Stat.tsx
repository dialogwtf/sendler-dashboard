import * as React from "react";
import { cn } from "@/lib/utils";

/** Stat tile of a widget (Main board `.stat`): a large number and its caption. */
export function Stat({ value, unit, caption, className }: { value: React.ReactNode; unit?: React.ReactNode; caption: React.ReactNode; className?: string }) {
    return (
        <div className={cn("min-w-0 rounded-[10px] bg-inset p-3", className)}>
            <div className="truncate font-display text-[22px] leading-[1.1] font-semibold tabular-nums">
                {value}
                {unit && <span className="ml-1 font-sans text-[13px] font-normal text-fg-2">{unit}</span>}
            </div>
            <div className="mt-1 truncate text-[11px] text-fg-3">{caption}</div>
        </div>
    );
}
