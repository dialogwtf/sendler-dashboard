/** Theme: dark by default, light on request. Stored per device; applied as data-theme on <html>. */
export type Theme = "dark" | "light";

const KEY = "dash:theme";

export function storedTheme(): Theme {
    try {
        return localStorage.getItem(KEY) === "light" ? "light" : "dark";
    } catch {
        return "dark";
    }
}

export function applyTheme(theme: Theme) {
    document.documentElement.dataset.theme = theme;
    document.querySelector('meta[name="theme-color"]')?.setAttribute("content", theme === "light" ? "#F5F7FA" : "#0B0E14");
}

export function setTheme(theme: Theme) {
    try {
        localStorage.setItem(KEY, theme);
    } catch { /* private mode */ }
    applyTheme(theme);
    window.dispatchEvent(new CustomEvent("dash:theme", { detail: theme }));
}
