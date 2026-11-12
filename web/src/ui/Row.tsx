import * as React from "react";
import { Link } from "react-router-dom";
import { Eye } from "lucide-react";
import { useTranslation } from "react-i18next";
import { EXTERNAL_REL } from "@/lib/safeUrl";
import { useMediaQuery } from "@/lib/useMediaQuery";
import { watchTokenUrl, watchWalletUrl } from "@/lib/botLinks";
import { openTelegramUrl } from "@/lib/telegram";
import { cn } from "@/lib/utils";
import { BuyInBot } from "./BuyInBot";

/** Inside a row's tap panel (phone): actions are drawn as full-size buttons. */
const PanelCtx = React.createContext(false);

/**
 * A list row that is a real link (UI spec: «строка — ссылка и фокусируется с клавиатуры»)
 * without nesting <a> in <a>: the row's link is stretched over the whole row, and inner links
 * (an account, a creator) sit above it (`relative z-[1]` via the `inner` class on them).
 * Hover / focus actions (`actions`) appear on the right on top of the row.
 *
 * Touch screens have no hover (UI spec «Мобайл»: «тап раскрывает „Купить в боте“, „Следить“,
 * „Открыть“»): a row with actions opens a panel under itself on tap instead of navigating.
 */
export function Row({
    to,
    href,
    label,
    className,
    style,
    children,
    actions,
}: {
    /** Internal route (token, account). */
    to?: string;
    /** External link (explorer). */
    href?: string;
    /** Accessible name of the row link. */
    label: string;
    className?: string;
    style?: React.CSSProperties;
    children: React.ReactNode;
    actions?: React.ReactNode;
}) {
    const { t } = useTranslation();
    const touch = useMediaQuery("(hover: none)");
    const [open, setOpen] = React.useState(false);
    const panelId = React.useId();
    const tap = touch && !!actions;
    const overlay = "absolute inset-0 z-0 rounded-[inherit] focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-brand";
    const openLink = "inline-flex h-11 shrink-0 items-center rounded-[10px] bg-surface-2 px-4 text-[14px] font-semibold text-fg";
    return (
        <>
            <div
                className={cn(
                    // content-visibility: rows scrolled out of a widget are not laid out or painted —
                    // the home page holds ~350 rows, a few dozen are on screen (plan step 10, first screen)
                    "group relative grid items-center gap-3 border-b border-row-line px-4 hover:bg-inset [contain-intrinsic-size:auto_56px] [content-visibility:auto]",
                    tap && open && "border-b-transparent bg-inset",
                    className,
                )}
                style={style}
            >
                {tap ? (
                    <button
                        type="button"
                        className={overlay}
                        aria-label={label}
                        aria-expanded={open}
                        aria-controls={panelId}
                        onClick={() => setOpen((o) => !o)}
                    />
                ) : to ? (
                    <Link to={to} className={overlay} aria-label={label} />
                ) : href ? (
                    <a href={href} target="_blank" rel={EXTERNAL_REL} className={overlay} aria-label={label} />
                ) : null}
                {children}
                {actions && !tap && (
                    <span className="pointer-events-none absolute inset-y-0 right-3 z-[2] flex items-center gap-1.5 opacity-0 transition-opacity group-focus-within:pointer-events-auto group-focus-within:opacity-100 group-hover:pointer-events-auto group-hover:opacity-100 max-md:hidden">
                        {actions}
                    </span>
                )}
            </div>
            {tap && open && (
                <div id={panelId} className="flex items-center gap-2 border-b border-row-line bg-inset px-4 pt-0.5 pb-3">
                    <PanelCtx.Provider value={true}>{actions}</PanelCtx.Provider>
                    {to ? (
                        <Link to={to} className={openLink}>{t("actions.open")}</Link>
                    ) : href ? (
                        <a href={href} target="_blank" rel={EXTERNAL_REL} className={openLink}>{t("actions.open")}</a>
                    ) : null}
                </div>
            )}
        </>
    );
}

/** Inner interactive element of a Row: sits above the row's stretched link. */
export const INNER = "relative z-[1]";

/** «Купить в боте» + «Следить» for a token row. */
export function TokenActions({ token, code }: { token: string; code?: string | null }) {
    const panel = React.useContext(PanelCtx);
    return (
        <>
            <BuyInBot token={token} code={code} compact={!panel} className={panel ? "h-11 min-w-0 flex-1 rounded-[10px] px-3 text-[14px] whitespace-nowrap" : undefined} />
            <WatchInBot url={watchTokenUrl(token, code)} wide={panel} className={panel ? "rounded-[10px] px-3.5 text-[14px] [&>svg]:hidden" : undefined} />
        </>
    );
}

/**
 * Links shown only in a row's tap panel (phone): a row whose small inner links are too close to
 * tap (a move's «from → to») offers them here as full-size buttons; on desktop — nothing.
 */
export function PanelLinks({ links }: { links: { label: React.ReactNode; to: string }[] }) {
    const panel = React.useContext(PanelCtx);
    if (!panel) return null;
    return (
        <>
            {links.map((l) => (
                <Link
                    key={l.to}
                    to={l.to}
                    className="inline-flex h-11 min-w-0 flex-1 items-center justify-center gap-1.5 rounded-[10px] bg-surface-2 px-3 text-[13px] font-semibold text-fg"
                >
                    {l.label}
                </Link>
            ))}
        </>
    );
}

/** «Следить» за кошельком (creator, smart wallet) in the bot's «👀 Слежка». */
export function WalletActions({ account, code }: { account: string; code?: string | null }) {
    const panel = React.useContext(PanelCtx);
    return <WatchInBot url={watchWalletUrl(account, code)} wide={panel} />;
}

export function WatchInBot({ url, wide, className }: { url: string; wide?: boolean; className?: string }) {
    const { t } = useTranslation();
    return (
        <a
            href={url}
            target="_blank"
            rel="noopener noreferrer"
            onClick={(e) => {
                e.stopPropagation();
                if (openTelegramUrl(url)) e.preventDefault();
            }}
            title={wide ? undefined : t("actions.watch")}
            className={cn(
                "inline-flex shrink-0 items-center justify-center gap-2 rounded-[9px] bg-surface-2 font-semibold text-fg hover:bg-line",
                wide ? "h-9 px-3.5 text-[13px] pointer-coarse:h-11" : "size-8 pointer-coarse:size-11",
                className,
            )}
        >
            <Eye className={wide ? "size-[18px]" : "size-4"} aria-hidden="true" />
            {wide ? t("actions.watch") : <span className="sr-only">{t("actions.watch")}</span>}
        </a>
    );
}
