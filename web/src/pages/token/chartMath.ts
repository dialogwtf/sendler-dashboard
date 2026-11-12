import type { Bar } from "./PriceChart";

/**
 * Bucket candles say only what traded inside the bucket, so a bucket with one trade is a flat
 * line. A pool's price does not move without a trade, so each bar opens where the previous closed
 * — the chart is continuous, as on the launchpads (the first bar keeps its own open).
 */
export function continuous(bars: Bar[]): Bar[] {
    const sorted = [...bars].sort((a, b) => a.time - b.time);
    return sorted.map((b, i) => {
        if (i === 0) return b;
        const open = sorted[i - 1].close;
        return { ...b, open, high: Math.max(b.high, open), low: Math.min(b.low, open) };
    });
}

/** Significant digits that tell the axis labels apart: the label step is ~1/5 of the visible span. */
export function sigFor(value: number, span: number): number {
    if (!(value > 0) || !(span > 0)) return 3;
    return Math.min(8, Math.max(3, Math.ceil(Math.log10(value / (span / 5))) + 1));
}
