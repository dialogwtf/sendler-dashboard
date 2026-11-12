import { useSyncExternalStore } from "react";

/** The chosen preset layout, per device (phase 3 moves it to the server for signed-in users). */
export type LayoutId = "memes" | "network";
export const LAYOUT_IDS: LayoutId[] = ["memes", "network"];

const KEY = "dash:layout";
const listeners = new Set<() => void>();

function read(): LayoutId {
    try {
        const v = localStorage.getItem(KEY);
        return v === "network" ? "network" : "memes";
    } catch {
        return "memes";
    }
}

let current: LayoutId = typeof window === "undefined" ? "memes" : read();

export function setLayout(id: LayoutId) {
    current = id;
    try {
        localStorage.setItem(KEY, id);
    } catch { /* private mode */ }
    for (const fn of listeners) fn();
}

export function useLayoutChoice(): LayoutId {
    return useSyncExternalStore(
        (fn) => {
            listeners.add(fn);
            return () => listeners.delete(fn);
        },
        () => current,
    );
}
