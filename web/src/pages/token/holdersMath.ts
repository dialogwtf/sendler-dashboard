/** Pure helpers of the holders section (Holders.tsx), unit-tested in holdersMath.test.ts. */

const HOUR = 3_600_000;
const DAY = 24 * HOUR;

/** Share of the supply, 0..1 → «28%», «2.9%», «0.4%», «0%»; no value → «—». */
export function sharePct(f: number | null | undefined): string {
    if (f === null || f === undefined || !Number.isFinite(f)) return "—";
    const p = Math.max(0, f * 100);
    if (p === 0) return "0%";
    if (p < 0.1) return "<0.1%";
    if (p < 10) return `${Number(p.toFixed(1))}%`;
    return `${Math.round(p)}%`;
}

/**
 * Holder count change over the last day: `current` minus the count of the latest point at least
 * 24 h before `now` (the series is a step function, so an older point still holds the count of a
 * day ago). A series that starts later than a day ago compares with its first point and says over
 * how many hours (`hours` < 24). No points or under an hour — null.
 */
export function holdersChange(series: { t: string; n: number }[], current: number, now: number): { delta: number; hours: number } | null {
    const pts = series
        .map((p) => ({ t: Date.parse(p.t), n: p.n }))
        .filter((p) => Number.isFinite(p.t) && Number.isFinite(p.n) && p.t <= now)
        .sort((a, b) => a.t - b.t);
    if (!pts.length) return null;
    let base = pts[0];
    for (const p of pts) if (p.t <= now - DAY) base = p;
    const hours = base.t <= now - DAY ? 24 : Math.floor((now - base.t) / HOUR);
    if (hours < 1) return null;
    return { delta: current - base.n, hours };
}

/** Creator and the wallets linked to them hold this much or more — the strip marks it. */
export const CREATOR_RISK = 0.1;

export function creatorRisk(dev: number | null | undefined, linked: number | null | undefined): number | null {
    if (dev == null && linked == null) return null;
    const sum = (dev ?? 0) + (linked ?? 0);
    return sum >= CREATOR_RISK ? sum : null;
}
