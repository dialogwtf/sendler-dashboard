"""Deep-link codes of the Sendler trade bot: the site's "Buy in the bot" button opens `t.me/<bot>?start=t_<code>`.

The code is a pure function of the token id, so the dashboard writes `sniper_token_links` {_id: code, token} for every
traded or launched token and the bot resolves the link without asking anyone.
"""
from __future__ import annotations

import base64
import hashlib

CODE_LEN = 10


def token_code(token: str) -> str:
    return base64.b32encode(hashlib.sha256(token.encode()).digest()).decode().lower()[:CODE_LEN]
