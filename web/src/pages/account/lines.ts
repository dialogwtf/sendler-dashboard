import type { Creator, CreatorHistory, Launch } from "@/api/types";

export interface LaunchLine {
    token: string;
    started: string | number | null;
    launch: Launch | null;
    stats: CreatorHistory | null;
}

/** /creators gives two lists: `recent` (the dashboard's launches, ≤ 10) and `history` (the bot's dev stats, ≤ 50). */
export function launchLines(c: Creator): LaunchLine[] {
    const byToken = new Map<string, LaunchLine>();
    for (const h of c.history) byToken.set(h.token, { token: h.token, started: h.started, launch: null, stats: h });
    for (const l of c.recent) {
        const prev = byToken.get(l.token);
        byToken.set(l.token, { token: l.token, started: l.started ?? prev?.started ?? null, launch: l, stats: prev?.stats ?? null });
    }
    const ms = (v: string | number | null) => (v === null ? 0 : typeof v === "number" ? v * 1000 : Date.parse(v) || 0);
    return [...byToken.values()].sort((a, b) => ms(b.started) - ms(a.started));
}
