import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import type { BackendModule, ReadCallback } from "i18next";
import en from "../locales/en/common.json";
import ru from "../locales/ru/common.json";

/** The saved choice from localStorage, else English — neither Telegram's nor the browser's language is guessed. */
export const SUPPORTED_LANGS = ["ru", "en"] as const;
export type AppLanguage = (typeof SUPPORTED_LANGS)[number];

/** Written only by an explicit choice in the header — auto-detection never persists. */
export const LANG_KEY = "dash:lang";

function asAppLanguage(raw: string | null | undefined): AppLanguage | null {
    if (!raw) return null;
    const base = raw.toLowerCase().split(/[-_]/)[0];
    return (SUPPORTED_LANGS as readonly string[]).includes(base) ? (base as AppLanguage) : null;
}

export function detectLanguage(): AppLanguage {
    try {
        const stored = asAppLanguage(localStorage.getItem(LANG_KEY));
        if (stored) return stored;
    } catch { /* private mode */ }
    return "en";
}

export function setAppLanguage(lng: AppLanguage) {
    try {
        localStorage.setItem(LANG_KEY, lng);
    } catch { /* private mode */ }
    void i18n.changeLanguage(lng);
}

/**
 * `common` of both languages is in the main bundle (~9 KB gzip together): as lazy chunks they were
 * a network round trip — two, with the en fallback — before the first frame (plan step 10). Other
 * namespaces, if any appear, are lazy chunks through the backend below.
 */
const resources = { ru: { common: ru }, en: { common: en } };
const loaders = import.meta.glob<{ default: Record<string, unknown> }>(["../locales/*/*.json", "!../locales/*/common.json"]);

const viteBackend: BackendModule = {
    type: "backend",
    init() {},
    read(lng: string, ns: string, callback: ReadCallback) {
        const load = loaders[`../locales/${lng}/${ns}.json`];
        if (!load) return callback(null, {});
        load().then(
            (mod) => callback(null, mod.default),
            (err) => callback(err as Error, null),
        );
    },
};

export function initI18n(): Promise<unknown> {
    i18n.on("languageChanged", (lng) => {
        document.documentElement.lang = lng;
    });
    return i18n
        .use(viteBackend)
        .use(initReactI18next)
        .init({
            lng: detectLanguage(),
            fallbackLng: "en",
            supportedLngs: [...SUPPORTED_LANGS],
            ns: ["common"],
            defaultNS: "common",
            resources,
            partialBundledLanguages: true,
            interpolation: { escapeValue: false },
            returnEmptyString: false,
        });
}

/** The current language as format.ts wants it. */
export function langOf(lng: string | undefined): AppLanguage {
    return lng?.startsWith("ru") ? "ru" : "en";
}

export default i18n;
