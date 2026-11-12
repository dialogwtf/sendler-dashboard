import { describe, expect, it } from "vitest";
import { base32, botCode, buyInBotUrl, sha256, tokenCode, watchTokenUrl, watchWalletUrl } from "./botLinks";

const hex = (b: Uint8Array) => [...b].map((x) => x.toString(16).padStart(2, "0")).join("");

describe("sha256", () => {
    it("empty", () =>
        expect(hex(sha256(new Uint8Array()))).toBe("e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"));
    it("abc", () =>
        expect(hex(sha256(new TextEncoder().encode("abc")))).toBe(
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"));
    it("two blocks", () => {
        const s = "abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq";
        expect(hex(sha256(new TextEncoder().encode(s)))).toBe(
            "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1");
    });
});

describe("base32", () => {
    it("RFC 4648 vectors, lower case without padding", () => {
        const enc = (s: string) => base32(new TextEncoder().encode(s));
        expect(enc("f")).toBe("my");
        expect(enc("fo")).toBe("mzxq");
        expect(enc("foobar")).toBe("mzxw6ytboi");
    });
});

describe("token code — same as indexer/services/dashboard/botlinks.py token_code", () => {
    // Reference values printed by the bot's own token_code().
    it.each([
        ["sinu-8.smithii.near", "ygtqlwlohs"],
        ["qcat-3.nearlytrade.near", "f5citwwufm"],
        ["wrap.near", "wvoesc5pxa"],
    ])("%s", (token, code) => expect(tokenCode(token)).toBe(code));

    it("account code — /creators bot_code", () => expect(botCode("scorp56.near")).toBe("htesz7a56y"));

    it("links carry the __dash source mark", () => {
        expect(buyInBotUrl("wrap.near")).toBe("https://t.me/sendler_trade_bot?start=t_wvoesc5pxa__dash");
        expect(watchTokenUrl("wrap.near")).toBe("https://t.me/sendler_trade_bot?start=wt_wvoesc5pxa__dash");
        expect(watchWalletUrl("scorp56.near")).toBe("https://t.me/sendler_trade_bot?start=ww_htesz7a56y__dash");
    });

    it("bot_code from the API wins", () =>
        expect(buyInBotUrl("wrap.near", "fromapi123")).toBe("https://t.me/sendler_trade_bot?start=t_fromapi123__dash"));
});
