import { describe, expect, it } from "vitest";
import type { Launch, Move, NewItem, SmartTrade } from "@/api/types";
import { dropHops, isBridgeMove, isHubMove, mergeNow, NOW_MOVE_MIN_USD } from "./index";

const launch = (token: string, started: string | null, fee_farm: Launch["fee_farm"] = false) =>
    ({ token, started, fee_farm, pad: "nearly" }) as Launch;
const smart = (id: string, ts: string, near: number) => ({ _id: id, ts, near, side: "buy", token: "t" }) as SmartTrade;
const move = (tx: string, ts: string, usd = 500_000) => ({ _id: tx, tx, ts, usd, from: "a", to: "b", amount: "1" }) as Move;
const item = (id: string, kind: NewItem["kind"], first_seen: string) => ({ _id: `${kind}:${id}`, id, kind, first_seen }) as NewItem;

describe("mergeNow", () => {
    const src = {
        launches: [launch("L1", "2026-11-11T10:00:05Z"), launch("FARM", "2026-11-11T10:00:09Z", true), launch("NOSTART", null)],
        smart: [smart("S1", "2026-11-11T10:00:04Z", 1), smart("DUST", "2026-11-11T10:00:08Z", 0.1)],
        moves: [move("M1", "2026-11-11T10:00:07Z")],
        news: [item("T1", "ft", "2026-11-11T10:00:06Z"), item("C1", "contract", "2026-11-11T10:00:10Z")],
    };

    it("newest first across sources, noise left out", () => {
        expect(mergeNow(src).map((x) => x.key)).toEqual(["m:M1", "n:ft:T1", "l:L1", "s:S1"]);
    });

    it("a filter keeps one kind", () => {
        expect(mergeNow(src, "smart").map((x) => x.key)).toEqual(["s:S1"]);
        expect(mergeNow(src, "launch").map((x) => x.kind)).toEqual(["launch"]);
    });

    it("caps the feed and orders equal times by key", () => {
        const same = { moves: [move("B", "2026-11-11T10:00:00Z"), move("A", "2026-11-11T10:00:00Z"), move("C", "2026-11-11T10:00:00Z")] };
        expect(mergeNow(same, "all", 2).map((x) => x.key)).toEqual(["m:A", "m:B"]);
    });

    it("bridge traffic stays out, other big moves stay in", () => {
        const at = "2026-11-11T10:00:00Z";
        const m = (from: string, to: string, extra: Partial<Move> = {}) => ({ ...move(`${from}>${to}`, at), from, to, ...extra }) as Move;
        expect(isBridgeMove(m("intents.near", "omni.bridge.near"))).toBe(true);
        expect(isBridgeMove(m("omni.bridge.near", "btc-connector.bridge.near"))).toBe(true);
        expect(isBridgeMove(m("zcash-connector.bridge.near", "alice.near"))).toBe(true);
        expect(isBridgeMove(m("alice.near", "x.near", { to_label: { label: "Some bridge", kind: "bridge" } as Move["to_label"] }))).toBe(true);
        expect(isBridgeMove(m("alice.near", "bob.near"))).toBe(false);
        expect(isBridgeMove(m("bridge.near", "notabridge.near"))).toBe(false);
        const rows = mergeNow({ moves: [m("intents.near", "omni.bridge.near"), m("alice.near", "bob.near")] });
        expect(rows.map((x) => x.key)).toEqual(["m:alice.near>bob.near"]);
    });

    it("moves: only from the feed's threshold, without hubs and exchanges", () => {
        const at = "2026-11-11T10:00:00Z";
        const hub = { label: null, kind: "hub" } as Move["from_label"];
        const exchange = { label: "Some exchange", kind: "exchange" } as Move["to_label"];
        const small = move("SMALL", at, NOW_MOVE_MIN_USD - 1);
        const edge = move("EDGE", at, NOW_MOVE_MIN_USD);
        const fromHub = { ...move("HUB", at), from_label: hub } as Move;
        const toExchange = { ...move("EXCH", at), to_label: exchange } as Move;
        expect(isHubMove(fromHub)).toBe(true);
        expect(isHubMove(toExchange)).toBe(true);
        expect(isHubMove(edge)).toBe(false);
        expect(mergeNow({ moves: [small, edge, fromHub, toExchange] }).map((x) => x.key)).toEqual(["m:EDGE"]);
    });

    it("a chain of hops of the same money shows once — its first move", () => {
        const m = (tx: string, ts: string, from: string, to: string, usd: number, token = "near") =>
            ({ ...move(tx, ts, usd), from, to, token }) as Move;
        const rows = [
            m("A", "2026-11-11T10:00:00Z", "whale", "w1", 1_800_000),
            m("B", "2026-11-11T10:03:00Z", "w1", "w2", 1_790_000), // hop: within 3 %
            m("C", "2026-11-11T10:05:00Z", "w2", "w3", 1_795_000), // hop of the hop
            m("D", "2026-11-11T10:06:00Z", "w1", "x", 400_000), // another sum: a move of its own
            m("E", "2026-11-11T11:10:00Z", "w3", "y", 1_795_000), // over 30 min later: its own
            m("F", "2026-11-11T10:07:00Z", "w2", "z", 1_790_000, "usdt"), // another token
        ];
        const at = (ts: string) => Date.parse(ts);
        expect(dropHops(rows, at).map((x) => x._id)).toEqual(["A", "D", "E", "F"]);
        // mergeNow applies it: newest first, hops gone
        expect(mergeNow({ moves: rows }).map((x) => x.key)).toEqual(["m:E", "m:F", "m:D", "m:A"]);
    });
});
