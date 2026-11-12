import * as React from "react";
import { cn } from "@/lib/utils";
import { safeIconSrc } from "@/lib/safeUrl";
import { useTokenMeta } from "@/api/batch";

/**
 * Token icon: metadata icon (usually a data: URI from ft_metadata) when the
 * caller has one, otherwise a built-in mark for the well-known tokens, else the
 * first letter.
 *
 * The built-ins are INLINE SVG on purpose. They used to be <img> pointing at
 * logo.svgcdn.com, which stopped resolving (NXDOMAIN) and silently took the
 * USDT/USDC icons down with it — everywhere the caller passed no `icon`
 * (currency-filter trigger, history-drawer options). Never reintroduce a remote
 * host here: a fallback that depends on the network is not a fallback.
 */

/**
 * NEAR — inline copy of the token's own `ft_metadata` art (green disc, black N),
 * so the mark looks the same here as it does wherever metadata is available
 * (currency filter, market listings). Fixed colours on purpose: it is the NEAR
 * logo, not a UI glyph, so it must not follow the user's accent.
 */
function NearMark({ className }: { className?: string }) {
    return (
        <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" className={className}>
            <defs>
                <linearGradient id="near-mark-g" x1="12" y1="0" x2="12" y2="24" gradientUnits="userSpaceOnUse">
                    <stop stopColor="#5CE8AB" />
                    <stop offset="1" stopColor="#00C67F" />
                </linearGradient>
            </defs>
            <circle cx="12" cy="12" r="12" fill="url(#near-mark-g)" />
            <path
                d="M7 17.4V6.9l10 10.2V6.6"
                stroke="#000"
                strokeWidth="2.6"
                strokeLinecap="round"
                strokeLinejoin="round"
            />
        </svg>
    );
}

/** Tether — green disc, white ₮ */
function UsdtMark({ className }: { className?: string }) {
    return (
        <svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg" className={className}>
            <circle cx="12" cy="12" r="12" fill="#26A17B" />
            <path
                d="M13.35 10.68V9.2h3.4V6.94H7.26V9.2h3.4v1.48c-2.76.13-4.84.68-4.84 1.33 0 .66 2.08 1.2 4.84 1.33v4.26h2.69v-4.26c2.76-.13 4.83-.67 4.83-1.33 0-.65-2.07-1.2-4.83-1.33Zm0 2.25v-.01c-.07 0-.42.03-1.19.03-.62 0-1.05-.02-1.2-.03v.01c-2.4-.11-4.19-.53-4.19-1.03 0-.5 1.79-.92 4.19-1.03v1.63c.16.01.6.04 1.21.04.74 0 1.11-.03 1.18-.04v-1.63c2.4.11 4.18.53 4.18 1.03 0 .5-1.78.92-4.18 1.03Z"
                fill="#fff"
            />
        </svg>
    );
}

/** USD Coin — blue disc, white $ in a ring */
function UsdcMark({ className }: { className?: string }) {
    return (
        <svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg" className={className}>
            <circle cx="12" cy="12" r="12" fill="#2775CA" />
            <path
                d="M12 3.5a8.5 8.5 0 1 0 0 17 8.5 8.5 0 0 0 0-17Zm0 1.6a6.9 6.9 0 1 1 0 13.8 6.9 6.9 0 0 1 0-13.8Z"
                fill="#fff"
                opacity=".55"
            />
            <path
                d="M12.62 11.42c-1.5-.4-1.98-.66-1.98-1.28 0-.6.53-1 1.4-1 .78 0 1.28.28 1.5.9.05.15.18.24.33.24h.7c.2 0 .35-.15.35-.34v-.06c-.2-1-.98-1.72-2-1.9v-.9c0-.2-.16-.35-.36-.35h-.68c-.2 0-.35.15-.35.34v.88c-1.42.2-2.3 1.06-2.3 2.2 0 1.4 1.03 1.94 2.66 2.36 1.4.36 1.86.68 1.86 1.36 0 .7-.6 1.16-1.45 1.16-1.1 0-1.55-.44-1.72-1.06a.35.35 0 0 0-.34-.26h-.75a.34.34 0 0 0-.34.34v.06c.2 1.1 1 1.9 2.38 2.1v.9c0 .2.15.34.35.34h.68c.2 0 .35-.15.35-.34v-.9c1.48-.22 2.4-1.14 2.4-2.36 0-1.5-1.05-2.04-2.7-2.44Z"
                fill="#fff"
            />
        </svg>
    );
}

type Mark = (props: { className?: string }) => React.JSX.Element;

/** Built-in marks by symbol (no network). Metadata icons always win over these. */
const MARKS: Record<string, Mark> = {
    NEAR: NearMark,
    WNEAR: NearMark,
    USDT: UsdtMark,
    USDC: UsdcMark,
};

/**
 * Symbols on chain carry suffixes the marks don't ("USDC.e", "USDt" → "USDC.E",
 * "USDT"), so match the base symbol too.
 */
function markFor(symbol: string): Mark | undefined {
    const s = symbol.toUpperCase();
    return MARKS[s] ?? MARKS[s.split(".")[0]];
}

/** Initials disc colours from the boards (MHome): picked by a hash of the token, stable per token. */
const DISC = ["#9AA6FF", "#E6A93A", "#5ED3F3", "#7BD389", "#D59BFF", "#FF9C93"];

function hash(s: string): number {
    let h = 0;
    for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
    return Math.abs(h);
}

export function TokenIcon({
    symbol,
    icon,
    seed,
    className,
    size,
}: {
    symbol?: string;
    /** Icon from token metadata (data: URI in practice). Untrusted: only ever an <img src>. */
    icon?: string | null;
    /** Token address — keeps the fallback colour stable. */
    seed?: string;
    className?: string;
    size?: number;
}) {
    const [imgError, setImgError] = React.useState(false);
    const [loaded, setLoaded] = React.useState(false);
    // The icon comes from the batched /tokens/meta (shared cache). While it is on its way the
    // slot is a quiet placeholder — never initials that are then swapped for the real logo.
    const meta = useTokenMeta(icon ? null : seed);
    const s = (symbol || "?").toUpperCase();
    const px = size ?? 24;
    const Mark = markFor(s);
    const src = safeIconSrc(icon ?? meta.data?.icon);

    if (src && !imgError) {
        return (
            <img
                src={src}
                alt=""
                aria-hidden="true"
                decoding="async"
                referrerPolicy="no-referrer"
                onLoad={() => setLoaded(true)}
                onError={() => setImgError(true)}
                className={cn("shrink-0 rounded-full bg-surface-2 object-cover transition-opacity duration-200", !loaded && "opacity-0", className)}
                style={{ width: px, height: px }}
            />
        );
    }

    if (!Mark && !icon && seed && meta.isPending) {
        return <span aria-hidden="true" className={cn("skeleton shrink-0 !rounded-full", className)} style={{ width: px, height: px }} />;
    }

    if (Mark) {
        return (
            <span aria-hidden="true" className={cn("inline-flex shrink-0 items-center justify-center rounded-full", className)} style={{ width: px, height: px }}>
                <Mark className="size-full" />
            </span>
        );
    }

    const initials = s.replace(/[^\p{L}\p{N}]/gu, "").slice(0, 2) || "?";
    return (
        <span
            aria-hidden="true"
            className={cn("grid shrink-0 place-items-center rounded-full font-display font-bold text-[#0B0E14]", className)}
            style={{ width: px, height: px, background: DISC[hash(seed ?? s) % DISC.length], fontSize: Math.max(9, Math.round(px * 0.36)) }}
        >
            {initials}
        </span>
    );
}
