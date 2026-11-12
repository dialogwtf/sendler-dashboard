import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

/** WCAG contrast of the colour roles, read straight from index.css. */
const css = readFileSync(new URL("../index.css", import.meta.url), "utf8");

function block(selector: string): Record<string, string> {
    const start = css.indexOf(selector);
    const body = css.slice(css.indexOf("{", start) + 1, css.indexOf("}", start));
    const vars: Record<string, string> = {};
    for (const m of body.matchAll(/--([\w-]+):\s*(#[0-9A-Fa-f]{6})/g)) vars[m[1]] = m[2];
    return vars;
}

function lum(hex: string): number {
    const c = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
        .map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
}

export function contrast(a: string, b: string): number {
    const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p);
    return (x + 0.05) / (y + 0.05);
}

const TEXT_ROLES = ["fg", "fg-2", "fg-3", "brand-text", "up-fg", "down-fg", "amber-fg", "brand-soft-fg"];

describe.each([
    ["dark", ':root,\n[data-theme="dark"]'],
    ["light", '[data-theme="light"]'],
])("%s theme", (_name, selector) => {
    const v = block(selector);
    it.each(TEXT_ROLES)("%s reads ≥ 4.5:1 on background and surfaces", (role) => {
        for (const ground of ["bg", "surface", "surface-2"]) {
            expect(contrast(v[role], v[ground]), `${role} on ${ground}`).toBeGreaterThanOrEqual(4.5);
        }
    });
    it("text on an accent fill", () => expect(contrast(v["brand-fg"], v.brand)).toBeGreaterThanOrEqual(4.5));
});
