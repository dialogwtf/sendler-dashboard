/**
 * Strings from the chain are written by anyone (tickers, names, creator links). A link is
 * rendered only if it is https:, always with rel="noopener noreferrer nofollow".
 */
export function safeHttpsUrl(raw: unknown): string | null {
    if (typeof raw !== "string" || raw.length > 2048) return null;
    try {
        const u = new URL(raw.trim());
        return u.protocol === "https:" ? u.href : null;
    } catch {
        return null;
    }
}

/** Token icons: only data:image/* (from ft_metadata) or https:, and only ever used as <img src>. */
export function safeIconSrc(raw: unknown): string | null {
    if (typeof raw !== "string" || raw.length > 200_000) return null;
    if (/^data:image\/(png|jpe?g|gif|webp|svg\+xml|avif)[;,]/i.test(raw)) return raw;
    return safeHttpsUrl(raw);
}

export const EXTERNAL_REL = "noopener noreferrer nofollow";

const EXPLORER_TX = (import.meta.env.VITE_EXPLORER_TX as string | undefined) || "https://nearblocks.io/txns/";

export function txUrl(tx: string): string {
    return EXPLORER_TX + encodeURIComponent(tx);
}

/** Account / contract page in the explorer (same host as the tx links). */
export function explorerAccountUrl(account: string): string {
    return EXPLORER_TX.replace(/txns\/?$/, "address/") + encodeURIComponent(account);
}
