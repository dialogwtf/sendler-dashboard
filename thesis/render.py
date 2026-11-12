"""Charts (SVG, light and dark) and the numbers table of THESIS.md from the snapshot. Plain Python, no dependencies.

Chart rules: one y-axis per plot (two measures -> two stacked panels), thin marks, hairline solid grid, a legend for
two or more series, sparing direct labels, text in ink colours only. Palette slots were checked for colour-blind
separation and contrast on both surfaces.
"""
from __future__ import annotations

import datetime as dt
import math
from pathlib import Path

THEMES = {
    "light": {"surface": "#fcfcfb", "ink": "#0b0b0b", "ink2": "#52514e", "muted": "#898781", "grid": "#e1e0d9",
              "axis": "#c3c2b7", "s1": "#2a78d6", "s2": "#eb6834", "neg": "#e34948"},
    "dark": {"surface": "#1a1a19", "ink": "#f0efec", "ink2": "#c3c2b7", "muted": "#898781", "grid": "#2c2c2a",
             "axis": "#383835", "s1": "#3987e5", "s2": "#d95926", "neg": "#e66767"},
}
FONT = "system-ui, -apple-system, 'Segoe UI', sans-serif"
W = 880
LEFT, RIGHT = 64, 28


# ── derived numbers ─────────────────────────────────────────────────────────

def _rolling(vals: list[float], n: int = 7) -> list[float]:
    out = []
    for i in range(len(vals)):
        w = vals[max(0, i - n + 1): i + 1]
        out.append(sum(w) / len(w))
    return out


def _corr(a: list[float], b: list[float]) -> float | None:
    n = len(a)
    if n < 10:
        return None
    ma, mb = sum(a) / n, sum(b) / n
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    if va <= 0 or vb <= 0:
        return None
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / math.sqrt(va * vb)


def full_days(snap: dict) -> list[dict]:
    """Daily DEX rows without the day the snapshot was taken on (it is incomplete)."""
    last = snap["generated_at"][:10]
    return [r for r in snap["dex_daily"] if r["day"] < last]


def derive(snap: dict) -> dict:
    """Numbers that are computed from the series rather than read from one field."""
    days = full_days(snap)
    price = {r["day"]: r["usd"] for r in snap["near_price"]}
    traders = _rolling([r["traders"] for r in days])
    usd = _rolling([r["usd"] for r in days])
    px = [price.get(r["day"]) for r in days]
    peak_i = max(range(len(days)), key=lambda i: traders[i])
    px_known = [(i, p) for i, p in enumerate(px) if p]
    px_peak_i = max(px_known, key=lambda t: t[1])[0] if px_known else None
    first7 = traders[6] if len(traders) > 6 else traders[-1]
    out = {
        "window": [days[0]["day"], days[-1]["day"]], "days": len(days),
        "traders_start": round(first7), "traders_peak": round(traders[peak_i]), "traders_peak_day": days[peak_i]["day"],
        "traders_now": round(traders[-1]), "traders_from_peak": round(traders[-1] / traders[peak_i] - 1, 4),
        "usd_peak": round(max(usd)), "usd_now": round(usd[-1]), "usd_from_peak": round(usd[-1] / max(usd) - 1, 4),
        "launch_share_30d": round(sum(r["launch_near"] for r in days[-30:]) / max(1e-9, sum(r["near"] for r in days[-30:])), 4),
    }
    if px_peak_i is not None and px[-1]:
        out |= {"price_start": px_known[0][1], "price_peak": px[px_peak_i], "price_peak_day": days[px_peak_i]["day"],
                "price_now": px[-1], "price_from_peak": round(px[-1] / px[px_peak_i] - 1, 4)}
        # lead / lag: weekly log-change of traders against the weekly log-change of price `k` days later
        px_s = _rolling([p if p else float("nan") for p in px], 7)
        lags = {}
        for k in (-14, -7, 0, 7, 14):
            a, b = [], []
            for t in range(13, len(days)):
                u = t + k
                if u - 7 < 6 or u >= len(days):
                    continue
                vals = (traders[t], traders[t - 7], px_s[u], px_s[u - 7])
                if any(v != v or v <= 0 for v in vals):
                    continue
                a.append(math.log(vals[0] / vals[1]))
                b.append(math.log(vals[2] / vals[3]))
            c = _corr(a, b)
            lags[str(k)] = {"corr": round(c, 3) if c is not None else None, "n": len(a)}
        out["lead_lag"] = lags
    full_net = [d for d in snap["network"]["days"] if d["full"]]
    if full_net:
        out["net_txs_day"] = round(sum(d["txs"] for d in full_net) / len(full_net))
        out["net_signers_day"] = round(sum(d["signers"] for d in full_net) / len(full_net))
        out["net_fee_usd"] = round(sum(d["fee_per_tx_usd"] for d in full_net) / len(full_net), 5)
        out["net_new_accounts_day"] = round(sum(d["new_accounts"] for d in full_net) / len(full_net))
        out["net_days"] = [d["day"] for d in full_net]
        by_day = {r["day"]: r for r in days}
        tr = [by_day[d["day"]]["traders"] for d in full_net if d["day"] in by_day]
        if tr:
            out["traders_share_of_signers"] = round(sum(tr) / len(tr) / out["net_signers_day"], 5)
    return out


# ── svg helpers ─────────────────────────────────────────────────────────────

def _esc(s: str) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _text(x, y, s, fill, size=11, anchor="start", weight=400) -> str:
    return (f'<text x="{x:.1f}" y="{y:.1f}" fill="{fill}" font-size="{size}" font-weight="{weight}" '
            f'text-anchor="{anchor}">{_esc(s)}</text>')


def _nice_max(v: float) -> tuple[float, float]:
    """(axis maximum, tick step) with 3-5 round ticks."""
    if v <= 0:
        return 1.0, 0.5
    exp = 10 ** math.floor(math.log10(v))
    for m in (1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10):
        if v <= m * exp:
            top = m * exp
            break
    for n in (4, 5, 3):
        step = top / n
        if abs(step / 10 ** math.floor(math.log10(step)) - round(step / 10 ** math.floor(math.log10(step)), 1)) < 1e-9:
            return top, step
    return top, top / 4


def _fmt(v: float, money: bool = False, compact: bool = False) -> str:
    p = "$" if money else ""
    a = abs(v)
    if compact or a >= 10_000:
        if a >= 1e6:
            return f"{p}{v / 1e6:.1f}M".replace(".0M", "M")
        if a >= 1e3:
            return f"{p}{v / 1e3:.1f}K".replace(".0K", "K")
    if a >= 100 or float(v).is_integer():
        return f"{p}{v:,.0f}"
    return f"{p}{v:,.2f}"


def _frame(c: dict, h: int, title: str, subtitle: str, body: list[str]) -> str:
    return "\n".join([
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {h}" width="{W}" height="{h}" '
        f'font-family="{FONT}" role="img" aria-label="{_esc(title)}">',
        f'<rect width="{W}" height="{h}" fill="{c["surface"]}"/>',
        _text(LEFT - 40, 30, title, c["ink"], 15, weight=600),
        _text(LEFT - 40, 49, subtitle, c["ink2"], 12),
        *body, "</svg>"])


def _yaxis(c: dict, top: float, bottom: float, vmax: float, step: float, money=False, compact=False) -> list[str]:
    out = []
    v = 0.0
    while v <= vmax + 1e-9:
        y = bottom - (bottom - top) * v / vmax
        out.append(f'<line x1="{LEFT}" x2="{W - RIGHT}" y1="{y:.1f}" y2="{y:.1f}" '
                   f'stroke="{c["axis"] if v == 0 else c["grid"]}" stroke-width="1"/>')
        out.append(_text(LEFT - 8, y + 4, _fmt(v, money, compact or vmax >= 10_000), c["muted"], 11, "end"))
        v += step
    return out


def _legend(c: dict, y: float, items: list[tuple[str, str]]) -> list[str]:
    out, x = [], LEFT
    for color, label in items:
        out.append(f'<rect x="{x}" y="{y - 9}" width="10" height="10" rx="2" fill="{color}"/>')
        out.append(_text(x + 16, y, label, c["ink2"], 12))
        x += 30 + 6.6 * len(label)
    return out


def _bar(x: float, y: float, w: float, h: float, fill: str, round_top: bool = True) -> str:
    """A column: 4px rounded data-end, square at the baseline."""
    if h <= 0:
        return ""
    r = min(4.0, w / 2, h) if round_top else 0.0
    return (f'<path d="M{x:.1f},{y + h:.1f} V{y + r:.1f} Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f} H{x + w - r:.1f} '
            f'Q{x + w:.1f},{y:.1f} {x + w:.1f},{y + r:.1f} V{y + h:.1f} Z" fill="{fill}"/>')


def _month_ticks(c: dict, days: list[str], xs: list[float], y: float) -> list[str]:
    out = []
    for d, x in zip(days, xs):
        if d.endswith("-01") or d.endswith("-15"):
            label = dt.date.fromisoformat(d).strftime("%b %d").replace(" 0", " ")
            out.append(_text(x, y, label, c["muted"], 11, "middle"))
    return out


def _line_panel(c: dict, top: float, bottom: float, xs: list[float], vals: list[float | None], color: str,
                heading: str, money: bool, labels: list[tuple[int, str, str]]) -> list[str]:
    known = [v for v in vals if v is not None]
    vmax, step = _nice_max(max(known))
    out = [_text(LEFT, top - 12, heading, c["ink2"], 12, weight=600), *_yaxis(c, top, bottom, vmax, step, money)]
    pts = [(x, bottom - (bottom - top) * v / vmax) for x, v in zip(xs, vals) if v is not None]
    out.append(f'<path d="M{" L".join(f"{x:.1f},{y:.1f}" for x, y in pts)}" fill="none" stroke="{color}" '
               f'stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>')
    for i, text, anchor in labels:
        if vals[i] is None:
            continue
        x, y = xs[i], bottom - (bottom - top) * vals[i] / vmax
        out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="{color}" stroke="{c["surface"]}" stroke-width="2"/>')
        dx = -8 if anchor == "end" else 8
        out.append(_text(x + dx, y - 8 if anchor != "end" else y + 16, text, c["ink"], 11, anchor, 600))
    return out


# ── charts ──────────────────────────────────────────────────────────────────

def chart_activity(snap: dict, c: dict) -> str:
    days = full_days(snap)
    price = {r["day"]: r["usd"] for r in snap["near_price"]}
    traders = _rolling([r["traders"] for r in days])
    px = [price.get(r["day"]) for r in days]
    xs = [LEFT + (W - LEFT - RIGHT) * i / (len(days) - 1) for i in range(len(days))]
    ti = max(range(len(days)), key=lambda i: traders[i])
    pk = [(i, p) for i, p in enumerate(px) if p]
    pi = max(pk, key=lambda t: t[1])[0]
    last = len(days) - 1

    def day(i):
        return dt.date.fromisoformat(days[i]["day"]).strftime("%b %d").replace(" 0", " ")

    body = _line_panel(c, 92, 232, xs, traders, c["s1"], "Unique DEX traders per day (7-day average)", False,
                       [(ti, f"{traders[ti]:,.0f} · {day(ti)}", "start"), (last, f"{traders[last]:,.0f}", "end")])
    body += _line_panel(c, 292, 432, xs, px, c["s2"], "NEAR price, USD", True,
                        [(pi, f"${px[pi]:.2f} · {day(pi)}", "start"), (last, f"${px[last]:.2f}", "end")])
    body += _month_ticks(c, [r["day"] for r in days], xs, 452)
    return _frame(c, 470, "Trading activity rose with the NEAR price and fell faster than it",
                  f"NEAR mainnet DEX swaps (Rhea DCL, Rhea v1, Intear), {days[0]['day']} to {days[-1]['day']}", body)


def _weeks(rows: list[dict], keys: list[str]) -> list[dict]:
    out: dict[str, dict] = {}
    for r in rows:
        d = dt.date.fromisoformat(r["day"])
        w = (d - dt.timedelta(days=d.weekday())).isoformat()
        o = out.setdefault(w, {"week": w, "n": 0, **{k: 0.0 for k in keys}})
        o["n"] += 1
        for k in keys:
            o[k] += float(r.get(k) or 0)
    return [out[w] for w in sorted(out)]


def _stacked(c: dict, weeks: list[dict], series: list[tuple[str, str, str]], top: float, bottom: float,
             money: bool) -> list[str]:
    """Columns per week: the daily average of each series, stacked with a 2px surface gap."""
    totals = [sum(w[k] for k, _, _ in series) / w["n"] for w in weeks]
    vmax, step = _nice_max(max(totals))
    body = _legend(c, 78, [(c[col], label) for _, col, label in series])
    body += _yaxis(c, top, bottom, vmax, step, money, compact=money)
    slot = (W - LEFT - RIGHT) / len(weeks)
    bw = min(24.0, slot * 0.6)
    for i, w in enumerate(weeks):
        x = LEFT + slot * i + (slot - bw) / 2
        y = bottom
        for j, (k, col, _) in enumerate(series):
            h = (bottom - top) * (w[k] / w["n"]) / vmax
            if h < 0.5:
                continue
            gap = 2 if j else 0
            y -= h
            body.append(_bar(x, y, bw, max(0.0, h - gap), c[col], round_top=j == len(series) - 1 or
                             all(w[kk] / w["n"] / vmax * (bottom - top) < 0.5 for kk, _, _ in series[j + 1:])))
        label = dt.date.fromisoformat(w["week"]).strftime("%b %d").replace(" 0", " ")
        body.append(_text(x + bw / 2, bottom + 16, label, c["muted"], 11, "middle"))
    return body


def chart_volume(snap: dict, c: dict) -> str:
    rows = []
    for r in full_days(snap):
        share = r["launch_near"] / r["near"] if r["near"] else 0.0
        rows.append({"day": r["day"], "other": r["usd"] * (1 - share), "launch": r["usd"] * share})
    weeks = [w for w in _weeks(rows, ["other", "launch"]) if w["n"] >= 4]
    body = _stacked(c, weeks, [("other", "s1", "Other tokens"), ("launch", "s2", "Launchpad tokens")], 100, 330, True)
    return _frame(c, 364, "DEX volume: one burst, then back toward the starting level",
                  "Average daily volume per week, USD; week starts on the date shown", body)


def chart_launches(snap: dict, c: dict) -> str:
    weeks = [w for w in _weeks(snap["launches"]["per_day"], ["nearly", "justhoot"]) if w["n"] >= 4]
    body = _stacked(c, weeks, [("nearly", "s1", "Nearly"), ("justhoot", "s2", "JustHoot")], 100, 330, False)
    return _frame(c, 364, "New token launches per day peaked in mid-October",
                  "Average launches per day per week, by launchpad; week starts on the date shown", body)


def chart_outcomes(snap: dict, c: dict) -> str:
    L, cr, h = snap["launches"], snap["creators"], snap["holders"]
    rows = [("Got at least one trade", L["traded_share"]),
            ("Creator dumped within 5 minutes of trading", cr["dumped_share"]),
            ("Peak price at least 2x the first trade", L["x_max"]["ge2_share"]),
            ("Still traded a week later", L["alive"]["share"]),
            ("Peak market cap of $10K or more", L["peak_cap_usd"]["ge_10k_share"]),
            ("Reached 50 holders", h["ge_50_share"])]
    x0, x1, top, rowh = 330, W - 80, 78, 34
    body = [f'<line x1="{x0}" x2="{x0}" y1="{top - 6}" y2="{top + rowh * len(rows)}" stroke="{c["axis"]}"/>']
    for i, (label, v) in enumerate(rows):
        y = top + rowh * i
        w = (x1 - x0) * (v or 0)
        body.append(_text(x0 - 12, y + 15, label, c["ink2"], 12, "end"))
        r = min(4.0, w / 2)
        body.append(f'<path d="M{x0},{y + 2} H{x0 + w - r:.1f} Q{x0 + w:.1f},{y + 2} {x0 + w:.1f},{y + 2 + r:.1f} '
                    f'V{y + 20 - r:.1f} Q{x0 + w:.1f},{y + 20} {x0 + w - r:.1f},{y + 20} H{x0} Z" fill="{c["s1"]}"/>')
        body.append(_text(x0 + w + 8, y + 15, f"{100 * (v or 0):.1f}%", c["ink"], 12, weight=600))
    return _frame(c, top + rowh * len(rows) + 16, "What happens to a launched token",
                  f"Share of {L['total']:,} Nearly and JustHoot launches, {L['from']} to {L['to']}", body)


def chart_wallets(snap: dict, c: dict) -> str:
    w = snap["wallets"]
    names = ["under -100", "-100 to -10", "-10 to -1", "-1 to 0", "0 to 1", "1 to 10", "10 to 100", "over 100"]
    hist = w["histogram"]
    vmax, step = _nice_max(max(b["wallets"] for b in hist))
    top, bottom = 100, 300
    body = _legend(c, 78, [(c["neg"], "Lost NEAR"), (c["s1"], "Made NEAR")])
    body += _yaxis(c, top, bottom, vmax, step)
    slot = (W - LEFT - RIGHT) / len(hist)
    for i, b in enumerate(hist):
        bw = 24.0
        x = LEFT + slot * i + (slot - bw) / 2
        h = (bottom - top) * b["wallets"] / vmax
        body.append(_bar(x, bottom - h, bw, h, c["neg"] if b["to"] <= 0 else c["s1"]))
        body.append(_text(x + bw / 2, bottom - h - 6, f"{b['wallets']:,}", c["ink"], 11, "middle", 600))
        body.append(_text(x + bw / 2, bottom + 16, names[i], c["muted"], 11, "middle"))
    body.append(_text(LEFT + (W - LEFT - RIGHT) / 2, bottom + 36, "Wallet result over the window, NEAR", c["ink2"], 11,
                      "middle"))
    return _frame(c, 350, f"{100 * w['profitable_share']:.0f}% of trading wallets made money; the median wallet lost",
                  f"{w['wallets']:,} wallets that bought a token for NEAR on a DEX, {w['from']} to {w['to']}", body)


CHARTS = {"activity-vs-price": chart_activity, "dex-volume": chart_volume, "launches-per-week": chart_launches,
          "launch-outcomes": chart_outcomes, "wallet-results": chart_wallets}


# ── numbers table ───────────────────────────────────────────────────────────

def _p(x) -> str:
    return "n/a" if x is None else f"{100 * x:.1f}%"


def numbers_md(snap: dict, d: dict) -> str:
    L, cr, w, h, k = snap["launches"], snap["creators"], snap["wallets"], snap["holders"], snap["concentration"]
    rows = [
        ("**DEX activity** (`dex_daily`: `dash_candles`, `i: 1d`)", "", ""),
        ("Window of full days", f"{d['window'][0]} to {d['window'][1]} ({d['days']} days)", ""),
        ("Unique traders per day, 7-day average: first week", f"{d['traders_start']:,}", "union of `w` over all tokens of the day"),
        ("… peak", f"{d['traders_peak']:,} on {d['traders_peak_day']}", ""),
        ("… latest", f"{d['traders_now']:,} ({_p(d['traders_from_peak'])} from the peak)", ""),
        ("Daily volume, 7-day average: peak / latest", f"${d['usd_peak']:,} / ${d['usd_now']:,} ({_p(d['usd_from_peak'])})", "sum of `v_usd`"),
        ("Launchpad tokens' share of volume, last 30 days", _p(d["launch_share_30d"]), "tokens of the Nearly / JustHoot / Intear launchpads, by account"),
        (f"Top 1 / 5 / 10 tokens' share of volume, last {k['days']} days", f"{_p(k['top1_share'])} / {_p(k['top5_share'])} / {_p(k['top10_share'])} of {k['tokens']:,} tokens", "`concentration`"),
    ]
    if "price_peak" in d:
        rows += [
            ("**NEAR price** (`near_price`: `ft_price_snapshots`, REF Finance)", "", ""),
            ("Start of window / peak / latest", f"${d['price_start']:.2f} / ${d['price_peak']:.2f} on {d['price_peak_day']} / ${d['price_now']:.2f} ({_p(d['price_from_peak'])} from the peak)", ""),
            ("Correlation of weekly change in traders with weekly change in price `k` days later", "; ".join(
                f"k={kk}: {v['corr']}" for kk, v in d["lead_lag"].items()), "`derive.lead_lag`; k > 0 means traders move first; overlapping daily observations, n = "
                + str(d["lead_lag"]["0"]["n"])),
        ]
    rows += [
        ("**Launches** (`launches`: `dash_launches`)", "", ""),
        ("Launches in the window", f"{L['total']:,} ({L['from']} to {L['to']}): " + ", ".join(f"{p} {n:,}" for p, n in sorted(L["by_pad"].items())), ""),
        ("Got at least one trade", _p(L["traded_share"]), "`px0` is set"),
        ("Lifetime volume per launch: median / 90th percentile", f"{L['volume_near']['median']:,} / {L['volume_near']['p90']:,} NEAR", ""),
        ("Peak price vs first trade: median", f"{L['x_max']['median']}x", "`px_max / px0`"),
        ("Peak at least 2x / 5x / 10x", f"{_p(L['x_max']['ge2_share'])} / {_p(L['x_max']['ge5_share'])} / {_p(L['x_max']['ge10_share'])}", ""),
        ("Last price below the first trade", _p(L["x_now"]["below_start_share"]), "`px / px0 < 1`"),
        ("Peak market cap: median / share at $10K+ / $100K+", f"${L['peak_cap_usd']['median']:,.0f} / {_p(L['peak_cap_usd']['ge_10k_share'])} / {_p(L['peak_cap_usd']['ge_100k_share'])}", f"`px_max` x 1e9 supply x NEAR at ${L['peak_cap_usd']['near_usd']}"),
        ("Launches older than 7 days still traded in the last 3 days", f"{L['alive']['traded_in_last_3d']:,} of {L['alive']['older_than_7d']:,} ({_p(L['alive']['share'])})", ""),
        ("Unique creators / with a single launch / with 5 or more", f"{L['creators']['unique']:,} / {_p(L['creators']['one_launch_share'])} / {L['creators']['ge5_launches']}", ""),
        ("Launches by the 10 most active creators", _p(L["creators"]["top10_launch_share"]), ""),
        (f"Launches seen live with tax data: built to tax bots", f"{L['live']['fee_farm']} of {L['live']['n']} ({_p(L['live']['fee_farm_share'])})", "`fee_farm` is set (live launches only)"),
        ("**Creators** (`creators`: `sniper_launch_stats`)", "", ""),
        ("Creator sold half or more of his tokens within 5 minutes of trading", f"{cr['dumped']:,} of {cr['judged']:,} ({_p(cr['dumped_share'])})", "`dumped`"),
        ("… by launchpad", ", ".join(f"{p} {_p(v['dumped_share'])}" for p, v in sorted(cr["by_pad"].items())), ""),
        ("Creators who received their own tokens / of them sold any within the first hour", f"{_p(cr['creator_held_share'])} / {_p(cr['sold_any_share_of_holders'])}", "`dev_in > 0` / `dev_sold > 0`"),
        ("Creators with 3+ launches who dumped at least half of them", f"{cr['repeat_creators']['dump_half_or_more']} of {cr['repeat_creators']['ge3_launches']} ({_p(cr['repeat_creators']['share'])})", ""),
        ("**Holders** (`holders`: `dash_holder_stats`)", "", ""),
        ("Holders per launch token: median / 90th percentile / maximum", f"{h['median']:.0f} / {h['p90']:.0f} / {h['max']}", f"{h['tokens']:,} tokens"),
        ("Tokens with 10 or fewer / 50 or more / 100 or more holders", f"{_p(h['le_10_share'])} / {_p(h['ge_50_share'])} / {_p(h['ge_100_share'])}", ""),
        ("**Wallets** (`wallets`: `sniper_smart_flows`, `sniper_smart_prices`)", "", ""),
        ("Wallets that bought a token for NEAR", f"{w['wallets']:,} ({w['from']} to {w['to']}), {w['invested_near']:,.0f} NEAR put in", "entries of 0.1 NEAR or more"),
        ("Wallets in profit / median result", f"{_p(w['profitable_share'])} / {w['median_pnl_near']} NEAR", "realised part + the rest at the last price"),
        ("Total won / total lost / net", f"{w['winners_total_near']:,.0f} / {w['losers_total_near']:,.0f} / {w['net_near']:,.0f} NEAR", ""),
        ("Share of all profit taken by the top 1% of wallets", _p(w["top1pct_share_of_profit"]), ""),
        ("Wallets with 5+ tokens traded: in profit", f"{_p(w['active_ge5_tokens']['profitable_share'])} of {w['active_ge5_tokens']['wallets']:,}", ""),
    ]
    if "net_txs_day" in d:
        rows += [
            ("**Network** (`network`: `dash_network`, `i: 1d`)", "", ""),
            ("Full days measured", ", ".join(d["net_days"]), ""),
            ("Transactions / unique signers / new accounts per day", f"{d['net_txs_day']:,} / {d['net_signers_day']:,} / {d['net_new_accounts_day']:,}", "signers: HyperLogLog estimate"),
            ("Average fee per transaction", f"${d['net_fee_usd']}", f"burnt NEAR / transactions x ${snap['network']['near_usd']}"),
            ("DEX traders as a share of daily signers", _p(d.get("traders_share_of_signers")), ""),
        ]
    mv, sp = snap.get("moves") or {}, snap.get("launch_speed") or {}
    if sp.get("launches"):
        rows += [
            ("**Launch speed** (`launch_speed`: `dash_launches`, launches seen live)", "", ""),
            ("Seconds from the first on-chain signal to the first trade: median / 90th percentile",
             f"{sp['to_first_trade_s']['median']} / {sp['to_first_trade_s']['p90']} s", f"{sp['launches']} launches"),
        ]
    if mv:
        rows += [
            ("**Large transfers** (`big_moves`: `dash_moves`, transfers of $1,000 and more)", "", ""),
            ("Window", f"{mv['from']} to {mv['to']} UTC ({mv['hours']} hours)", ""),
            ("Transfers / value / value per day", f"{mv['transfers']:,} / ${mv['usd']:,} / ${mv['usd_per_day']:,}", ""),
            ("By token", ", ".join(f"{t['symbol'] or t['token']} {_p(t['share'])}" for t in mv["by_token"]), ""),
            ("By counterparty", ", ".join(f"{k} {_p(v)}" for k, v in mv["by_route"].items()), "labels of `dash_labels`"),
        ]
    out = [f"# Numbers behind THESIS.md", "",
           f"Generated by `python thesis/build.py` from the snapshot of {snap['generated_at']} "
           f"(`thesis/data/snapshot.json`). The first column names the function of `thesis/build.py` and the collection "
           f"each block comes from.", "", "| Metric | Value | How |", "|---|---|---|"]
    out += [f"| {a} | {b} | {c} |" for a, b, c in rows]
    return "\n".join(out) + "\n"


def render(snap: dict, charts_dir: Path, numbers_path: Path) -> dict:
    d = derive(snap)
    charts_dir.mkdir(parents=True, exist_ok=True)
    for name, fn in CHARTS.items():
        for theme, colors in THEMES.items():
            (charts_dir / f"{name}-{theme}.svg").write_text(fn(snap, colors), encoding="utf-8", newline="\n")
    numbers_path.parent.mkdir(parents=True, exist_ok=True)
    numbers_path.write_text(numbers_md(snap, d), encoding="utf-8", newline="\n")
    return d
