/**
 * Server clock. Ages ("6 мин") are counted from the API's clock, not the device's: phone
 * clocks drift. Every REST response's Date header updates the offset (api/client.ts).
 */
let offsetMs = 0;
let samples = 0;

/** Feed a Date header and the local time the response arrived. Second precision → smoothed. */
export function observeServerDate(header: string | null, localNow = Date.now()) {
    if (!header) return;
    const server = Date.parse(header);
    if (Number.isNaN(server)) return;
    // The header is truncated to the second: the true server time is in [server, server + 1 s).
    const sample = server + 500 - localNow;
    samples = Math.min(samples + 1, 10);
    offsetMs += (sample - offsetMs) / samples;
}

/** Now on the server's clock, ms. */
export function serverNow(): number {
    return Date.now() + offsetMs;
}

/** An ISO time (or unix seconds) as epoch ms; null when unknown. */
export function msOf(t: string | number | null | undefined): number | null {
    if (t === null || t === undefined) return null;
    const ms = typeof t === "number" ? t * 1000 : Date.parse(t);
    return Number.isNaN(ms) ? null : ms;
}

/** Seconds since an ISO time (or unix seconds), on the server's clock; null when unknown. */
export function secondsSince(t: string | number | null | undefined, now = serverNow()): number | null {
    const ms = msOf(t);
    if (ms === null) return null;
    return Math.max(0, (now - ms) / 1000);
}

export function _resetClock() {
    offsetMs = 0;
    samples = 0;
}
