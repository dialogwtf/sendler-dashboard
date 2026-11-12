"""Intear DEX (dex.intear.near) pools: borsh decoding of pool state and of the `dex_events_borsh` event
(lz4 + borsh, the only pool event since the contract update of 2026-11-01; all pools are format V3 —
PoolView 4/5/6). Mirrors the contract slimedragon.near/xyk (github.com/INTEARnear/dex, dexes/xyk).
"""
from __future__ import annotations

import base64
import struct
from dataclasses import dataclass, field

FULL = 1_000_000
PROTOCOL_FEE = 1_000  # 0.1%
PROTOCOL_SHARE_PERCENT = 5
PROTOCOL_SHARE_V3 = 20  # % of each receiver's fee (the contract's PROTOCOL_FEE_SHARE)
XYK_DEX = ("slimedragon.near", "xyk")
NEAR = 10 ** 24
NS = 1_000_000_000


# ── borsh ──────────────────────────────────────────────────────────────────────

class _Reader:
    def __init__(self, data: bytes) -> None:
        self.b = data
        self.i = 0

    def take(self, n: int) -> bytes:
        if self.i + n > len(self.b):
            raise ValueError("borsh: unexpected end of data")
        out = self.b[self.i:self.i + n]
        self.i += n
        return out

    def u8(self) -> int:
        return self.take(1)[0]

    def u32(self) -> int:
        return struct.unpack("<I", self.take(4))[0]

    def u64(self) -> int:
        return struct.unpack("<Q", self.take(8))[0]

    def u128(self) -> int:
        return int.from_bytes(self.take(16), "little")

    def string(self) -> str:
        return self.take(self.u32()).decode()

    def boolean(self) -> bool:
        return self.u8() != 0


@dataclass(frozen=True)
class FeeAmount:
    kind: str  # fixed | scheduled | dynamic
    fixed: int = 0
    start_ts: int = 0
    start_f: int = 0
    end_ts: int = 0
    end_f: int = 0

    def fraction_at(self, ts: int) -> int:
        if self.kind == "fixed":
            return self.fixed
        if self.kind == "scheduled":
            if ts < self.start_ts:
                return self.start_f
            if ts >= self.end_ts:
                return self.end_f
            return self.start_f - (self.start_f - self.end_f) * (ts - self.start_ts) // (self.end_ts - self.start_ts)
        raise ValueError("Dynamic fee is not implemented in the contract")

    def describe(self) -> str:
        if self.kind == "fixed":
            return f"fixed {pct(self.fixed)}"
        if self.kind == "scheduled":
            return (f"{pct(self.start_f)} → {pct(self.end_f)} from {fmt_ts(self.start_ts)} to {fmt_ts(self.end_ts)} UTC "
                    f"({(self.end_ts - self.start_ts) // NS} s)")
        return "dynamic"


@dataclass(frozen=True)
class FeeReceiver:
    kind: str  # account | pool | community
    account: str | None = None

    def describe(self) -> str:
        return "Pool" if self.kind == "pool" else f"{self.kind}:{self.account}"


@dataclass
class Pool:
    pool_id: int
    kind: str  # private | public | launch | launch_v2
    assets: dict[str, int]  # asset_id -> reserve
    fee_config: list[tuple[FeeReceiver, FeeAmount]]
    fee_config_version: str  # V1 | V2 | V3
    owner_id: str | None = None
    locked: bool | None = None
    raw: bytes = field(default=b"", repr=False)

    def reserve(self, asset_id: str) -> int:
        return self.assets.get(asset_id, 0)

    def has(self, asset_id: str) -> bool:
        return asset_id in self.assets

    def fee_signature(self) -> tuple:
        return (self.fee_config_version, tuple(self.fee_config))

    def scheduled_end(self) -> int | None:
        ends = [a.end_ts for _, a in self.fee_config if a.kind == "scheduled"]
        return max(ends) if ends else None

    def scheduled_start(self) -> int | None:
        starts = [a.start_ts for _, a in self.fee_config if a.kind == "scheduled"]
        return min(starts) if starts else None


def _asset_id(r: _Reader) -> str:
    tag = r.u8()
    if tag == 0:
        return "near"
    if tag == 1:
        return "nep141:" + r.string()
    if tag == 2:
        c = r.string()
        return f"nep245:{c}:{r.string()}"
    if tag == 3:
        c = r.string()
        return f"nep171:{c}:{r.string()}"
    raise ValueError(f"borsh: unknown AssetId {tag}")


def _receiver(r: _Reader) -> FeeReceiver:
    tag = r.u8()
    if tag == 0:
        return FeeReceiver("account", r.string())
    if tag == 1:
        return FeeReceiver("pool")
    if tag == 2:
        return FeeReceiver("community", r.string())
    if tag == 3:  # V3: the receiver and the payout method do not affect the swap's output
        account = r.string()
        r.u8(), r.u8()  # payout_type, claim_mode
        return FeeReceiver("account", account)
    if tag == 4:
        return FeeReceiver("protocol")
    raise ValueError(f"borsh: unknown FeeReceiver {tag}")


def _fee_amount(r: _Reader) -> FeeAmount:
    tag = r.u8()
    if tag == 0:
        return FeeAmount("fixed", fixed=r.u32())
    if tag == 1:
        st, sf = r.u64(), r.u32()
        et, ef = r.u64(), r.u32()
        r.u8()  # curve: Linear only
        return FeeAmount("scheduled", start_ts=st, start_f=sf, end_ts=et, end_f=ef)
    if tag == 2:
        lo, hi = r.u32(), r.u32()
        return FeeAmount("dynamic", fixed=hi, start_f=lo)
    raise ValueError(f"borsh: unknown FeeAmount {tag}")


def _current_fees(r: _Reader) -> None:
    for _ in range(r.u32()):
        _receiver(r)
        r.u32()


def _fee_configuration(r: _Reader) -> tuple[str, list[tuple[FeeReceiver, FeeAmount]]]:
    tag = r.u8()
    out = []
    if tag == 0:
        for _ in range(r.u32()):
            rec = _receiver(r)
            out.append((rec, FeeAmount("fixed", fixed=r.u32())))
        return "V1", out
    if tag == 1:
        for _ in range(r.u32()):
            rec = _receiver(r)
            out.append((rec, _fee_amount(r)))
        return "V2", out
    raise ValueError(f"borsh: unknown FeeConfiguration {tag}")


def _fee_receivers(r: _Reader) -> list[tuple[FeeReceiver, FeeAmount]]:
    """The contract's FeeConfigurationV2 (our V3): a list without a version tag."""
    return [(_receiver(r), _fee_amount(r)) for _ in range(r.u32())]


def _asset_with_balance(r: _Reader) -> tuple[str, int]:
    a = _asset_id(r)
    return a, r.u128()


def decode_pool(pool_id: int, data: bytes) -> Pool | None:
    """Answer of dex_view get_pool: borsh Option<PoolView>."""
    r = _Reader(data)
    if r.u8() == 0:
        return None
    return _pool_view(r, pool_id, data)


def _pool_view(r: _Reader, pool_id: int, data: bytes) -> Pool:
    variant = r.u8()
    if variant in (4, 5):  # Private / Public, V3
        a0, a1 = _asset_with_balance(r), _asset_with_balance(r)
        _current_fees(r)
        cfg = _fee_receivers(r)
        owner, locked = None, None
        if variant == 4:
            owner, locked = r.string(), r.boolean()
        elif r.u8():  # total_shares: Option
            r.u128()
        return Pool(pool_id, "private" if variant == 4 else "public", {a0[0]: a0[1], a1[0]: a1[1]}, cfg, "V3",
                    owner, locked, data)
    if variant == 6:  # Launch, V3: the quote asset comes first
        qa, la = _asset_with_balance(r), _asset_with_balance(r)
        _current_fees(r)
        cfg = _fee_receivers(r)
        r.u128()  # phantom_liquidity
        return Pool(pool_id, "launch_v2", {qa[0]: qa[1], la[0]: la[1]}, cfg, "V3", raw=data)
    if variant in (0, 1):  # Private / Public
        a0, a1 = _asset_with_balance(r), _asset_with_balance(r)
        _current_fees(r)
        ver, cfg = _fee_configuration(r)
        owner, locked = None, None
        if variant == 0:
            owner, locked = r.string(), r.boolean()
        else:
            if r.u8():
                r.u128()
        return Pool(pool_id, "private" if variant == 0 else "public", {a0[0]: a0[1], a1[0]: a1[1]}, cfg, ver,
                    owner, locked, data)
    if variant == 2:  # Launch (NEAR)
        near_amount = r.u128()
        la = _asset_with_balance(r)
        _current_fees(r)
        ver, cfg = _fee_configuration(r)
        return Pool(pool_id, "launch", {"near": near_amount, la[0]: la[1]}, cfg, ver, raw=data)
    if variant == 3:  # LaunchV2
        qa, la = _asset_with_balance(r), _asset_with_balance(r)
        _current_fees(r)
        ver, cfg = _fee_configuration(r)
        return Pool(pool_id, "launch_v2", {qa[0]: qa[1], la[0]: la[1]}, cfg, ver, raw=data)
    raise ValueError(f"borsh: unknown PoolView {variant}")


# ── pool events ──────────────────────────────────────────────────────────────

def lz4_unpack(data: bytes) -> bytes:
    """An lz4 block with the length in front (u32 LE) - the contract's lz4_flex::compress_prepend_size. Broken data - ValueError."""
    if len(data) < 4:
        raise ValueError("lz4: no length")
    size = struct.unpack("<I", data[:4])[0]
    out = bytearray()
    i = 4
    try:
        while i < len(data):
            token = data[i]
            i += 1
            n = token >> 4
            if n == 15:
                while data[i] == 255:
                    n += 255
                    i += 1
                n += data[i]
                i += 1
            out += data[i:i + n]
            i += n
            if i >= len(data):
                break
            offset = data[i] | data[i + 1] << 8
            i += 2
            if not 0 < offset <= len(out):
                raise ValueError("lz4: bad offset")
            n = token & 15
            if n == 15:
                while data[i] == 255:
                    n += 255
                    i += 1
                n += data[i]
                i += 1
            n += 4
            if len(out) + n > size:
                raise ValueError("lz4: longer than the declared length")
            for _ in range(n):  # the source may overlap with what is being written
                out.append(out[-offset])
    except IndexError as e:
        raise ValueError("lz4: data cut off") from e
    if len(out) != size:
        raise ValueError("lz4: length mismatch")
    return bytes(out)


def pool_updates(data: str) -> list[Pool]:
    """Pools from a `dex_events_borsh` event: Vec<DexEventData> -> XykDexEvent::PoolUpdated. Foreign format - ValueError."""
    r = _Reader(lz4_unpack(base64.b64decode(data, validate=True)))
    out = []
    for _ in range(r.u32()):
        dex = r.string(), r.string()
        event = r.take(r.u32())
        for _ in range(2):  # referrer, user
            if r.u8():
                r.string()
        if dex == XYK_DEX and event[:1] == b"\0":  # PoolUpdated - the first variant of XykDexEvent
            e = _Reader(event)
            e.u8()
            pool_id = e.u32()
            out.append(_pool_view(e, pool_id, event))
    return out
