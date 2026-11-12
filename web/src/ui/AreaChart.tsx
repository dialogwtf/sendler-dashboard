import * as React from "react";
import { cn } from "@/lib/utils";

/**
 * One-series area chart (dataviz skill: a single series needs no legend — the caption names it;
 * 2 px line, light fill to a zero baseline, recessive grid, crosshair + tooltip on hover and on
 * keyboard focus, value first in the tooltip). The table view lives with the caller.
 *
 * Colours are CSS roles, so the chart follows the theme. Labels come in already formatted.
 */
export interface Point {
    t: number; // ms
    v: number;
}

const PAD = { top: 10, right: 6, bottom: 20, left: 40 };

function niceMax(max: number): number {
    if (max <= 0) return 1;
    const p = 10 ** Math.floor(Math.log10(max));
    for (const m of [1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10]) if (m * p >= max) return m * p;
    return 10 * p;
}

export function AreaChart({
    points,
    formatY,
    formatX,
    formatTip,
    xTicks,
    summary,
    className,
    color = "var(--brand)",
}: {
    points: Point[];
    /** Axis labels: compact («2.2k»). */
    formatY: (v: number) => string;
    /** Tooltip time («14:32»). */
    formatX: (t: number) => string;
    /** Tooltip value («2 540»). */
    formatTip: (v: number) => string;
    /** Three x labels: start, middle, end («−60 мин», «−30», «сейчас»). */
    xTicks: [string, string, string];
    /** Text alternative for screen readers. */
    summary: string;
    className?: string;
    color?: string;
}) {
    const box = React.useRef<HTMLDivElement>(null);
    const [size, setSize] = React.useState({ w: 0, h: 0 });
    const [hover, setHover] = React.useState<number | null>(null);

    React.useLayoutEffect(() => {
        const el = box.current;
        if (!el) return;
        const ro = new ResizeObserver(([e]) => {
            const r = e.contentRect;
            setSize({ w: Math.round(r.width), h: Math.round(r.height) });
        });
        ro.observe(el);
        return () => ro.disconnect();
    }, []);

    const { w, h } = size;
    const n = points.length;
    const iw = Math.max(0, w - PAD.left - PAD.right);
    const ih = Math.max(0, h - PAD.top - PAD.bottom);
    const yMax = niceMax(Math.max(0, ...points.map((p) => p.v)));
    const x = (i: number) => PAD.left + (n <= 1 ? iw : (i / (n - 1)) * iw);
    const y = (v: number) => PAD.top + ih - (v / yMax) * ih;

    const line = points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)} ${y(p.v).toFixed(1)}`).join(" ");
    const area = n ? `${line} L${x(n - 1).toFixed(1)} ${PAD.top + ih} L${x(0).toFixed(1)} ${PAD.top + ih} Z` : "";
    const grid = [yMax, yMax / 2, 0];

    const pick = (clientX: number) => {
        const el = box.current;
        if (!el || n === 0) return;
        const rx = clientX - el.getBoundingClientRect().left - PAD.left;
        setHover(Math.max(0, Math.min(n - 1, Math.round((rx / Math.max(1, iw)) * (n - 1)))));
    };

    const hi = hover !== null && hover < n ? hover : null;
    const tip = hi !== null ? `${formatTip(points[hi].v)} · ${formatX(points[hi].t)}` : "";
    const tipW = tip.length * 6.2 + 16;
    const tipX = hi !== null ? Math.min(Math.max(x(hi) - tipW / 2, PAD.left), w - PAD.right - tipW) : 0;

    return (
        <div
            ref={box}
            className={cn("relative min-h-[96px] w-full touch-pan-y outline-none", className)}
            tabIndex={n ? 0 : -1}
            role="img"
            aria-label={summary}
            onPointerMove={(e) => pick(e.clientX)}
            onPointerDown={(e) => pick(e.clientX)}
            onPointerLeave={() => setHover(null)}
            onBlur={() => setHover(null)}
            onKeyDown={(e) => {
                if (!n) return;
                if (e.key === "ArrowLeft") setHover((i) => Math.max(0, (i ?? n) - 1));
                else if (e.key === "ArrowRight") setHover((i) => Math.min(n - 1, (i ?? -1) + 1));
                else if (e.key === "Escape") setHover(null);
                else return;
                e.preventDefault();
            }}
        >
            {w > 0 && h > 0 && (
                <svg width={w} height={h} className="absolute inset-0 block" aria-hidden="true">
                    <g stroke="var(--line-soft)" strokeWidth={1}>
                        {grid.map((g) => (
                            <line key={g} x1={PAD.left} x2={w - PAD.right} y1={y(g)} y2={y(g)} />
                        ))}
                    </g>
                    <g fill="var(--fg-3)" fontSize={10} fontFamily="Inter, sans-serif">
                        {grid.map((g) => (
                            <text key={g} x={PAD.left - 6} y={y(g) + 3.5} textAnchor="end" className="tabular-nums">
                                {formatY(g)}
                            </text>
                        ))}
                        <text x={PAD.left} y={h - 4}>{xTicks[0]}</text>
                        <text x={PAD.left + iw / 2} y={h - 4} textAnchor="middle">{xTicks[1]}</text>
                        <text x={w - PAD.right} y={h - 4} textAnchor="end">{xTicks[2]}</text>
                    </g>
                    {n > 0 && (
                        <>
                            <path d={area} fill={color} fillOpacity={0.14} />
                            <path d={line} fill="none" stroke={color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
                        </>
                    )}
                    {hi !== null && (
                        <g>
                            <line x1={x(hi)} x2={x(hi)} y1={PAD.top} y2={PAD.top + ih} stroke="var(--fg-2)" strokeDasharray="3 3" />
                            <circle cx={x(hi)} cy={y(points[hi].v)} r={4.5} fill={color} stroke="var(--surface)" strokeWidth={2} />
                            <rect x={tipX} y={0} width={tipW} height={20} rx={5} fill="var(--line)" />
                            <text x={tipX + tipW / 2} y={14} textAnchor="middle" fill="var(--fg)" fontSize={10.5} fontWeight={600} fontFamily="Inter, sans-serif" className="tabular-nums">
                                {tip}
                            </text>
                        </g>
                    )}
                </svg>
            )}
        </div>
    );
}

/** Tiny trend line for rows (Rising): no axes, no hover; the row text carries the numbers. */
export function Sparkline({ values, width = 76, height = 28, label }: { values: number[]; width?: number; height?: number; label: string }) {
    if (values.length < 2) return <span style={{ width, height }} className="inline-block" />;
    const min = Math.min(...values);
    const max = Math.max(...values);
    const span = max - min || 1;
    const pts = values
        .map((v, i) => `${((i / (values.length - 1)) * (width - 4) + 2).toFixed(1)},${(height - 2 - ((v - min) / span) * (height - 4)).toFixed(1)}`)
        .join(" ");
    const up = values[values.length - 1] >= values[0];
    return (
        <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img" aria-label={label} className="shrink-0">
            <polyline points={pts} fill="none" stroke={up ? "var(--up)" : "var(--down)"} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
        </svg>
    );
}
