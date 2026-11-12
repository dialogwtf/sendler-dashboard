"""Shared slowapi limiter instance."""
import ipaddress
import logging
from typing import Optional, Tuple

from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request

from config.env import TRUSTED_PROXY_IPS

logger = logging.getLogger(__name__)

_Network = ipaddress.IPv4Network | ipaddress.IPv6Network


def _parse_networks(raw: str) -> Tuple[_Network, ...]:
    networks = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            networks.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            logger.error(f"TRUSTED_PROXY_IPS: invalid entry {part!r} ignored")
    return tuple(networks)


_TRUSTED_PROXIES: Tuple[_Network, ...] = _parse_networks(TRUSTED_PROXY_IPS)


def _parse_ip(value: Optional[str]) -> Optional[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        return ipaddress.ip_address((value or "").strip())
    except ValueError:
        return None


def _is_trusted(ip) -> bool:
    return ip is not None and any(ip in net for net in _TRUSTED_PROXIES)


def client_ip(request: Request) -> str:
    """Real client IP for rate limiting and logs.

    Proxy headers are honoured only when the TCP peer is in TRUSTED_PROXY_IPS — otherwise
    any client could pick its own bucket by sending a fake header.
    """
    peer = get_remote_address(request)
    if not _is_trusted(_parse_ip(peer)):
        return peer

    for header in ("CF-Connecting-IP", "X-Real-IP"):
        ip = _parse_ip(request.headers.get(header))
        if ip is not None and not _is_trusted(ip):
            return str(ip)

    # Rightmost entry that is not one of our proxies; entries to its left are client-supplied.
    forwarded = request.headers.get("X-Forwarded-For", "")
    for part in reversed(forwarded.split(",")):
        ip = _parse_ip(part)
        if ip is not None and not _is_trusted(ip):
            return str(ip)

    return peer


limiter = Limiter(key_func=client_ip, default_limits=["300/minute"])
