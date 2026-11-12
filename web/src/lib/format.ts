/**
 * Every number on the dashboard goes through here (rules: «Основы» board, plan «Числа»).
 * Pure functions, no React and no i18n instance — the language comes as an argument, so
 * each rule has a unit test (format.test.ts).
 *
 *   0.000000543        → 0.0₆543        (from 4 zeros after the point: zero count as a subscript)
 *   54 012 $           → $54.0k
 *   1 204 300          → 1.2 млн / 1.2M
 *   6.21 times         → ×6.2
 *   +512.4% / −70.1%   → ▲ 512% / ▼ 70%  (arrow and sign always, never colour alone)
 *   NEAR amount        → 42.0 Ⓝ
 *   age                → 12 с · 6 мин · 5 ч · 3 дн
 *   implicit account   → a1b2…9f0e
 *   no value           → —              (never 0)
 */

export type Lang = "ru" | "en";

export const DASH = "—";
export const MINUS = "−"; // U+2212, same width as + in tabular figures
const NBSP = " ";
const NNBSP = " "; // narrow no-break space: Russian thousands separator

const SUB_DIGITS = "₀₁₂₃₄₅₆₇₈₉";
const toSub = (n: number) => String(n).replace(/\d/g, (d) => SUB_DIGITS[+d]);

type Num = number | null | undefined;
const missing = (n: Num): n is null | undefined => n === null || n === undefined || !Number.isFinite(n);

/** Drop trailing zeros of a fixed-point string: "1.50" → "1.5", "2.00" → "2". */
function trimZeros(s: string): string {
    return s.includes(".") ? s.replace(/\.?0+$/, "") : s;
}

/** Group thousands of an integer string: 1204 → "1 204" (ru) / "1,204" (en). */
function group(intPart: string, lang: Lang): string {
    return intPart.replace(/\B(?=(\d{3})+(?!\d))/g, lang === "ru" ? NNBSP : ",");
}

/**
 * A small positive number with `sig` significant digits; from 4 zeros after the point the
 * zeros are folded into a subscript: 0.000000543 → "0.0₆543", 0.00054 → "0.00054".
 * `trim: false` keeps trailing zeros, so a chart axis has labels of one width.
 */
export function small(abs: number, sig = 3, trim = true): string {
    if (abs === 0) return "0";
    const zeros = Math.max(0, -Math.floor(Math.log10(abs)) - 1); // zeros right after the point
    // toFixed caps at 100 digits; meme prices per raw unit reach 1e-27, so build digits by hand.
    const digits = (abs / 10 ** -(zeros + sig)).toFixed(0).padStart(sig, "0");
    // Rounding can carry into a new digit (0.0009999 → 0.00100): recompute from the rounded value.
    // With no zeros it carries to a whole 1 (0.996 → "1"), which would carry again forever.
    if (digits.length > sig) return zeros === 0 ? "1" : small(Number(`1e-${zeros}`), sig, trim);
    const mant = trim ? digits.replace(/0+$/, "") || "0" : digits;
    if (zeros >= 4) return `0.0${toSub(zeros)}${mant}`;
    return `0.${"0".repeat(zeros)}${mant}`;
}

const UNITS: Record<Lang, [number, string][]> = {
    ru: [[1e12, `${NBSP}трлн`], [1e9, `${NBSP}млрд`], [1e6, `${NBSP}млн`], [1e3, "k"]], // i18n-ok — units per language, the formatter takes the language as an argument
    en: [[1e12, "T"], [1e9, "B"], [1e6, "M"], [1e3, "k"]],
};

/** 54 012 → "54.0k", 1 204 300 → "1.2 млн" / "1.2M", 999 → "999". One decimal under 100 units. */
export function compact(n: Num, lang: Lang): string {
    if (missing(n)) return DASH;
    const sign = n < 0 ? MINUS : "";
    const abs = Math.abs(n);
    for (const [base, unit] of UNITS[lang]) {
        if (abs >= base * 0.9995) {
            let v = abs / base;
            // 999 950 would print "1000.0k": promote to the next unit instead.
            let u = unit;
            const bigger = UNITS[lang].find(([b]) => b === base * 1000);
            if (v >= 999.5 && bigger) {
                v = abs / bigger[0];
                u = bigger[1];
            }
            const body = v >= 100 ? v.toFixed(0) : v.toFixed(1);
            return `${sign}${body}${u}`;
        }
    }
    return sign + plain(abs, lang);
}

/**
 * A chart axis value with `sig` fixed significant digits, in the units of `compact`:
 * 5 170 → "5.170k" (sig 4), 12.5 → "12.50", 0.0000123 → "0.0₄1230". Labels a step apart never
 * collapse into the same text, which `compact`'s one decimal does on a narrow range.
 */
export function axisValue(n: number, lang: Lang, sig: number): string {
    const sign = n < 0 ? MINUS : "";
    const abs = Math.abs(n);
    if (abs === 0) return "0";
    const fixed = (v: number) => v.toFixed(Math.max(0, sig - 1 - Math.floor(Math.log10(v))));
    for (const [base, unit] of UNITS[lang]) {
        if (abs >= base * 0.9995) return `${sign}${fixed(abs / base)}${unit}`;
    }
    return sign + (abs >= 1 ? fixed(abs) : small(abs, sig, false));
}

/** A plain number under 1 000 (or a grouped integer): 2 decimals max, 3 significant below 1. */
function plain(abs: number, lang: Lang): string {
    if (abs === 0) return "0";
    if (abs < 1) return small(abs, 3);
    if (abs < 100) return trimZeros(abs.toFixed(2));
    const [i, f] = abs.toFixed(abs < 1000 ? 1 : 0).split(".");
    return group(i, lang) + (f && f !== "0" ? `.${f}` : "");
}

/** Count of things (trades, holders, txs): grouped up to 9 999, compact above. */
export function count(n: Num, lang: Lang): string {
    if (missing(n)) return DASH;
    const r = Math.round(n);
    if (Math.abs(r) >= 10_000) return compact(r, lang);
    return (r < 0 ? MINUS : "") + group(String(Math.abs(r)), lang);
}

/**
 * NEAR amount: "42.0 Ⓝ", "120 Ⓝ", "0.6 Ⓝ", "1.2k Ⓝ".
 * 1–100: one decimal; 100–1000: integer; below 1: two significant digits.
 */
export function near(n: Num, lang: Lang, { unit = true } = {}): string {
    if (missing(n)) return DASH;
    const sign = n < 0 ? MINUS : "";
    const abs = Math.abs(n);
    let body: string;
    if (abs >= 1000) body = compact(abs, lang);
    else if (abs >= 100) body = abs.toFixed(0);
    else if (abs >= 1) body = abs.toFixed(1);
    else if (abs === 0) body = "0";
    else body = small(abs, 2);
    return sign + body + (unit ? `${NBSP}Ⓝ` : "");
}

/** Dollars: "$54.0k", "$3.42", "$0.0₃540". `sig` — fixed significant digits under $1, for a chart axis. */
export function usd(n: Num, lang: Lang, sig?: number): string {
    if (missing(n)) return DASH;
    const sign = n < 0 ? MINUS : "";
    const abs = Math.abs(n);
    if (abs >= 1000) return `${sign}$${compact(abs, lang)}`;
    if (abs >= 1) return `${sign}$${abs.toFixed(2)}`;
    if (abs === 0) return "$0";
    return `${sign}$${small(abs, sig ?? 3, sig === undefined)}`;
}

/** A price (token in NEAR or $): 3 significant digits, small ones subscripted; `sig` — fixed digits, for a chart axis. */
export function price(n: Num, lang: Lang, sig?: number): string {
    if (missing(n)) return DASH;
    const abs = Math.abs(n);
    if (abs >= 1000) return compact(n, lang);
    if (abs >= 1) {
        const s = abs.toPrecision(sig ?? 3);
        return s.includes("e") ? abs.toFixed(0) : sig === undefined ? trimZeros(s) : s;
    }
    return small(abs, sig ?? 3, sig === undefined);
}

/** Growth multiple: 6.21 → "×6.2", 38.4 → "×38", 384.3 → "×384", 1234 → "×1.2k". */
export function mult(n: Num, lang: Lang = "en"): string {
    if (missing(n)) return DASH;
    if (n >= 1000) return `×${compact(n, lang)}`;
    if (n >= 10) return `×${n.toFixed(0)}`;
    return `×${n.toFixed(1)}`;
}

export type Trend = "up" | "down" | "flat";

export function trendOf(n: Num): Trend {
    if (missing(n) || n === 0) return "flat";
    return n > 0 ? "up" : "down";
}

/**
 * Change given as a FRACTION (0.12 = +12%; the API's change_24h): "▲ 12%", "▼ 70%",
 * "▲ 2.1%" (one decimal under 10%). Colour comes from trendOf — the arrow carries meaning alone.
 */
export function change(fraction: Num, lang: Lang): string {
    if (missing(fraction)) return DASH;
    const pct = fraction * 100;
    const abs = Math.abs(pct);
    const rounded = abs < 10 ? Number(abs.toFixed(1)) : Math.round(abs);
    if (rounded === 0) return "0%";
    const body = rounded >= 10_000 ? compact(rounded, lang) : String(rounded);
    return `${pct > 0 ? "▲" : "▼"}${NBSP}${body}%`;
}

/** Growth RATIO (1.0 = unchanged, the API's `growth`/`x_period`) as a change: 1.16 → "▲ 16%". */
export function ratioChange(ratio: Num, lang: Lang): string {
    return missing(ratio) ? DASH : change(ratio - 1, lang);
}

/** Plain signed percent of a fraction, for ROI: 0.4238 → "+42%". */
export function signedPct(fraction: Num): string {
    if (missing(fraction)) return DASH;
    const pct = Math.round(fraction * 100);
    if (pct === 0) return "0%";
    return `${pct > 0 ? "+" : MINUS}${Math.abs(pct)}%`;
}

const AGE_UNITS: Record<Lang, { s: string; m: string; h: string; d: string; sep: string }> = {
    ru: { s: "с", m: "мин", h: "ч", d: "дн", sep: NBSP }, // i18n-ok
    en: { s: "s", m: "m", h: "h", d: "d", sep: "" },
};

/** Age in seconds → "12 с" / "6 мин" / "5 ч" / "3 дн" (en: 12s / 6m / 5h / 3d). */
export function age(seconds: Num, lang: Lang): string {
    if (missing(seconds)) return DASH;
    const s = Math.max(0, Math.floor(seconds));
    const u = AGE_UNITS[lang];
    if (s < 60) return `${s}${u.sep}${u.s}`;
    if (s < 3600) return `${Math.floor(s / 60)}${u.sep}${u.m}`;
    if (s < 86_400) return `${Math.floor(s / 3600)}${u.sep}${u.h}`;
    return `${Math.floor(s / 86_400)}${u.sep}${u.d}`;
}

const IMPLICIT = /^[0-9a-f]{64}$/;
const ETH_IMPLICIT = /^0x[0-9a-f]{40}$/;

export function isImplicit(account: string): boolean {
    return IMPLICIT.test(account) || ETH_IMPLICIT.test(account);
}

/** Implicit accounts (64 hex, 0x + 40 hex) shortened: "a1b2…9f0e"; named accounts as they are. */
export function account(id: string | null | undefined): string {
    if (!id) return DASH;
    if (IMPLICIT.test(id)) return `${id.slice(0, 4)}…${id.slice(-4)}`;
    if (ETH_IMPLICIT.test(id)) return `${id.slice(0, 6)}…${id.slice(-4)}`;
    return id;
}

/**
 * Raw u128 token amount (string) → a JS number of whole tokens. BigInt keeps the integer part
 * exact; the fraction is cut to 18 digits, which no display needs more of.
 */
export function rawToNumber(raw: string | null | undefined, decimals: number | null | undefined): number | null {
    if (raw === null || raw === undefined || decimals === null || decimals === undefined) return null;
    if (!/^\d+$/.test(raw)) return null;
    const big = BigInt(raw);
    const base = 10n ** BigInt(decimals);
    const whole = big / base;
    const frac = big % base;
    const fracStr = decimals > 0 ? frac.toString().padStart(decimals, "0").slice(0, 18) : "";
    return Number(`${whole}${fracStr ? `.${fracStr}` : ""}`);
}

/** Token amount from raw units: "1.2M", "914", "0.0₅12". No decimals known → "—". */
export function tokenAmount(raw: string | null | undefined, decimals: number | null | undefined, lang: Lang): string {
    const n = rawToNumber(raw, decimals);
    if (n === null) return DASH;
    if (n >= 1000) return compact(n, lang);
    return plain(n, lang);
}

/** Price per raw unit (`px`, candle o/h/l/c) → price per whole token. */
export function perToken(pxRaw: Num, decimals: number | null | undefined): number | null {
    if (missing(pxRaw) || decimals === null || decimals === undefined) return null;
    return pxRaw * 10 ** decimals;
}

/** Block height, grouped with plain spaces in every language: 223 787 303. */
export function blockHeight(n: Num): string {
    if (missing(n)) return DASH;
    return String(Math.round(n)).replace(/\B(?=(\d{3})+(?!\d))/g, " ");
}

/** Trend of a growth multiple (1.0 = unchanged): within ±5% it is noise, not a move. */
export function multTrend(x: Num): Trend {
    if (missing(x) || Math.abs(x - 1) < 0.05) return "flat";
    return x > 1 ? "up" : "down";
}
