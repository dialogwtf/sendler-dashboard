"""Ссылки создателя токена: сайт, X (Twitter), Telegram, описание — для алертов и карточки токена.

Источники (проверено на mainnet 2026-10-09):
- Nearly — аргументы launch: args.links {website, twitter, telegram} и args.description; после запуска —
  get_launch_by_token (Tokens уже читает его: Token.launch);
- JustHoot — JSON по ft_metadata.reference (storage.googleapis.com/justhoot/…): {description, telegram, twitter?, website?};
- Intear launch-v2 — аргументы launch_token: launch_data {telegram, x, twitch, website, description};
- любой токен — ft_metadata.reference (NEP-148), если это JSON со ссылками.

Всё это пишет создатель: ссылки недоверенные. Принимаем только http(s), без пробелов и кавычек, до 200 символов;
@handle превращаем в ссылку на x.com / t.me. В тексте показываем домен или @handle, а не произвольную подпись.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from urllib.parse import quote, urlparse

import aiohttp

from .i18n import tr

MAX_URL = 200
MAX_DESCRIPTION = 300
REFERENCE_MAX_BYTES = 32 * 1024
REFERENCE_TIMEOUT = 3.0
CACHE_TTL = 3600.0
HANDLE = re.compile(r"^@?([A-Za-z0-9_]{2,40})$")
KEYS = {"website": "website", "site": "website", "web": "website", "twitter": "twitter", "x": "twitter",
        "telegram": "telegram", "tg": "telegram", "discord": "discord", "twitch": "twitch"}


@dataclass
class Links:
    urls: dict[str, str] = field(default_factory=dict)  # вид → нормализованный URL
    description: str | None = None

    def __bool__(self) -> bool:
        return bool(self.urls or self.description)

    def merge(self, other: Links | None) -> Links:
        if other:
            for k, v in other.urls.items():
                self.urls.setdefault(k, v)
            self.description = self.description or other.description
        return self


def normalize(kind: str, value) -> str | None:
    if not isinstance(value, str):
        return None
    v = value.strip()
    if not v or len(v) > MAX_URL or any(ch in v for ch in " \"'<>\\\n\t"):
        return None
    m = HANDLE.match(v)
    if m and kind in ("twitter", "telegram") and (v.startswith("@") or "." not in v):
        return (f"https://x.com/{m.group(1)}" if kind == "twitter" else f"https://t.me/{m.group(1)}")
    if not re.match(r"^https?://", v, re.I):
        if re.match(r"^[A-Za-z0-9.-]+\.[A-Za-z]{2,}(/\S*)?$", v):
            v = "https://" + v
        else:
            return None
    p = urlparse(v)
    if p.scheme.lower() not in ("http", "https") or not p.netloc or "@" in p.netloc:
        return None
    return v


def label(kind: str, url: str) -> str:
    """Что показать вместо ссылки: @handle для X/Telegram, иначе домен."""
    p = urlparse(url)
    host = p.netloc.lower().removeprefix("www.")
    path = p.path.strip("/")
    if host in ("x.com", "twitter.com", "t.me", "telegram.me") and path and "/" not in path:
        return "@" + path
    return host + ("/" + path if path and len(path) <= 24 else "")


def from_dict(d: dict | None) -> Links:
    links = Links()
    if not isinstance(d, dict):
        return links
    for raw_key, value in d.items():
        kind = KEYS.get(str(raw_key).lower())
        url = normalize(kind, value) if kind else None
        if url:
            links.urls.setdefault(kind, url)
    desc = d.get("description")
    if isinstance(desc, str) and desc.strip():
        links.description = desc.strip()[:MAX_DESCRIPTION]
    return links


def from_nearly(args_or_launch: dict | None) -> Links:
    """launch args.args или запись get_launch_by_token: links + description."""
    if not isinstance(args_or_launch, dict):
        return Links()
    links = from_dict(args_or_launch.get("links"))
    return links.merge(from_dict({"description": args_or_launch.get("description")}))


def from_intear(args: dict | None) -> Links:
    return from_dict((args or {}).get("launch_data"))


class LinksResolver:
    """Кэш ссылок по токену: из ленты (remember) или из сети (Nearly — get_launch_by_token, иначе reference)."""

    def __init__(self, session: aiohttp.ClientSession, tokens) -> None:
        self.session = session
        self.tokens = tokens
        self._cache: dict[str, tuple[float, Links]] = {}

    def remember(self, token_id: str | None, links: Links | None) -> None:
        if token_id and links:
            self._cache[token_id] = (time.time(), links)

    async def for_token(self, token_id: str, reference: str | None = None) -> Links:
        hit = self._cache.get(token_id)
        if hit and time.time() - hit[0] < CACHE_TTL:
            return hit[1]
        links = Links()
        try:
            tok = await self.tokens.get(token_id)
            if tok.launch:
                links.merge(from_nearly(tok.launch))
            ref = reference or getattr(tok, "reference", None)
            if ref and not links:
                links.merge(await self.fetch_reference(ref))
        except Exception:  # noqa: BLE001 — ссылки необязательны, карточка и алерт без них работают
            pass
        self._cache[token_id] = (time.time(), links)
        return links

    async def fetch_reference(self, url: str) -> Links:
        if not normalize("website", url) or not url.lower().startswith("https://"):
            return Links()
        try:
            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=REFERENCE_TIMEOUT),
                                        allow_redirects=False) as r:
                if r.status != 200:
                    return Links()
                body = await r.content.read(REFERENCE_MAX_BYTES + 1)
            if len(body) > REFERENCE_MAX_BYTES:
                return Links()
            return from_dict(json.loads(body))
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, UnicodeDecodeError):
            return Links()


# ── страница токена на платформе и график ──────────────────────────────────────

def dexscreener_dcl(pool_id: str) -> str:
    """Пул Rhea DCL на DexScreener: refv2-<token_x>:<token_y>:<fee> (проверено по API DexScreener)."""
    return f"https://dexscreener.com/near/refv2-{quote(pool_id.replace('|', ':'), safe=':.-_')}"


def platform_links(launchpad: str | None, token: str | None, pool: str | None) -> list[tuple[str, str]]:
    """[(подпись, url)] — где смотреть запуск: страница платформы и график.

    Nearly — nearly.trade/<токен>, JustHoot — justhoot.fun/p/<токен> (маршруты из кода сайтов), график их пулов
    DCL — DexScreener. Intear — dex.intear.tech/launch?token= (там же график; пулы Intear DexScreener не видит)
    и /pool?id=. Адреса строятся только из аккаунтов и номеров пулов, не из данных создателя.
    """
    out: list[tuple[str, str]] = []
    t = quote(token, safe=".-_") if token else None
    if launchpad == "nearly" and t:
        out.append(("🌐 Nearly", f"https://nearly.trade/{t}"))
    elif launchpad == "justhoot" and t:
        out.append(("🌐 JustHoot", f"https://justhoot.fun/p/{t}"))
    elif launchpad == "intear" and t:
        out.append(("📈 Intear", f"https://dex.intear.tech/launch?token={t}"))
    if pool and "|" in pool:
        out.append((tr("📈 График"), dexscreener_dcl(pool)))
    elif pool and pool.startswith("v1:") and pool[3:].isdigit():  # классический пул Rhea (DexScreener: refv1-<id>)
        out.append((tr("📈 График"), f"https://dexscreener.com/near/refv1-{pool[3:]}"))
    elif pool and pool.isdigit() and launchpad != "intear":
        out.append((tr("📈 Пул Intear"), f"https://dex.intear.tech/pool?id={pool}"))
    return out

