import { describe, expect, it } from "vitest";
import { normalizePrefs, PHONE_TABS, visibleTabs } from "./phone";

const ALL = PHONE_TABS.map((t) => t.id);

describe("phone tab prefs", () => {
    it("garbage → the default order", () => {
        expect(normalizePrefs(null)).toEqual({ order: ALL, hidden: [] });
        expect(normalizePrefs({ order: "x", hidden: 5 })).toEqual({ order: ALL, hidden: [] });
    });

    it("unknown and repeated ids dropped, missing tabs appended", () => {
        const p = normalizePrefs({ order: ["top", "gone", "top", "now"], hidden: ["gone", "movers"] });
        expect(p.order.slice(0, 2)).toEqual(["top", "now"]);
        expect(p.order).toHaveLength(ALL.length);
        expect(p.hidden).toEqual(["movers"]);
    });

    it("hiding every tab shows them all", () => {
        expect(normalizePrefs({ order: ALL, hidden: ALL }).hidden).toEqual([]);
    });

    it("visible tabs follow the order without the hidden", () => {
        const ids = visibleTabs(normalizePrefs({ order: ["smart", "now"], hidden: ["now"] })).map((t) => t.id);
        expect(ids[0]).toBe("smart");
        expect(ids).not.toContain("now");
        expect(ids).toHaveLength(ALL.length - 1);
    });
});
