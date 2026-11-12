import { Link } from "react-router-dom";
import { TokenIcon } from "@/components/common/TokenIcon";
import { tickerOf } from "@/lib/text";
import { cn } from "@/lib/utils";

/** A ticker is never truncated: when it is too long for a row, the logo stands in for it. */
const MAX_TICKER = 10;

export function tokenPath(token: string) {
    return `/token/${encodeURIComponent(token)}`;
}

/**
 * Token in a row: logo + $TICKER. `link` makes it a link (for rows that are not links as a
 * whole); the full ticker and contract are in the tooltip either way.
 */
export function TokenCell({
    token,
    symbol,
    icon,
    size = 24,
    link = false,
    hideIcon = false,
    className,
}: {
    token: string;
    symbol: string | null | undefined;
    icon?: string | null;
    size?: number;
    link?: boolean;
    hideIcon?: boolean;
    className?: string;
}) {
    const ticker = tickerOf(symbol, token);
    const long = ticker.length > MAX_TICKER;
    const label = (
        <>
            {(!hideIcon || long) && <TokenIcon symbol={ticker} icon={icon} seed={token} size={size} />}
            {long ? <span className="sr-only">{`$${ticker}`}</span> : <b className="whitespace-nowrap font-semibold">{`$${ticker}`}</b>}
        </>
    );
    const cls = cn("inline-flex min-w-0 items-center gap-2", className);
    const title = `$${ticker} · ${token}`;
    return link ? (
        <Link to={tokenPath(token)} className={cn(cls, "relative z-[1] hover:text-brand-text")} title={title}>
            {label}
        </Link>
    ) : (
        <span className={cls} title={title}>
            {label}
        </span>
    );
}
