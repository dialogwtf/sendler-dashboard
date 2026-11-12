import * as React from "react";
import { cn } from "@/lib/utils";

/** A page's stat tile (Token board): caption, a large number, a line under it. */
export function Tile({ label, value, sub, tone }: { label: string; value: React.ReactNode; sub?: React.ReactNode; tone?: "up" | "down" | "flat" }) {
    return (
        <div className="min-w-0 rounded-[var(--radius-widget)] border border-line bg-surface p-3.5">
            <div className="text-[11px] text-fg-3">{label}</div>
            <div className={cn("mt-0.5 truncate font-display text-[20px] font-semibold tabular-nums", tone === "up" && "text-up-fg", tone === "down" && "text-down-fg")}>
                {value}
            </div>
            <div className="truncate text-[12px] text-fg-3">{sub ?? " "}</div>
        </div>
    );
}

/** Skeleton row of `n` tiles, the same height as real ones. */
export function TileSkeletons({ n, className }: { n: number; className?: string }) {
    return (
        <div className={className} aria-hidden="true">
            {Array.from({ length: n }, (_, i) => <div key={i} className="skeleton h-[86px] !rounded-[var(--radius-widget)]" />)}
        </div>
    );
}

/** One fact of a dossier: a mark (never colour alone) and its text. */
export function Fact({ mark, tone, children }: { mark: string; tone: "amber" | "up" | "down" | "neutral"; children: React.ReactNode }) {
    const cls = { amber: "text-amber-fg", up: "text-up-fg", down: "text-down-fg", neutral: "text-fg-2" }[tone];
    return (
        <div className="flex gap-2.5 border-b border-row-line py-2.5 last:border-0">
            <span className={cn("w-3 shrink-0 text-center", cls)} aria-hidden="true">{mark}</span>
            <div className="min-w-0">{children}</div>
        </div>
    );
}
