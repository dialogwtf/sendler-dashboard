import { describe, expect, it } from "vitest";
import type { Creator, CreatorHistory, Launch } from "@/api/types";
import { launchLines } from "./lines";

const hist = (token: string, started: string | number): CreatorHistory => ({
    token, pad: "nearly", started, dumped: false, first_sell_s: null, sold_share: null, sold_near: null,
});
const launch = (token: string, started: string | null): Launch => ({ token, started, pad: "nearly", symbol: token.toUpperCase() } as Launch);
const creator = (history: CreatorHistory[], recent: Launch[]): Creator => ({
    account: "dev.near", launches: history.length, dumped: 0, history, recent, links: [], blocked_by: 0, blocked_reasons: [],
});

describe("account launches", () => {
    it("joins the dev stats and the dashboard launch of one token, newest first", () => {
        const lines = launchLines(creator(
            [hist("a", "2026-11-01T00:00:00Z"), hist("b", 1_762_000_000)],
            [launch("a", "2026-11-01T00:00:01Z"), launch("c", "2026-11-09T00:00:00Z")],
        ));
        expect(lines.map((l) => l.token)).toEqual(["c", "a", "b"]);
        const a = lines.find((l) => l.token === "a")!;
        expect(a.launch?.symbol).toBe("A");
        expect(a.stats?.token).toBe("a");
    });

    it("keeps the stats' start when the launch has none", () => {
        const [a] = launchLines(creator([hist("a", "2026-11-01T00:00:00Z")], [launch("a", null)]));
        expect(a.started).toBe("2026-11-01T00:00:00Z");
    });
});
