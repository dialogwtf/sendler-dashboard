import sdkUrl from "@/vendor/telegram-web-app.js?url";

/**
 * Telegram Mini App. The site is also a Mini App of the sniper bot; inside Telegram it opens full
 * screen on phones, keeps the page from closing on a downward swipe, paints Telegram's own bars in
 * the page colours, uses Telegram's back button and keeps its header out from under Telegram's
 * controls (the SDK writes --tg-safe-area-inset-* / --tg-content-safe-area-inset-*, index.css adds
 * them up as --safe-top / --safe-bottom).
 *
 * The SDK is Telegram's own telegram-web-app.js, kept in src/vendor (Vite gives it a hashed name
 * under /assets/, so the CSP's 'self' covers it and it is cached for good). It is loaded only when
 * the page was opened by Telegram — a site visitor never downloads it. To update it: download
 * https://telegram.org/js/telegram-web-app.js over src/vendor/telegram-web-app.js.
 */

type WebApp = Window["Telegram"]["WebApp"];

/** Waiting longer than this for the SDK = show the page without it (links then open as links). */
const SDK_TIMEOUT_MS = 2500;
/** Platforms where full screen is the phone's screen; on desktop it would take the whole monitor. */
const PHONES = new Set(["ios", "android", "android_x"]);

let app: WebApp | null = null;

/**
 * Opened by Telegram: the launch parameters in the URL (first load), the WebView bridge (phones),
 * or the parameters the SDK kept in sessionStorage (a reload or a link inside the Mini App).
 */
export function launchedInTelegram(): boolean {
    if (typeof window === "undefined") return false;
    if ((window as Window & { TelegramWebviewProxy?: unknown }).TelegramWebviewProxy !== undefined) return true;
    if (/[#&?]tgWebApp/.test(window.location.hash + window.location.search)) return true;
    try {
        return sessionStorage.getItem("__telegram__initParams") !== null;
    } catch {
        return false;
    }
}

function loadSdk(): Promise<void> {
    return new Promise((resolve) => {
        const s = document.createElement("script");
        s.src = sdkUrl;
        const timer = setTimeout(resolve, SDK_TIMEOUT_MS);
        s.onload = s.onerror = () => {
            clearTimeout(timer);
            resolve();
        };
        document.head.appendChild(s);
    });
}

/** Telegram's bars in the page colours (theme tokens of index.css), for both themes. */
function paint(tg: WebApp) {
    const css = getComputedStyle(document.documentElement);
    const bg = css.getPropertyValue("--bg").trim();
    const bar = css.getPropertyValue("--inset").trim();
    try {
        if (bg && tg.isVersionAtLeast("6.1")) {
            tg.setHeaderColor(bg);
            tg.setBackgroundColor(bg);
        }
        if (bar && tg.isVersionAtLeast("7.10")) tg.setBottomBarColor(bar);
    } catch { /* an older client refuses a colour: it keeps its own */ }
}

/**
 * Before the first render: load the SDK (only inside Telegram) and set the Mini App up. Never
 * throws and never waits more than SDK_TIMEOUT_MS — the page works without it.
 */
export async function initTelegram(): Promise<void> {
    if (!launchedInTelegram()) return;
    await loadSdk();
    const tg = window.Telegram?.WebApp;
    // The SDK also runs in a plain tab that kept the parameters; there the platform is «unknown».
    if (!tg || !tg.platform || tg.platform === "unknown") return;
    app = tg;
    document.documentElement.dataset.tg = tg.platform;
    try {
        tg.ready();
        tg.expand();
        // The lists scroll inside the page: a downward swipe must scroll them, not close the app.
        if (tg.isVersionAtLeast("7.7")) tg.disableVerticalSwipes();
        if (PHONES.has(tg.platform) && tg.isVersionAtLeast("8.0") && !tg.isFullscreen) tg.requestFullscreen();
    } catch { /* an old client: the page stays as Telegram opened it */ }
    paint(tg);
    window.addEventListener("dash:theme", () => paint(tg));
}

/** Telegram's back button (in its header / full-screen bar) stands in for the page's own. */
export function telegramBack(): WebApp["BackButton"] | null {
    return app && app.isVersionAtLeast("6.1") ? app.BackButton : null;
}

/**
 * Open a t.me link inside the Telegram Mini App through openTelegramLink (a plain link would
 * leave Telegram). Returns false outside Telegram — the caller's <a href target=_blank> then
 * opens it as an ordinary link. (Mini App branch from near-nft-market src/lib/telegramBot.ts;
 * its window.open branch is not copied: with "noopener" window.open returns null, which made
 * the link open twice.)
 */
export function openTelegramUrl(url: string): boolean {
    const tg = typeof window !== "undefined" ? window.Telegram?.WebApp : undefined;
    if (tg?.openTelegramLink && tg.initData) {
        tg.openTelegramLink(url);
        return true;
    }
    return false;
}
