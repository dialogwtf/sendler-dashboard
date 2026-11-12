"""Token creator's links: website, X (Twitter), Telegram, description - for the launch card.

Sources (checked on mainnet 2026-10-09):
- Nearly - arguments of launch: args.links {website, twitter, telegram} and args.description;
  after the launch - get_launch_by_token;
- JustHoot - JSON at ft_metadata.reference (storage.googleapis.com/justhoot/…): {description, telegram, twitter?, website?};
- Intear launch-v2 - arguments of launch_token: launch_data {telegram, x, twitch, website, description};
- any token - ft_metadata.reference (NEP-148), if it is JSON with links.

All of it is written by the creator: links are untrusted. Only http(s) is accepted, no spaces or quotes, up to 200
characters; an @handle becomes a link to x.com / t.me. The text shows the domain or the @handle, never a free caption.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

MAX_URL = 200
MAX_DESCRIPTION = 300
HANDLE = re.compile(r"^@?([A-Za-z0-9_]{2,40})$")
KEYS = {"website": "website", "site": "website", "web": "website", "twitter": "twitter", "x": "twitter",
        "telegram": "telegram", "tg": "telegram", "discord": "discord", "twitch": "twitch"}


@dataclass
class Links:
    urls: dict[str, str] = field(default_factory=dict)  # kind -> normalised URL
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
    """What to show instead of the link: @handle for X / Telegram, otherwise the domain."""
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
    """launch args.args or a get_launch_by_token record: links + description."""
    if not isinstance(args_or_launch, dict):
        return Links()
    links = from_dict(args_or_launch.get("links"))
    return links.merge(from_dict({"description": args_or_launch.get("description")}))


def from_intear(args: dict | None) -> Links:
    return from_dict((args or {}).get("launch_data"))
