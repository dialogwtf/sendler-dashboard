import { describe, expect, it } from "vitest";
import { axisValue, price, usd } from "@/lib/format";
import { continuous, sigFor } from "./chartMath";

const bar = (time: number, open: number, high: number, low: number, close: number) => ({ time, open, high, low, close, volume: 1 });

describe("continuous", () => {
    it("a one-trade bar opens at the previous close", () => {
        const out = continuous([bar(60, 1.02, 1.02, 1.02, 1.02), bar(0, 1, 1, 1, 1)]);
        expect(out.map((b) => b.time)).toEqual([0, 60]);
        expect(out[1]).toMatchObject({ open: 1, high: 1.02, low: 1, close: 1.02 });
    });
    it("the first bar keeps its own open", () => {
        expect(continuous([bar(0, 2, 3, 1, 2.5)])[0]).toMatchObject({ open: 2, high: 3, low: 1 });
    });
    it("a gap down widens the low", () => {
        expect(continuous([bar(0, 1, 1, 1, 1), bar(60, 0.9, 0.95, 0.9, 0.95)])[1]).toMatchObject({ open: 1, high: 1, low: 0.9 });
    });
});

describe("sigFor", () => {
    it("a wide range keeps 3 digits", () => expect(sigFor(1e-6, 1e-6)).toBe(3));
    it("a 4% range needs 4 digits", () => expect(sigFor(1.03e-6, 0.04e-6)).toBe(4));
    it("a 0.1% range needs more", () => expect(sigFor(1, 0.001)).toBe(5));
    it("no range — the default", () => expect(sigFor(1, 0)).toBe(3));
    it("capped at 8", () => expect(sigFor(1, 1e-12)).toBe(8));
});

describe("fixed digits for the axis", () => {
    it("small price keeps trailing zeros", () => expect(price(0.0000010300, "en", 5)).toBe("0.0₅10300"));
    it("default still trims", () => expect(price(0.0000010300, "en")).toBe("0.0₅103"));
    it("over 1", () => expect(price(1.2, "en", 4)).toBe("1.200"));
    it("dollars", () => expect(usd(0.0000123, "en", 4)).toBe("$0.0₄1230"));
});

describe("axisValue (market cap)", () => {
    it("thousands keep the digits", () => {
        expect(axisValue(5170, "en", 4)).toBe("5.170k");
        expect(axisValue(5180, "en", 4)).toBe("5.180k");
    });
    it("millions", () => expect(axisValue(1_234_567, "en", 3)).toBe("1.23M"));
    it("under a thousand", () => expect(axisValue(12.5, "en", 4)).toBe("12.50"));
    it("small", () => expect(axisValue(0.0000123, "en", 4)).toBe("0.0₄1230"));
    it("zero", () => expect(axisValue(0, "en", 4)).toBe("0"));
});
