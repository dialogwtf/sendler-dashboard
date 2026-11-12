import { useSyncExternalStore } from "react";
import { serverNow } from "./clock";

/** One shared 1-second tick for every age on screen («12 с», «данные 2 мин назад»). */
let now = serverNow();
const listeners = new Set<() => void>();
let timer: ReturnType<typeof setInterval> | null = null;

function subscribe(fn: () => void) {
    listeners.add(fn);
    if (!timer) {
        timer = setInterval(() => {
            now = serverNow();
            for (const l of listeners) l();
        }, 1000);
    }
    return () => {
        listeners.delete(fn);
        if (!listeners.size && timer) {
            clearInterval(timer);
            timer = null;
        }
    };
}

/** Server-clock now (ms), re-rendering once a second. */
export function useNow(): number {
    return useSyncExternalStore(subscribe, () => now);
}
