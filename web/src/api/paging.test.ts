import { describe, expect, it } from "vitest";
import { cursorAfter, hasMoreAfter, mergeRows } from "./paging";

type R = { k: string; ts: string };
const r = (k: string, ts = "2026-11-10T12:00:00.000Z"): R => ({ k, ts });
const key = (x: R) => x.k;

describe("older pages", () => {
    it("cursor is 1 ms after the last row (rows of one block share a time)", () => {
        expect(cursorAfter("2026-11-10T12:00:00.000Z")).toBe("2026-11-10T12:00:00.001Z");
        expect(cursorAfter("2026-11-10T12:00:00Z")).toBe("2026-11-10T12:00:00.001Z");
        expect(cursorAfter(1_762_776_000)).toBe(new Date(1_762_776_000_000 + 1).toISOString());
        expect(cursorAfter(null)).toBeNull();
        expect(cursorAfter("not a date")).toBeNull();
    });

    it("merges head and pages without repeats, head first", () => {
        const head = [r("a"), r("b")];
        expect(mergeRows(head, [[r("b"), r("c")], [r("c"), r("d")]], key).map(key)).toEqual(["a", "b", "c", "d"]);
    });

    it("a short page ends the list", () => {
        expect(hasMoreAfter([[r("c")]], [r("a")], 2, key)).toBe(false);
        expect(hasMoreAfter([[r("c"), r("d")]], [r("a")], 2, key)).toBe(true);
    });

    it("a full page of repeats ends the list instead of looping", () => {
        expect(hasMoreAfter([[r("a"), r("b")]], [r("a"), r("b")], 2, key)).toBe(false);
    });

    it("before the first page there may be more", () => expect(hasMoreAfter([], [r("a")], 2, key)).toBe(true));
});
