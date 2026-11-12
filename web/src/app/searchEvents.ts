/** The search dialog listens for this; the phone's bottom tab «Поиск» fires it. */
export const OPEN_SEARCH_EVENT = "dash:search";

export function openSearch() {
    window.dispatchEvent(new Event(OPEN_SEARCH_EVENT));
}
