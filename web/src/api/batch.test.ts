import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Batcher } from "./batch";

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe("Batcher", () => {
    it("one call for ids requested in the same tick, duplicates merged", async () => {
        const fetchMany = vi.fn(async (ids: string[]) => Object.fromEntries(ids.map((id) => [id, id.toUpperCase()])));
        const b = new Batcher<string>(fetchMany);
        const p = Promise.all([b.load("a"), b.load("b"), b.load("a")]);
        await vi.advanceTimersByTimeAsync(20);
        expect(await p).toEqual(["A", "B", "A"]);
        expect(fetchMany).toHaveBeenCalledTimes(1);
        expect(fetchMany.mock.calls[0][0]).toEqual(["a", "b"]);
    });

    it("splits into chunks of max ids", async () => {
        const fetchMany = vi.fn(async (ids: string[]) => Object.fromEntries(ids.map((id) => [id, 1])));
        const b = new Batcher<number>(fetchMany, 100);
        const ps = Array.from({ length: 250 }, (_, i) => b.load(`t${i}`));
        await vi.advanceTimersByTimeAsync(20);
        await Promise.all(ps);
        expect(fetchMany.mock.calls.map((c) => c[0].length)).toEqual([100, 100, 50]);
    });

    it("unknown id resolves to null, an error rejects its chunk", async () => {
        const b = new Batcher<string>(async () => ({}));
        const p = b.load("x");
        await vi.advanceTimersByTimeAsync(20);
        expect(await p).toBeNull();
        const bad = new Batcher<string>(async () => { throw new Error("boom"); });
        const q = bad.load("y");
        const caught = q.catch((e: Error) => e.message);
        await vi.advanceTimersByTimeAsync(20);
        expect(await caught).toBe("boom");
    });
});
