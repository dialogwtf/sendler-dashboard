import type { CreatorFacts, Launch } from "@/api/types";
import { isFeeFarm } from "@/api/types";

/**
 * Facts, not labels (UI rule 3): what the chain shows about a creator, worst first. The word
 * «скам» is never used. Returns an i18n key, its values and a tone.
 */
export type FactTone = "neutral" | "amber" | "down";

export interface Fact {
    key: string;
    values?: Record<string, number>;
    tone: FactTone;
}

export function creatorFact(f: CreatorFacts | null | undefined): Fact | null {
    if (!f) return null;
    if (f.blocked_by > 0) return { key: "facts.blockedBy", values: { count: f.blocked_by }, tone: "down" };
    if (f.linked) return { key: "facts.linked", tone: "down" };
    if (f.dumped > 0) return { key: "facts.dumps", values: { dumped: f.dumped, launches: f.launches }, tone: "amber" };
    if (f.launches <= 1) return { key: "facts.firstLaunch", tone: "neutral" };
    return { key: "facts.launchesClean", values: { count: f.launches }, tone: "neutral" };
}

/**
 * Tax in percent, whatever shape the API sends: a number (percent), or Nearly's tax_opts object
 * ({buy_bps, sell_bps, …} → the larger side). Unknown → null («—», never 0).
 */
export function taxPct(tax: unknown): number | null {
    if (typeof tax === "number" && Number.isFinite(tax)) return tax;
    if (tax && typeof tax === "object") {
        const t = tax as Record<string, unknown>;
        const bps = Math.max(Number(t.buy_bps) || 0, Number(t.sell_bps) || 0);
        if (Number.isFinite(bps)) return bps / 100;
    }
    return null;
}

const YOCTO = 10n ** 24n;

/** Dev buy in NEAR from the yocto string; "0" → 0 (known: none), null → null (unknown). */
export function devBuyNear(raw: string | null | undefined): number | null {
    if (raw === null || raw === undefined || !/^\d+$/.test(raw)) return null;
    const big = BigInt(raw);
    return Number(big / YOCTO) + Number(big % YOCTO) / 1e24;
}

/** Client-side filters of the feed (the API filters pads, fee farm and blocked creators itself). */
export function passesMinCap(l: Launch, minCapUsd: number): boolean {
    if (!minCapUsd) return true;
    // Capless launches (Intear) and fresh ones without a first trade stay visible: «no value» is not «small».
    return l.cap_usd === null || l.cap_usd >= minCapUsd;
}

export { isFeeFarm };
