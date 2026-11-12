"""NEAR primitives of the wallet sign-in (NEP-413): base58 keys, the signed payload hash, ed25519, and the check
that the signing key is a FullAccess key of the account (one `view_access_key` RPC call per sign-in — the only RPC
call of the dashboard API).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from typing import Optional

import httpx

from config.env import NEAR_RPC_URLS

logger = logging.getLogger(__name__)

# ─── base58 (Bitcoin alphabet, as in NEAR keys and signatures) ───────────────────

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_B58_INDEX = {c: i for i, c in enumerate(_B58)}


def b58encode(data: bytes) -> str:
    n = int.from_bytes(data, "big")
    out = ""
    while n:
        n, r = divmod(n, 58)
        out = _B58[r] + out
    return "1" * (len(data) - len(data.lstrip(b"\0"))) + out


def b58decode(text: str) -> bytes:
    n = 0
    for c in text:
        if c not in _B58_INDEX:
            raise ValueError("invalid base58")
        n = n * 58 + _B58_INDEX[c]
    body = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    return b"\0" * (len(text) - len(text.lstrip("1"))) + body


def decode_key(value, prefix: str, length: int) -> Optional[bytes]:
    """"<prefix>:<base58>" of an exact length → bytes, else None."""
    if not isinstance(value, str) or not value.startswith(prefix + ":"):
        return None
    try:
        raw = b58decode(value[len(prefix) + 1:])
    except ValueError:
        return None
    return raw if len(raw) == length else None


# ─── NEP-413 / ed25519 ───────────────────────────────────────────────────────────

NEP413_TAG = 2**31 + 413


def _borsh_string(s: str) -> bytes:
    b = s.encode()
    return len(b).to_bytes(4, "little") + b


def nep413_hash(message: str, nonce: bytes, recipient: str, callback_url: Optional[str] = None) -> bytes:
    """sha256(borsh(2³¹+413) ‖ borsh({message, nonce, recipient, callback_url})) — what the wallet signs."""
    data = (
        NEP413_TAG.to_bytes(4, "little")
        + _borsh_string(message)
        + nonce
        + _borsh_string(recipient)
        + (b"\x01" + _borsh_string(callback_url) if callback_url is not None else b"\x00")
    )
    return hashlib.sha256(data).digest()


def ed25519_verify(public_key: bytes, message: bytes, signature: bytes) -> bool:
    from nacl.exceptions import BadSignatureError
    from nacl.signing import VerifyKey

    try:
        VerifyKey(public_key).verify(message, signature)
        return True
    except (BadSignatureError, ValueError, TypeError):
        return False


# ─── accounts ────────────────────────────────────────────────────────────────────

_ACCOUNT_RE = re.compile(r"^(([a-z\d]+[\-_])*[a-z\d]+\.)*([a-z\d]+[\-_])*[a-z\d]+$")
_IMPLICIT_RE = re.compile(r"^[0-9a-f]{64}$")


def is_valid_account_id(value) -> bool:
    return isinstance(value, str) and 2 <= len(value) <= 64 and bool(_ACCOUNT_RE.match(value))


# ─── key ownership ───────────────────────────────────────────────────────────────

class KeyCheckError(Exception):
    """HTTP status + detail code: 401 key_not_owned, 503 rpc_unavailable."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


async def _view_access_key(account_id: str, public_key: str, retries: int = 3) -> dict:
    """→ the RPC `result`, or {"missing": "account" | "key"}. Fails over across NEAR_RPC_URLS; retries transport
    errors, 429 and 5xx."""
    params = {"request_type": "view_access_key", "finality": "final", "account_id": account_id,
              "public_key": public_key}
    last = "no RPC URL configured"
    attempts = retries * max(1, len(NEAR_RPC_URLS))
    async with httpx.AsyncClient(timeout=10) as http:
        for attempt in range(attempts):
            url = NEAR_RPC_URLS[attempt % len(NEAR_RPC_URLS)]
            try:
                resp = await http.post(url, json={"jsonrpc": "2.0", "id": "dash", "method": "query", "params": params})
            except httpx.HTTPError as e:
                last = f"{type(e).__name__}: {e}"
            else:
                if resp.status_code == 429 or resp.status_code >= 500:
                    last = f"HTTP {resp.status_code} from RPC"
                else:
                    try:
                        body = resp.json()
                    except ValueError:
                        last = f"HTTP {resp.status_code}: non-JSON RPC response"
                    else:
                        err = body.get("error")
                        if not err:
                            return body.get("result") or {}
                        cause = (err.get("cause") or {}).get("name", "")
                        if cause == "UNKNOWN_ACCOUNT":
                            return {"missing": "account"}
                        if cause == "UNKNOWN_ACCESS_KEY":
                            return {"missing": "key"}
                        last = json.dumps(err)
            if attempt + 1 < attempts:
                await asyncio.sleep(min(0.5 * (attempt + 1), 3))
    logger.warning("dash sign-in: view_access_key failed for %s: %s", account_id, last[:300])
    raise KeyCheckError(503, "rpc_unavailable")


async def check_key_owned(account_id: str, public_key: str, pk: bytes) -> None:
    """`public_key` is a FullAccess key of `account_id` on the final block. An Ed25519 implicit account that is not
    on chain yet (never funded) belongs to the key whose hex it is."""
    result = await _view_access_key(account_id, public_key)
    missing = result.get("missing")
    error = result.get("error")
    if error:  # old nodes: a missing key / account is a result, not an RPC error
        text = str(error)
        missing = "key" if "access key" in text else "account" if "does not exist" in text else None
        if missing is None:
            logger.warning("dash sign-in: view_access_key error for %s: %s", account_id, text[:300])
            raise KeyCheckError(503, "rpc_unavailable")
    elif not missing:
        if result.get("permission") == "FullAccess":
            return
        raise KeyCheckError(401, "key_not_owned")  # FunctionCall key
    if missing == "account" and _IMPLICIT_RE.match(account_id) and account_id == pk.hex():
        return
    raise KeyCheckError(401, "key_not_owned")
