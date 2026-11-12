import * as React from "react";
import { useSearchParams } from "react-router-dom";

/**
 * Widget settings of a full-list page live in the address (`/launches?pad=nearly&min_cap=50000`),
 * so a filter survives a reload and a link carries it. Keys are the settings' names in
 * snake_case; only values that differ from the defaults are written. A value of the wrong type,
 * or outside `choices`, falls back to the default — the address is user input.
 */

export type Choices<S> = Partial<Record<keyof S, readonly string[]>>;

export function paramName(key: string): string {
    return key.replace(/[A-Z]/g, (c) => `_${c.toLowerCase()}`);
}

export function parseSettings<S extends Record<string, unknown>>(sp: URLSearchParams, defaults: S, choices?: Choices<S>): S {
    const out: Record<string, unknown> = { ...defaults };
    for (const [key, d] of Object.entries(defaults)) {
        const raw = sp.get(paramName(key));
        if (raw === null) continue;
        const allowed = choices?.[key as keyof S];
        if (typeof d === "boolean") {
            if (raw === "1" || raw === "true") out[key] = true;
            else if (raw === "0" || raw === "false") out[key] = false;
        } else if (typeof d === "number") {
            const n = Number(raw);
            if (raw.trim() !== "" && Number.isFinite(n) && n >= 0 && (!allowed || allowed.includes(raw))) out[key] = n;
        } else if (typeof d === "string") {
            if (allowed ? allowed.includes(raw) : raw.length <= 64) out[key] = raw;
        }
    }
    return out as S;
}

export function writeSettings<S extends Record<string, unknown>>(sp: URLSearchParams, next: S, defaults: S): URLSearchParams {
    const out = new URLSearchParams(sp);
    for (const [key, d] of Object.entries(defaults)) {
        const v = next[key];
        const name = paramName(key);
        if (v === d || v === undefined) out.delete(name);
        else out.set(name, typeof v === "boolean" ? (v ? "1" : "0") : String(v));
    }
    return out;
}

export function useUrlSettings<S extends Record<string, unknown>>(defaults: S, choices?: Choices<S>): [S, (patch: Partial<S>) => void] {
    const [sp, setSp] = useSearchParams();
    const settings = React.useMemo(() => parseSettings(sp, defaults, choices), [sp, defaults, choices]);
    const update = React.useCallback(
        (patch: Partial<S>) => {
            // A filter change is a new view, not a step back in history.
            setSp((prev) => writeSettings(prev, { ...parseSettings(prev, defaults, choices), ...patch }, defaults), { replace: true });
        },
        [setSp, defaults, choices],
    );
    return [settings, update];
}
