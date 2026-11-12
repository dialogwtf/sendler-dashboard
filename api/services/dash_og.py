"""Sendler Dash — link previews of token pages (/token/<id>): messengers don't run JS, so the server writes the tags.

- page(token): the site's built index.html (cfg.SITE_INDEX, re-read when its mtime changes) with the token's title,
  description and og:/twitter: tags. nginx of dash.sendler.xyz proxies /token/<id> here and falls back to the static
  index.html on any error — a visitor always gets the SPA, a bot also gets the card.
- image(token): PNG 1200×630 drawn with Pillow (fonts Onest / Unbounded, OFL, assets/fonts): logo, ticker, name,
  price in NEAR and $, 24 h change, cap / volume / holders, a 24 h sparkline, the site's address.

Data — dash_service.token() (the same cached reads as the token page), no RPC. English: the card is for any chat.
"""
from __future__ import annotations

import base64
import html
import io
import os
import re
from functools import lru_cache
from pathlib import Path

from config import dash as cfg
from services import dash_service as svc

W, H = 1200, 630
BG, PANEL, LINE = "#0B0E14", "#121722", "#1E2533"
TEXT, MUTED, DIM = "#E8ECF3", "#8A94A6", "#5B6578"
ACCENT, UP, DOWN = "#6F7FF0", "#45D69B", "#F26D6D"
FONTS = Path(__file__).resolve().parents[1] / "assets" / "fonts"
FONT_FILES = {"onest": "Onest[wght].ttf", "unbounded": "Unbounded[wght].ttf"}
SUB = "₀₁₂₃₄₅₆₇₈₉"
ICON_MAX_BYTES = 512 * 1024
PADS = {"nearly": "Nearly", "justhoot": "JustHoot", "intear": "Intear"}

_INDEX: dict = {"mtime": None, "html": None}


# ─── numbers ─────────────────────────────────────────────────────────────────

def fmt_price(p: float | None) -> str:
    """0.000000543 → 0.0₆543 (zeros after «0.0» as a subscript, like the bot and the site); 1234.5 → 1,234.5."""
    if p is None:
        return "—"
    if p == 0:
        return "0"
    if p >= 1000:
        return f"{p:,.0f}"
    if p >= 1:
        return f"{p:,.4g}" if p < 100 else f"{p:,.2f}"
    s = f"{p:.20f}".split(".")[1]
    zeros = len(s) - len(s.lstrip("0"))
    digits = s[zeros:zeros + 4].rstrip("0") or "0"
    if zeros < 4:
        return f"0.{'0' * zeros}{digits}"
    return f"0.0{''.join(SUB[int(c)] for c in str(zeros))}{digits}"


def fmt_usd(v: float | None) -> str:
    if v is None:
        return "—"
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(v) >= div:
            return f"${v / div:.1f}{suf}"
    return f"${v:,.0f}" if abs(v) >= 1 else f"${fmt_price(v)}"


def fmt_change(c: float | None) -> str | None:
    if c is None:
        return None
    return f"{'+' if c >= 0 else '−'}{abs(c) * 100:.1f}%"


# ─── page ────────────────────────────────────────────────────────────────────

def _index_html() -> str | None:
    try:
        mtime = os.path.getmtime(cfg.SITE_INDEX)
    except OSError:
        return None
    if _INDEX["mtime"] != mtime:
        with open(cfg.SITE_INDEX, encoding="utf-8") as f:
            _INDEX.update(mtime=mtime, html=f.read())
    return _INDEX["html"]


def _set_meta(doc: str, attr: str, key: str, value: str) -> str:
    """Replace content of <meta {attr}="{key}" …>; add it before </head> if the build has none."""
    tag = f'<meta {attr}="{key}" content="{html.escape(value, quote=True)}" />'
    rx = re.compile(rf'<meta\s+{attr}="{re.escape(key)}"[^>]*>', re.I)
    if rx.search(doc):
        return rx.sub(lambda _: tag, doc, count=1)
    return doc.replace("</head>", f"    {tag}\n  </head>", 1)


def texts(token: str, t: dict) -> tuple[str, str]:
    sym = t.get("symbol") or token
    head = f"${sym}" if t.get("symbol") else token
    if t.get("price_near") is None:
        return f"{head} · Sendler Dash", f"{t.get('name') or sym} on NEAR: chart, holders, creator and smart money."
    parts = [f"{fmt_price(t['price_near'])} NEAR"]
    if t.get("price_usd") is not None:
        parts.append(f"${fmt_price(t['price_usd'])}")
    if (ch := fmt_change(t.get("change_24h"))) is not None:
        parts.append(f"{ch} 24h")
    cap = (t.get("launch") or {}).get("cap_usd")
    if cap:
        parts.append(f"cap {fmt_usd(cap)}")
    return f"{head} {' · '.join(parts[:1])} · Sendler Dash", \
        f"{t.get('name') or sym}: " + " · ".join(parts) + ". Chart, holders, creator and smart money — live."


async def page(token: str, version: str) -> str | None:
    """index.html with the token's tags, or None when the build is not on this server."""
    doc = _index_html()
    if doc is None:
        return None
    t = await svc.CACHE.get(f"og:{token}", 60, lambda: svc.token(token))
    title, desc = texts(token, t)
    url = f"{cfg.SITE_URL}/token/{token}"
    image = f"{cfg.API_URL}/v2/dash/og/token/{token}.png?v={version}"
    doc = re.sub(r"<title>.*?</title>", lambda _: f"<title>{html.escape(title)}</title>", doc, count=1, flags=re.S)
    for attr, key, value in (("name", "description", desc), ("property", "og:title", title),
                             ("property", "og:description", desc), ("property", "og:url", url),
                             ("property", "og:image", image), ("property", "og:image:alt", title),
                             ("name", "twitter:title", title), ("name", "twitter:description", desc),
                             ("name", "twitter:image", image)):
        doc = _set_meta(doc, attr, key, value)
    return doc


# ─── image ───────────────────────────────────────────────────────────────────

@lru_cache(maxsize=64)
def font(family: str, size: int, weight: int = 400) -> "ImageFont.FreeTypeFont":
    from PIL import ImageFont  # Pillow is only for the image: without it the API works, only the preview fails

    f = ImageFont.truetype(str(FONTS / FONT_FILES[family]), size)
    try:
        f.set_variation_by_axes([weight])
    except (OSError, ValueError):
        pass
    return f


def _text_w(d: ImageDraw.ImageDraw, s: str, f) -> float:
    return d.textlength(s, font=f)


def draw_price(d: ImageDraw.ImageDraw, x: float, y: float, s: str, f, fill: str) -> float:
    """Text with subscript digits drawn smaller and lower (the fonts have no ₀–₉ glyphs). Returns the right edge."""
    small = font("unbounded", int(f.size * 0.55), 600)
    for ch in s:
        if ch in SUB:
            c = str(SUB.index(ch))
            d.text((x, y + f.size * 0.42), c, font=small, fill=fill)
            x += _text_w(d, c, small) + 2
        else:
            d.text((x, y), ch, font=f, fill=fill)
            x += _text_w(d, ch, f)
    return x


def _fit(d, s: str, family: str, size: int, weight: int, max_w: float):
    while size > 20:
        f = font(family, size, weight)
        if _text_w(d, s, f) <= max_w:
            return f
        size -= 4
    return font(family, size, weight)


def _icon(data_uri: str | None, size: int) -> "Image.Image | None":
    """Token icon from ft_metadata: raster data: URIs only (SVG and remote URLs are not fetched)."""
    from PIL import Image, ImageDraw

    m = re.match(r"^data:image/(png|jpeg|jpg|webp|gif);base64,(.+)$", data_uri or "", re.S)
    if not m:
        return None
    try:
        raw = base64.b64decode(m.group(2), validate=False)
        if len(raw) > ICON_MAX_BYTES:
            return None
        img = Image.open(io.BytesIO(raw))
        img.draft("RGB", (size * 2, size * 2))
        img = img.convert("RGBA").resize((size, size), Image.LANCZOS)
    except Exception:  # noqa: BLE001 - a broken icon: a letter instead of it
        return None
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    img.putalpha(mask)
    return img


def _logo(d: ImageDraw.ImageDraw, x: int, y: int, s: int) -> None:
    d.rounded_rectangle((x, y, x + s, y + s), radius=int(s * 0.28), fill=ACCENT)
    k = s / 32
    pts = [(6, 17), (11, 17), (14, 10), (18, 23), (21, 17), (26, 17)]
    d.line([(x + px * k, y + py * k) for px, py in pts], fill=BG, width=max(2, int(2.4 * k)), joint="curve")


def _sparkline(d: ImageDraw.ImageDraw, box: tuple[int, int, int, int], values: list[float], color: str) -> None:
    vals = [v for v in values if v is not None]
    if len(vals) < 4:  # two or three points are a diagonal, not a chart
        return
    x0, y0, x1, y1 = box
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or hi or 1.0
    pts = [(x0 + (x1 - x0) * i / (len(vals) - 1), y1 - (y1 - y0) * (v - lo) / span) for i, v in enumerate(vals)]
    d.polygon(pts + [(x1, y1), (x0, y1)], fill=color + "22")
    d.line(pts, fill=color, width=4, joint="curve")


def render(token: str, t: dict, spark: list[float], holders: int | None) -> bytes:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img, "RGBA")
    pad = 64
    _logo(d, pad, 52, 44)
    d.text((pad + 60, 58), "Sendler Dash", font=font("unbounded", 26, 600), fill=TEXT)
    d.text((W - pad, 62), "dash.sendler.xyz", font=font("onest", 24, 500), fill=MUTED, anchor="ra")

    sym = t.get("symbol") or token.split(".")[0]
    icon = _icon(t.get("icon"), 112)
    ix, iy = pad, 150
    if icon is not None:
        img.paste(icon, (ix, iy), icon)
    else:
        d.ellipse((ix, iy, ix + 112, iy + 112), fill=PANEL, outline=LINE, width=2)
        d.text((ix + 56, iy + 56), sym[:1].upper(), font=font("unbounded", 52, 700), fill=ACCENT, anchor="mm")
    tx = ix + 112 + 32
    d.text((tx, iy - 4), f"${sym}", font=_fit(d, f"${sym}", "unbounded", 64, 700, W - tx - pad), fill=TEXT)
    sub = t.get("name") or token
    if (pad_name := PADS.get((t.get("launch") or {}).get("pad"))):
        sub = f"{sub} · {pad_name}"
    d.text((tx, iy + 76), sub[:60], font=font("onest", 30, 500), fill=MUTED)

    py = 318
    price = t.get("price_near")
    pf = font("unbounded", 60, 700)
    right = draw_price(d, pad, py, f"{fmt_price(price)}", pf, TEXT) if price is not None else \
        draw_price(d, pad, py, "no trades yet", font("unbounded", 44, 600), MUTED)
    if price is not None:
        right = draw_price(d, right + 16, py + 22, "NEAR", font("unbounded", 30, 600), MUTED)
        ch = t.get("change_24h")
        if (txt := fmt_change(ch)) is not None:
            col = UP if ch >= 0 else DOWN
            cf = font("onest", 30, 700)
            w = _text_w(d, txt + " 24h", cf)
            bx = right + 28
            d.rounded_rectangle((bx, py + 14, bx + w + 32, py + 62), radius=24, fill=col + "26")
            d.text((bx + 16, py + 20), txt + " 24h", font=cf, fill=col)
        if t.get("price_usd") is not None:
            draw_price(d, pad, py + 82, f"${fmt_price(t['price_usd'])}", font("onest", 34, 500), MUTED)

    stats = []
    launch = t.get("launch") or {}
    if launch.get("cap_usd"):
        stats.append(("Market cap", fmt_usd(launch["cap_usd"])))
    vol = t.get("volume_24h_usd")
    if vol:
        stats.append(("Volume 24h", fmt_usd(vol)))
    if holders:
        stats.append(("Holders", f"{holders:,}"))
    elif t.get("traders_24h"):
        stats.append(("Traders 24h", f"{t['traders_24h']:,}"))
    sx, sy = pad, 500
    for label, value in stats[:3]:
        d.text((sx, sy), label, font=font("onest", 24, 500), fill=DIM)
        d.text((sx, sy + 32), value, font=font("unbounded", 36, 600), fill=TEXT)
        sx += max(240, _text_w(d, value, font("unbounded", 36, 600)) + 64)

    ch = t.get("change_24h")
    _sparkline(d, (780, 440, W - pad, 570), spark, UP if (ch or 0) >= 0 else DOWN)
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


async def image(token: str) -> bytes:
    async def make():
        t = await svc.CACHE.get(f"og:{token}", 60, lambda: svc.token(token))
        db = svc._db()
        since = svc._now().replace(minute=0, second=0, microsecond=0) - svc.dt.timedelta(hours=24)
        spark = [c.get("c") async for c in db.dash_candles.find(
            {"token": token, "i": "1h", "t": {"$gte": since}}, {"c": 1}).sort("t", 1)]
        st = await db.dash_holder_stats.find_one({"_id": token}, {"holders": 1, "complete": 1, "seeded": 1}) or {}
        holders = st.get("holders") if (st.get("complete") or st.get("seeded")) else None
        return render(token, t, spark, holders)
    return await svc.CACHE.get(f"og.png:{token}", 600, make)
