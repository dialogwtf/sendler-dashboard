import * as React from "react";

/**
 * Widget settings per placed widget, kept in localStorage (phase 2). The same shape moves to
 * the server in phase 3. Unknown or broken stored values fall back to the defaults key by key.
 */
const PREFIX = "dash:w:";

function read<S extends Record<string, unknown>>(key: string, defaults: S): S {
    try {
        const raw = localStorage.getItem(PREFIX + key);
        if (!raw) return defaults;
        const stored = JSON.parse(raw) as Record<string, unknown>;
        const out: Record<string, unknown> = { ...defaults };
        for (const k of Object.keys(defaults)) {
            const v = stored[k];
            const d = defaults[k];
            if (v !== undefined && (Array.isArray(d) ? Array.isArray(v) : typeof v === typeof d)) out[k] = v;
        }
        return out as S;
    } catch {
        return defaults;
    }
}

/** Every mounted user of a key hears its changes (the phone tab's badge reads the widget's filters). */
const listeners = new Map<string, Set<(v: Record<string, unknown>) => void>>();

export function useWidgetSettings<S extends Record<string, unknown>>(key: string, defaults: S): [S, (patch: Partial<S>) => void] {
    const [settings, set] = React.useState<S>(() => read(key, defaults));
    React.useEffect(() => {
        const fn = (v: Record<string, unknown>) => set(v as S);
        let subs = listeners.get(key);
        if (!subs) listeners.set(key, (subs = new Set()));
        subs.add(fn);
        return () => {
            subs.delete(fn);
            if (!subs.size) listeners.delete(key);
        };
    }, [key]);
    const update = React.useCallback(
        (patch: Partial<S>) => {
            set((prev) => {
                const next = { ...prev, ...patch };
                try {
                    localStorage.setItem(PREFIX + key, JSON.stringify(next));
                } catch { /* private mode */ }
                // Others after this render: a state update of another component is not allowed inside.
                queueMicrotask(() => {
                    for (const fn of listeners.get(key) ?? []) fn(next);
                });
                return next;
            });
        },
        [key],
    );
    return [settings, update];
}
