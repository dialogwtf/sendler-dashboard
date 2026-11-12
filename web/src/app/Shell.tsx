import * as React from "react";
import { useTranslation } from "react-i18next";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { Moon, Sun } from "lucide-react";
import { setAppLanguage } from "./i18n";
import { BottomNav } from "./BottomNav";
import { PulseRow } from "./PulseRow";
import { SearchBox } from "./Search";
import { LAYOUT_IDS, setLayout, useLayoutChoice } from "@/layouts/choice";
import { useLang } from "@/lib/useLang";
import { useMediaQuery } from "@/lib/useMediaQuery";
import { setTheme, storedTheme, type Theme } from "@/lib/theme";
import { cn } from "@/lib/utils";

/** Logo mark from the boards («пульс» — a working mark, own logo is an open question). */
export function LogoMark({ size = 32 }: { size?: number }) {
    return (
        <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true">
            <rect width="32" height="32" rx="9" fill="var(--brand)" />
            <path d="M6 17h5l3-7 4 13 3-6h5" fill="none" stroke="var(--brand-fg)" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
    );
}

function useTheme(): [Theme, (t: Theme) => void] {
    const [theme, set] = React.useState<Theme>(storedTheme);
    return [theme, (t) => { setTheme(t); set(t); }];
}

/**
 * App frame: the desktop header of the Main board — logo → layout tabs → search → language →
 * theme, then the network pulse row. No «Подключить кошелёк», «+ Своя», «Киты», «NFT» in phase 2.
 */
export function Shell({ children }: { children: React.ReactNode }) {
    const { t } = useTranslation();
    const lang = useLang();
    const [theme, applyTheme] = useTheme();
    const layout = useLayoutChoice();
    const { pathname } = useLocation();
    const navigate = useNavigate();
    // Phone home (plan step 12): a fixed-height column — the tabs' widget scrolls, the page does not.
    const fixed = pathname === "/";
    const phone = useMediaQuery("(max-width: 767.98px)");

    return (
        // Telegram full screen: the page starts below the status bar and Telegram's buttons (--safe-top).
        <div className={cn("min-h-dvh bg-bg pt-[var(--safe-top)]", fixed && "max-md:flex max-md:h-dvh max-md:flex-col")}>
            <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-50 focus:rounded-lg focus:bg-surface focus:px-3 focus:py-2">
                {t("nav.skip")}
            </a>
            {/* phone: the token page has its own header (MToken) */}
            <header className={cn("shrink-0 border-b border-surface-2 bg-bg", pathname.startsWith("/token/") && "max-md:hidden")}>
                <div className="mx-auto flex max-w-[1440px] flex-wrap items-center gap-x-4 gap-y-3 px-4 py-2 sm:px-6 md:py-3.5">
                    <Link to="/" className="flex min-h-11 items-center gap-2.5 text-fg" aria-label={`${t("app.name")} — ${t("nav.home")}`}>
                        <LogoMark />
                        <span className="font-display text-[19px] font-bold tracking-[-0.01em]">Sendler Dash</span>
                    </Link>
                    {/* desktop and tablet only: the phone home has its own tabs instead of layouts */}
                    <nav
                        aria-label={t("nav.layouts")}
                        className="ml-2 flex w-fit gap-0.5 rounded-lg bg-inset p-0.5 max-md:hidden"
                    >
                        {LAYOUT_IDS.map((id) => {
                            const on = pathname === "/" && layout === id;
                            return (
                                <button
                                    key={id}
                                    type="button"
                                    aria-pressed={on}
                                    onClick={() => {
                                        setLayout(id);
                                        if (pathname !== "/") navigate("/");
                                    }}
                                    className={cn(
                                        "rounded-md px-2.5 py-[5px] text-[12px] font-medium pointer-coarse:min-h-11",
                                        on ? "bg-line text-fg" : "text-fg-2 hover:text-fg",
                                    )}
                                >
                                    {t(`nav.${id}`)}
                                </button>
                            );
                        })}
                    </nav>
                    <span className="flex-1" />
                    {phone && <PulseRow compact />}
                    <SearchBox />
                    <button
                        type="button"
                        onClick={() => setAppLanguage(lang === "ru" ? "en" : "ru")}
                        className="grid h-9 min-w-9 place-items-center rounded-lg px-2 text-[12px] font-semibold text-fg-2 hover:bg-surface-2 hover:text-fg max-md:hidden"
                        aria-label={`${t("nav.language")}: ${lang === "ru" ? "English" : "Русский"}`} /* i18n-ok: language names in themselves */
                    >
                        {lang === "ru" ? "RU" : "EN"}
                    </button>
                    <button
                        type="button"
                        onClick={() => applyTheme(theme === "dark" ? "light" : "dark")}
                        className="grid size-9 place-items-center rounded-lg text-fg-2 hover:bg-surface-2 hover:text-fg max-md:hidden"
                        aria-label={`${t("nav.theme")}: ${theme === "dark" ? t("nav.themeLight") : t("nav.themeDark")}`}
                    >
                        {theme === "dark" ? <Moon className="size-[18px]" aria-hidden="true" /> : <Sun className="size-[18px]" aria-hidden="true" />}
                    </button>
                </div>
                {!phone && <PulseRow />}
            </header>
            {/* phone: room for the bottom tabs (BottomNav, 64 px + the safe area) */}
            <main
                id="main"
                className={cn(
                    "mx-auto max-w-[1440px] px-4 pt-5 pb-[calc(88px+var(--safe-bottom))] sm:px-6 md:pb-10",
                    fixed && "max-md:flex max-md:min-h-0 max-md:w-full max-md:flex-1 max-md:flex-col max-md:p-0 max-md:pb-[calc(64px+var(--safe-bottom))]",
                )}
            >
                {children}
            </main>
            <BottomNav theme={theme} onTheme={applyTheme} />
        </div>
    );
}
