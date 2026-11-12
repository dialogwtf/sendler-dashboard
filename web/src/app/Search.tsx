import * as React from "react";
import { useTranslation } from "react-i18next";
import { Search as SearchIcon } from "lucide-react";
import { OPEN_SEARCH_EVENT } from "./searchEvents";

const SearchDialog = React.lazy(() => import("./SearchDialog"));

/**
 * Header search (Main board): one field — ticker, token contract or account. Ctrl K / ⌘K opens it,
 * and on the phone the bottom tab «Поиск» (the header button is hidden there).
 * Results come grouped (tokens, accounts) from GET /search; an exact address also offers to open
 * it directly, even when the API knows nothing about it yet. The dialog itself is a lazy chunk,
 * mounted on the first open and kept for the close animation.
 */
export function SearchBox() {
    const { t } = useTranslation();
    const [open, setOpen] = React.useState(false);
    const [mounted, setMounted] = React.useState(false);
    const show = React.useCallback((next: boolean | ((o: boolean) => boolean)) => {
        setMounted(true);
        setOpen(next);
    }, []);

    React.useEffect(() => {
        const onKey = (e: KeyboardEvent) => {
            if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
                e.preventDefault();
                show((o) => !o);
            }
        };
        const onOpen = () => show(true);
        window.addEventListener("keydown", onKey);
        window.addEventListener(OPEN_SEARCH_EVENT, onOpen);
        return () => {
            window.removeEventListener("keydown", onKey);
            window.removeEventListener(OPEN_SEARCH_EVENT, onOpen);
        };
    }, [show]);

    return (
        <>
            <button
                type="button"
                onClick={() => show(true)}
                className="flex h-10 w-[340px] max-w-full min-w-0 items-center gap-2 rounded-[10px] border border-line bg-surface px-3 text-left text-fg-3 hover:border-fg-3/40 max-md:hidden"
                aria-label={t("search.open")}
            >
                <SearchIcon className="size-4 shrink-0" aria-hidden="true" />
                <span className="flex-1 truncate">{t("search.placeholder")}</span>
                <kbd className="rounded-[5px] bg-surface-2 px-1.5 py-0.5 font-mono text-[10px] text-fg-2">Ctrl K</kbd>
            </button>
            {mounted && (
                <React.Suspense fallback={null}>
                    <SearchDialog open={open} onOpenChange={setOpen} />
                </React.Suspense>
            )}
        </>
    );
}
