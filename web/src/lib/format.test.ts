import { describe, expect, it } from "vitest";
import {
    DASH, account, age, blockHeight, multTrend, change, compact, count, mult, near, perToken, price, rawToNumber,
    ratioChange, signedPct, small, tokenAmount, trendOf, usd,
} from "./format";

const NBSP = " ";
const NNBSP = " ";

describe("small: zeros folded into a subscript from 4 zeros", () => {
    it("plan example", () => expect(small(0.000000543)).toBe("0.0₆543"));
    it("exactly 4 zeros", () => expect(small(0.0000158)).toBe("0.0₄158"));
    it("3 zeros stay plain", () => expect(small(0.00054)).toBe("0.00054"));
    it("meme price per raw unit (1e-27)", () => expect(small(1.4045e-24)).toBe("0.0₂₃14"));
    it("trailing zeros of the mantissa dropped", () => expect(small(0.0000005)).toBe("0.0₆5"));
    it("rounding up to a whole 1 stops there (was an endless recursion)", () => {
        expect(small(0.996, 2)).toBe("1");
        expect(small(0.9999)).toBe("1");
        expect(small(0.0009999)).toBe("0.001");
    });
    it("rounding carries into the next digit", () => expect(small(0.0009999)).toBe("0.001"));
    it("two significant digits", () => expect(small(0.000001234, 2)).toBe("0.0₅12"));
});

describe("compact", () => {
    it("$54.0k from 54 012", () => expect(compact(54_012, "ru")).toBe("54.0k"));
    it("1.2 млн (ru)", () => expect(compact(1_204_300, "ru")).toBe(`1.2${NBSP}млн`));
    it("1.2M (en)", () => expect(compact(1_204_300, "en")).toBe("1.2M"));
    it("integer from 100 units", () => expect(compact(123_456, "en")).toBe("123k"));
    it("999 950 promotes to the next unit", () => expect(compact(999_950, "en")).toBe("1.0M"));
    it("under 1000 is not compacted", () => expect(compact(999, "en")).toBe("999"));
    it("negative", () => expect(compact(-54_012, "en")).toBe("−54.0k"));
    it("billions", () => expect(compact(2_500_000_000, "ru")).toBe(`2.5${NBSP}млрд`));
    it("missing", () => expect(compact(null, "en")).toBe(DASH));
});

describe("count", () => {
    it("grouped in ru", () => expect(count(1204, "ru")).toBe(`1${NNBSP}204`));
    it("grouped in en", () => expect(count(1204, "en")).toBe("1,204"));
    it("compact from 10 000", () => expect(count(67_009, "en")).toBe("67.0k"));
    it("zero is a value, not missing", () => expect(count(0, "en")).toBe("0"));
    it("missing", () => expect(count(undefined, "en")).toBe(DASH));
});

describe("near", () => {
    it("one decimal 1–100", () => expect(near(42, "ru")).toBe(`42.0${NBSP}Ⓝ`));
    it("integer 100–1000", () => expect(near(120.4, "ru")).toBe(`120${NBSP}Ⓝ`));
    it("two significant below 1", () => expect(near(0.6, "ru")).toBe(`0.6${NBSP}Ⓝ`));
    it("compact from 1000", () => expect(near(16_983.9, "en")).toBe(`17.0k${NBSP}Ⓝ`));
    it("without unit", () => expect(near(15.5, "en", { unit: false })).toBe("15.5"));
    it("missing", () => expect(near(null, "en")).toBe(DASH));
});

describe("usd", () => {
    it("$54.0k", () => expect(usd(54_012, "en")).toBe("$54.0k"));
    it("$3.42", () => expect(usd(3.42, "en")).toBe("$3.42"));
    it("small price", () => expect(usd(0.000540, "en")).toBe("$0.00054"));
    it("tiny price", () => expect(usd(0.0000540, "en")).toBe("$0.0₄54"));
    it("missing", () => expect(usd(null, "en")).toBe(DASH));
});

describe("price", () => {
    it("3 significant", () => expect(price(1.2366, "en")).toBe("1.24"));
    it("sub-1", () => expect(price(0.0978185, "en")).toBe("0.0978"));
    it("tiny", () => expect(price(0.0000158, "en")).toBe("0.0₄158"));
    it("missing", () => expect(price(undefined, "en")).toBe(DASH));
});

describe("mult", () => {
    it("×6.2", () => expect(mult(6.21)).toBe("×6.2"));
    it("×38 from 10", () => expect(mult(38.4)).toBe("×38"));
    it("×384", () => expect(mult(384.28)).toBe("×384"));
    it("missing — dash, not ×0", () => expect(mult(null)).toBe(DASH));
});

describe("change: arrow and sign always", () => {
    it("▲ 512%", () => expect(change(5.124, "en")).toBe(`▲${NBSP}512%`));
    it("▼ 70%", () => expect(change(-0.701, "en")).toBe(`▼${NBSP}70%`));
    it("one decimal under 10%", () => expect(change(0.021, "en")).toBe(`▲${NBSP}2.1%`));
    it("zero has no arrow", () => expect(change(0.0001, "en")).toBe("0%"));
    it("ratio 1.16 → ▲ 16%", () => expect(ratioChange(1.1647, "en")).toBe(`▲${NBSP}16%`));
    it("ratio below 1", () => expect(ratioChange(0.9374, "en")).toBe(`▼${NBSP}6.3%`));
    it("trend", () => {
        expect(trendOf(0.1)).toBe("up");
        expect(trendOf(-0.1)).toBe("down");
        expect(trendOf(0)).toBe("flat");
        expect(trendOf(null)).toBe("flat");
    });
    it("signed ROI", () => {
        expect(signedPct(0.4238)).toBe("+42%");
        expect(signedPct(-0.12)).toBe("−12%");
    });
});

describe("age", () => {
    it("seconds", () => expect(age(12, "ru")).toBe(`12${NBSP}с`));
    it("minutes", () => expect(age(6 * 60 + 59, "ru")).toBe(`6${NBSP}мин`));
    it("hours", () => expect(age(5 * 3600, "ru")).toBe(`5${NBSP}ч`));
    it("days", () => expect(age(3 * 86_400 + 10, "ru")).toBe(`3${NBSP}дн`));
    it("en", () => expect(age(360, "en")).toBe("6m"));
    it("clock skew never negative", () => expect(age(-5, "en")).toBe("0s"));
});

describe("account", () => {
    it("implicit", () =>
        expect(account("a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e9f0e")).toBe("a1b2…9f0e"));
    it("eth implicit", () => expect(account("0x1234567890abcdef1234567890abcdef12345678")).toBe("0x1234…5678"));
    it("named as is", () => expect(account("quietwhale.near")).toBe("quietwhale.near"));
    it("missing", () => expect(account(null)).toBe(DASH));
});

describe("raw token amounts via BigInt", () => {
    it("u128 beyond Number precision", () =>
        expect(rawToNumber("92990680638544600514278482", 18)).toBeCloseTo(92_990_680.6385446, 4));
    it("formatted", () => expect(tokenAmount("914000000000000000000", 18, "en")).toBe("914"));
    it("compact", () => expect(tokenAmount("2858287683262099792346491", 18, "en")).toBe("2.9M"));
    it("no decimals — dash", () => expect(tokenAmount("123", null, "en")).toBe(DASH));
    it("garbage — dash", () => expect(tokenAmount("1e5", 6, "en")).toBe(DASH));
    it("price per token from raw px", () => expect(perToken(9.78e-20, 18)).toBeCloseTo(0.0978, 6));
});

describe("block height", () => {
    it("plain spaces", () => expect(blockHeight(223787303)).toBe("223 787 303"));
    it("missing", () => expect(blockHeight(null)).toBe(DASH));
});

describe("multTrend", () => {
    it("noise band", () => expect(multTrend(0.99)).toBe("flat"));
    it("up", () => expect(multTrend(1.4)).toBe("up"));
    it("down", () => expect(multTrend(0.7)).toBe("down"));
    it("missing", () => expect(multTrend(null)).toBe("flat"));
});
