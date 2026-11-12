import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { initI18n } from "@/app/i18n";
import { applyTheme, storedTheme } from "@/lib/theme";
import { initTelegram } from "@/lib/telegram";
import { App } from "@/app/App";
import { queryClient } from "@/app/providers";
import "./index.css";

applyTheme(storedTheme());

/**
 * After a deploy, a tab opened on the old build asks for chunks by their old names. The deploy
 * keeps old assets, but if one is gone anyway — reload once to get the new
 * index.html. At most once per 30 s, so a truly missing chunk can't loop the page.
 */
const RELOAD_KEY = "dash:chunk-reload";
window.addEventListener("vite:preloadError", (e) => {
    try {
        const last = Number(sessionStorage.getItem(RELOAD_KEY)) || 0;
        if (Date.now() - last < 30_000) return; // let the error reach the page boundary
        sessionStorage.setItem(RELOAD_KEY, String(Date.now()));
    } catch { /* storage blocked: reload anyway */ }
    e.preventDefault();
    window.location.reload();
});

/**
 * The splash (index.html) stays until the first screen is filled: the page has rendered and no
 * request is in flight — so nobody watches skeletons and icons pop in one by one. Never longer
 * than SPLASH_MAX_MS: on a slow network the page shows with its own loading states.
 */
const SPLASH_MAX_MS = 3500;

function liftSplash() {
    const splash = document.getElementById("splash");
    if (!splash) return;
    const t0 = performance.now();
    let calm = 0;
    const tick = () => {
        const rendered = !!document.querySelector("main section, main h1");
        calm = rendered && queryClient.isFetching() === 0 ? calm + 1 : 0;
        if (calm >= 3 || performance.now() - t0 > SPLASH_MAX_MS) {
            splash.classList.add("done");
            setTimeout(() => splash.remove(), 400);
        } else {
            setTimeout(tick, 80);
        }
    };
    tick();
}

// Dictionaries are lazy chunks: render once the active language's common namespace is in.
// Inside Telegram the Mini App is set up first (full screen, safe areas) — the splash covers it.
void Promise.allSettled([initI18n(), initTelegram()]).finally(() => {
    createRoot(document.getElementById("root")!).render(
        <StrictMode>
            <App />
        </StrictMode>,
    );
    liftSplash();
});
