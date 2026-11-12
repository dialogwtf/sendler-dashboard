/**
 * Chain strings (tickers, names) are written by anyone. React escapes HTML; here we also drop
 * control and bidi-override characters (they can reorder the rest of a row) and cap the length.
 */
const UNSAFE = new RegExp(
    "[" +
        "\\u0000-\\u001F\\u007F-\\u009F" + // C0 / C1 controls
        "\\u200B-\\u200F" + // zero-width, LRM / RLM
        "\\u202A-\\u202E" + // bidi embeddings and overrides
        "\\u2066-\\u2069" + // bidi isolates
        "\\uFEFF" + // BOM
        "]",
    "g",
);

export function clean(s: string | null | undefined, max = 64): string {
    if (!s) return "";
    const t = s.replace(UNSAFE, "").trim();
    return t.length > max ? `${t.slice(0, max - 1)}…` : t;
}

/** Ticker for display; tokens without metadata fall back to the contract's first label. */
export function tickerOf(symbol: string | null | undefined, token: string): string {
    const s = clean(symbol, 32);
    if (s) return s;
    return clean(token.split(".")[0], 24);
}
