import { describe, expect, it } from "vitest";
import { clean, tickerOf } from "./text";

describe("chain strings", () => {
    it("drops bidi overrides and control characters", () => expect(clean("AB‮C\u0007D")).toBe("ABCD"));
    it("caps the length", () => expect(clean("x".repeat(100), 10)).toBe(`${"x".repeat(9)}…`));
    it("ticker falls back to the contract label", () => expect(tickerOf(null, "qcat-3.nearlytrade.near")).toBe("qcat-3"));
});
