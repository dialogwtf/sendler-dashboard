import { describe, expect, it } from "vitest";
import { creatorRisk, holdersChange, sharePct } from "./holdersMath";

const at = (h: number) => new Date(Date.UTC(2026, 10, 11, 0) + h * 3_600_000).toISOString();

describe("sharePct", () => {
    it("whole percents from 10", () => expect(sharePct(0.281481)).toBe("28%"));
    it("one decimal under 10", () => expect(sharePct(0.0294)).toBe("2.9%"));
    it("trailing zero dropped", () => expect(sharePct(0.05)).toBe("5%"));
    it("tiny shares", () => expect(sharePct(0.0004)).toBe("<0.1%"));
    it("zero and missing", () => {
        expect(sharePct(0)).toBe("0%");
        expect(sharePct(null)).toBe("—");
        expect(sharePct(undefined)).toBe("—");
    });
});

describe("holdersChange", () => {
    const now = (h: number) => Date.parse(at(h));
    it("against the latest point a day before now", () => {
        const s = [{ t: at(0), n: 10 }, { t: at(10), n: 30 }, { t: at(24), n: 40 }, { t: at(30), n: 55 }];
        // now = h32, a day before = h8 → the latest point at or before it is h0 (n 10)
        expect(holdersChange(s, 57, now(32))).toEqual({ delta: 47, hours: 24 });
    });
    it("an exact day back", () => {
        const s = [{ t: at(0), n: 10 }, { t: at(24), n: 12 }];
        expect(holdersChange(s, 12, now(24))).toEqual({ delta: 2, hours: 24 });
    });
    it("a series younger than a day: from its first point, hours to now", () => {
        const s = [{ t: at(13), n: 4 }, { t: at(14), n: 16 }, { t: at(15), n: 15 }];
        expect(holdersChange(s, 15, now(24.1))).toEqual({ delta: 11, hours: 11 });
    });
    it("unsorted input", () => {
        const s = [{ t: at(15), n: 15 }, { t: at(13), n: 4 }];
        expect(holdersChange(s, 15, now(16))).toEqual({ delta: 11, hours: 3 });
    });
    it("under an hour — nothing", () => expect(holdersChange([{ t: at(1), n: 3 }], 3, now(1.5))).toBeNull());
    it("no usable points — nothing", () => {
        expect(holdersChange([], 3, now(5))).toBeNull();
        expect(holdersChange([{ t: "x", n: 1 }], 3, now(5))).toBeNull();
    });
});

describe("creatorRisk", () => {
    it("creator plus linked from 10%", () => expect(creatorRisk(0.06, 0.05)).toBeCloseTo(0.11));
    it("under 10% — no mark", () => expect(creatorRisk(0.05, 0.04)).toBeNull());
    it("unknown — no mark", () => expect(creatorRisk(null, null)).toBeNull());
});
