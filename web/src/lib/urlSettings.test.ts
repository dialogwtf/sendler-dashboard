import { describe, expect, it } from "vitest";
import { paramName, parseSettings, writeSettings } from "./urlSettings";

const defaults = { pad: "all", hideFeeFarm: true, minCap: "0", minNear: 0 };
const choices = { pad: ["all", "nearly", "justhoot"], minCap: ["0", "5000"] } as const;

describe("settings in the address", () => {
    it("names params in snake_case", () => expect(paramName("hideFeeFarm")).toBe("hide_fee_farm"));

    it("reads known values and keeps defaults for the rest", () => {
        const s = parseSettings(new URLSearchParams("pad=nearly&hide_fee_farm=0&min_near=0.5"), defaults, choices);
        expect(s).toEqual({ pad: "nearly", hideFeeFarm: false, minCap: "0", minNear: 0.5 });
    });

    it("drops values outside the choices and of the wrong type", () => {
        const s = parseSettings(new URLSearchParams("pad=evil&min_cap=7&hide_fee_farm=maybe&min_near=-1"), defaults, choices);
        expect(s).toEqual(defaults);
    });

    it("writes only what differs from the defaults and keeps foreign params", () => {
        const sp = writeSettings(new URLSearchParams("ref=x&pad=nearly"), { ...defaults, pad: "all", minCap: "5000", hideFeeFarm: false }, defaults);
        expect(sp.toString()).toBe("ref=x&hide_fee_farm=0&min_cap=5000");
    });

    it("round-trips", () => {
        const s = { pad: "justhoot", hideFeeFarm: false, minCap: "5000", minNear: 1 };
        expect(parseSettings(writeSettings(new URLSearchParams(), s, defaults), defaults, choices)).toEqual(s);
    });
});
