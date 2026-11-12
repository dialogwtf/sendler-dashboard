/**
 * Every link into the Telegram bot is built here (plan step 8, backend Б1/Б9 done 2026-11-09):
 *
 *   «Купить в боте»          https://t.me/<bot>?start=t_<code>__dash
 *   «Следить» за токеном     …?start=wt_<code>__dash   → «🪙 Токены» in the bot
 *   «Следить» за кошельком   …?start=ww_<code>__dash   → «👀 Слежка» (creator, smart wallet)
 *
 * code = `bot_code` from the API (/tokens/meta, /tokens/{id}, /creators/{account}, /search), or the
 * same thing computed here: first 10 chars of the lower-case base32 of SHA-256 of the address —
 * `token_code` in indexer/services/dashboard/botlinks.py. `__dash` marks the source: the bot counts
 * visits and newcomers from the dashboard. No referral code in phase 2 (Б10 → phase 3:
 * t_<code>-<ref>__dash for a signed-in bot user).
 */

const BOT = (import.meta.env.VITE_BOT as string | undefined) || "sendler_trade_bot";

const CODE_LEN = 10;
const SOURCE = "__dash";

export const WATCH_ENABLED = true;

// --- SHA-256, synchronous: links are rendered as plain <a href>, WebCrypto is async. ---------

const K = new Uint32Array([
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
]);

export function sha256(data: Uint8Array): Uint8Array {
    const h = new Uint32Array([
        0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
    ]);
    const bitLen = data.length * 8;
    const padded = new Uint8Array(((data.length + 9 + 63) >> 6) << 6);
    padded.set(data);
    padded[data.length] = 0x80;
    const view = new DataView(padded.buffer);
    view.setUint32(padded.length - 8, Math.floor(bitLen / 2 ** 32));
    view.setUint32(padded.length - 4, bitLen >>> 0);
    const w = new Uint32Array(64);
    const rotr = (x: number, n: number) => (x >>> n) | (x << (32 - n));
    for (let off = 0; off < padded.length; off += 64) {
        for (let i = 0; i < 16; i++) w[i] = view.getUint32(off + i * 4);
        for (let i = 16; i < 64; i++) {
            const s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >>> 3);
            const s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >>> 10);
            w[i] = (w[i - 16] + s0 + w[i - 7] + s1) >>> 0;
        }
        let [a, b, c, d, e, f, g, hh] = h;
        for (let i = 0; i < 64; i++) {
            const S1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
            const ch = (e & f) ^ (~e & g);
            const t1 = (hh + S1 + ch + K[i] + w[i]) >>> 0;
            const S0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
            const maj = (a & b) ^ (a & c) ^ (b & c);
            const t2 = (S0 + maj) >>> 0;
            hh = g; g = f; f = e; e = (d + t1) >>> 0;
            d = c; c = b; b = a; a = (t1 + t2) >>> 0;
        }
        h[0] += a; h[1] += b; h[2] += c; h[3] += d; h[4] += e; h[5] += f; h[6] += g; h[7] += hh;
    }
    const out = new Uint8Array(32);
    const ov = new DataView(out.buffer);
    for (let i = 0; i < 8; i++) ov.setUint32(i * 4, h[i]);
    return out;
}

const B32 = "abcdefghijklmnopqrstuvwxyz234567";

/** RFC 4648 base32, lower case, no padding (Python's b32encode(...).lower() minus the "="). */
export function base32(bytes: Uint8Array): string {
    let out = "";
    let buf = 0;
    let bits = 0;
    for (const byte of bytes) {
        buf = (buf << 8) | byte;
        bits += 8;
        while (bits >= 5) {
            out += B32[(buf >>> (bits - 5)) & 31];
            bits -= 5;
        }
        buf &= (1 << bits) - 1;
    }
    if (bits > 0) out += B32[(buf << (5 - bits)) & 31];
    return out;
}

const codes = new Map<string, string>();

/** Bot code of a token or account address (cached). */
export function botCode(address: string): string {
    let code = codes.get(address);
    if (!code) {
        code = base32(sha256(new TextEncoder().encode(address))).slice(0, CODE_LEN);
        codes.set(address, code);
    }
    return code;
}

/** @deprecated name kept for readability at call sites about tokens. */
export const tokenCode = botCode;

function botUrl(prefix: "t_" | "wt_" | "ww_", code: string): string {
    return `https://t.me/${BOT}?start=${prefix}${code}${SOURCE}`;
}

/** «Купить в боте»: opens the bot's token screen. `code` = bot_code from the API when at hand. */
export function buyInBotUrl(token: string, code?: string | null): string {
    return botUrl("t_", code || botCode(token));
}

/** «Следить» за токеном: adds it to «🪙 Токены» in the bot. */
export function watchTokenUrl(token: string, code?: string | null): string {
    return botUrl("wt_", code || botCode(token));
}

/** «Следить» за кошельком (creator, smart wallet): adds it to «👀 Слежка». */
export function watchWalletUrl(account: string, code?: string | null): string {
    return botUrl("ww_", code || botCode(account));
}

export function botHomeUrl(): string {
    return `https://t.me/${BOT}`;
}
