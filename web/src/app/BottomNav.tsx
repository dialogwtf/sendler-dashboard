import * as React from "react";
import { useTranslation } from "react-i18next";
import { Link, useLocation } from "react-router-dom";
import { Activity, Ellipsis, Search as SearchIcon, Send, Star, UserRound, type LucideIcon } from "lucide-react";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { botHomeUrl } from "@/lib/botLinks";
import { openTelegramUrl } from "@/lib/telegram";
import type { Theme } from "@/lib/theme";
import { useLang } from "@/lib/useLang";
import { cn } from "@/lib/utils";
import { Segmented } from "@/ui/Segmented";
import { setAppLanguage } from "./i18n";
import { openSearch } from "./searchEvents";

/** Height of the bar without the safe area: the page leaves this much room at the bottom. */
export const BOTTOM_NAV_PX = 64;

/**
 * Phone navigation: Пульс · Поиск · Избранное · Профиль · Ещё. Only what the tabs of the home don't
 * already give — «Запуски», «Умные», «Крупные», «Новые» are tabs there. «Избранное» (the watchlist)
 * and «Профиль» (sign-in through the bot) are phase 3: shown now as dimmed «скоро» placeholders, not
 * clickable. Not on the token page: it has its own pinned «Купить в боте» (MToken).
 */
export function BottomNav({ theme, onTheme }: { theme: Theme; onTheme: (t: Theme) => void }) {
    const { t } = useTranslation();
    const { pathname } = useLocation();
    const [more, setMore] = React.useState(false);
    if (pathname.startsWith("/token/")) return null;
    const item = "flex h-full min-w-0 flex-1 flex-col items-center justify-center gap-[3px] text-[10.5px] font-semibold";
    const on = "text-fg";
    const off = "text-fg-3 hover:text-fg-2";
    return (
        <>
            <nav
                aria-label={t("nav.sections")}
                className="fixed inset-x-0 bottom-0 z-30 flex border-t border-surface-2 bg-inset px-1 pb-[var(--safe-bottom)] md:hidden"
                style={{ height: `calc(${BOTTOM_NAV_PX}px + var(--safe-bottom))` }}
            >
                <Link to="/" className={cn(item, pathname === "/" ? on : off)} aria-current={pathname === "/" ? "page" : undefined}>
                    <Activity className="size-[22px]" aria-hidden="true" />
                    {t("nav.pulse")}
                </Link>
                <button type="button" onClick={openSearch} className={cn(item, off)}>
                    <SearchIcon className="size-[22px]" aria-hidden="true" />
                    {t("nav.search")}
                </button>
                <Soon icon={Star} label={t("nav.watchlist")} className={item} />
                <Soon icon={UserRound} label={t("nav.profile")} className={item} />
                <button type="button" onClick={() => setMore(true)} className={cn(item, more ? on : off)} aria-haspopup="dialog">
                    <Ellipsis className="size-[22px]" aria-hidden="true" />
                    {t("nav.more")}
                </button>
            </nav>
            <MoreSheet open={more} onOpenChange={setMore} theme={theme} onTheme={onTheme} />
        </>
    );
}

/** A section that comes later: in its place, dimmed, with a «скоро» tag; not a link or a button. */
function Soon({ icon: Icon, label, className }: { icon: LucideIcon; label: string; className: string }) {
    const { t } = useTranslation();
    return (
        <span className={cn(className, "relative cursor-default text-fg-3/55 select-none")} aria-disabled="true">
            <span className="relative">
                <Icon className="size-[22px]" aria-hidden="true" />
                <span className="absolute -top-1.5 left-[15px] rounded-full bg-surface-2 px-1 py-px text-[8.5px] leading-none font-bold tracking-wide text-fg-3 uppercase" aria-hidden="true">
                    {t("nav.soonTag")}
                </span>
            </span>
            {label}
            <span className="sr-only">{`— ${t("pages.soon")}`}</span>
        </span>
    );
}

function MoreSheet({ open, onOpenChange, theme, onTheme }: { open: boolean; onOpenChange: (o: boolean) => void; theme: Theme; onTheme: (t: Theme) => void }) {
    const { t } = useTranslation();
    const lang = useLang();
    const bot = botHomeUrl();
    return (
        <Sheet open={open} onOpenChange={onOpenChange}>
            <SheetContent side="bottom" className="pb-[var(--safe-bottom)]">
                <SheetHeader>
                    <SheetTitle>{t("nav.more")}</SheetTitle>
                    <SheetDescription className="sr-only">{t("more.hint")}</SheetDescription>
                </SheetHeader>
                <div className="flex flex-col gap-4 overflow-y-auto px-4 pb-6">
                    <div className="flex items-center justify-between gap-3">
                        <span className="text-[13px] text-fg-2">{t("nav.language")}</span>
                        <Segmented
                            label={t("nav.language")}
                            value={lang}
                            onChange={(l) => setAppLanguage(l)}
                            options={[{ value: "ru", label: "Русский" }, { value: "en", label: "English" }]} /* i18n-ok: language names in themselves */
                        />
                    </div>
                    <div className="flex items-center justify-between gap-3">
                        <span className="text-[13px] text-fg-2">{t("nav.theme")}</span>
                        <Segmented
                            label={t("nav.theme")}
                            value={theme}
                            onChange={onTheme}
                            options={[{ value: "dark", label: t("nav.themeDark") }, { value: "light", label: t("nav.themeLight") }]}
                        />
                    </div>
                    <a
                        href={bot}
                        target="_blank"
                        rel="noopener noreferrer"
                        onClick={(e) => {
                            if (openTelegramUrl(bot)) e.preventDefault();
                        }}
                        className="flex h-12 items-center justify-center gap-2 rounded-xl bg-brand text-[15px] font-bold text-brand-fg"
                    >
                        <Send className="size-[18px]" aria-hidden="true" />
                        {t("more.openBot")}
                    </a>
                </div>
            </SheetContent>
        </Sheet>
    );
}
