"""Чистая часть покупки TEAR: borsh пула Intear DEX, комиссия на момент блока, XYK, решение «когда стрелять».

Всё повторяет контракт slimedragon.near/xyk (github.com/INTEARnear/dex, dexes/xyk) до единицы:
- комиссия получателя = floor(amount * fraction / 1e6), сумма по получателям вычитается из входа;
- в резерв входа уходит after_fees + доля Pool (комиссия остаётся в пуле и поднимает цену);
- out = floor(after * reserve_out / (reserve_in + after));
- протокол: 0.1% сверху (TEAR не в списке «стейблов»), пока расписание не кончилось — ещё 5% от
  запланированной доли переезжают из Pool протоколу (сумма не меняется);
- Scheduled: до start — стартовая комиссия, после end — конечная, между — линейно, floor.

Время везде в наносекундах цепи (block_timestamp). Локальное время переводим в «момент исполнения
нашей квитанции» через калибровку L = ts_блока_исполнения − локальное_время_отправки.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

FULL = 1_000_000
PROTOCOL_FEE = 1_000  # 0.1%
PROTOCOL_SHARE_PERCENT = 5
NEAR = 10 ** 24
NS = 1_000_000_000


# ── borsh ──────────────────────────────────────────────────────────────────────

class _Reader:
    def __init__(self, data: bytes) -> None:
        self.b = data
        self.i = 0

    def take(self, n: int) -> bytes:
        if self.i + n > len(self.b):
            raise ValueError("borsh: неожиданный конец данных")
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
        raise ValueError("Dynamic-комиссия в контракте не реализована")

    def describe(self) -> str:
        if self.kind == "fixed":
            return f"фикс {pct(self.fixed)}"
        if self.kind == "scheduled":
            return (f"{pct(self.start_f)} → {pct(self.end_f)} с {fmt_ts(self.start_ts)} до {fmt_ts(self.end_ts)} UTC "
                    f"({(self.end_ts - self.start_ts) // NS} с)")
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
    assets: dict[str, int]  # asset_id -> резерв
    fee_config: list[tuple[FeeReceiver, FeeAmount]]
    fee_config_version: str  # V1 | V2
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
    raise ValueError(f"borsh: неизвестный AssetId {tag}")


def _receiver(r: _Reader) -> FeeReceiver:
    tag = r.u8()
    if tag == 0:
        return FeeReceiver("account", r.string())
    if tag == 1:
        return FeeReceiver("pool")
    if tag == 2:
        return FeeReceiver("community", r.string())
    raise ValueError(f"borsh: неизвестный FeeReceiver {tag}")


def _fee_amount(r: _Reader) -> FeeAmount:
    tag = r.u8()
    if tag == 0:
        return FeeAmount("fixed", fixed=r.u32())
    if tag == 1:
        st, sf = r.u64(), r.u32()
        et, ef = r.u64(), r.u32()
        r.u8()  # curve: только Linear
        return FeeAmount("scheduled", start_ts=st, start_f=sf, end_ts=et, end_f=ef)
    if tag == 2:
        lo, hi = r.u32(), r.u32()
        return FeeAmount("dynamic", fixed=hi, start_f=lo)
    raise ValueError(f"borsh: неизвестный FeeAmount {tag}")


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
    raise ValueError(f"borsh: неизвестная FeeConfiguration {tag}")


def _asset_with_balance(r: _Reader) -> tuple[str, int]:
    a = _asset_id(r)
    return a, r.u128()


def decode_pool(pool_id: int, data: bytes) -> Pool | None:
    """Ответ dex_view get_pool: borsh Option<PoolView>."""
    r = _Reader(data)
    if r.u8() == 0:
        return None
    variant = r.u8()
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
    raise ValueError(f"borsh: неизвестный PoolView {variant}")


# ── комиссия и своп ──────────────────────────────────────────────────────────

REDUCE_ASSET_ACCOUNTS = ("17208628f84f5d6ad33f0da3bbbeb27ffcb398eac501a31bd6ad2011e36133a1", "usdt.tether-token.near",
                         "near")
REDUCE_ASSET_PARENTS = ("omft.near", "omni.hot.tg")
PROTOCOL_FEE_REDUCED = 1


def _asset_account(asset_id: str) -> str:
    return "near" if asset_id == "near" else asset_id.split(":")[1]


def protocol_fee_reduced(asset_ids) -> bool:
    """should_reduce_fee: все активы — «стейблы»/NEAR. Для NEAR/TEAR — нет (0.1%)."""
    for a in asset_ids:
        acc = _asset_account(a)
        if acc in REDUCE_ASSET_ACCOUNTS or any(acc.endswith("." + p) for p in REDUCE_ASSET_PARENTS):
            continue
        return False
    return True


def current_fees(pool: Pool, ts: int) -> list[tuple[bool, int]]:
    """(в_пул?, доля) — как FeeConfiguration::with_protocol_fee (без реферала)."""
    receivers = []
    base = PROTOCOL_FEE_REDUCED if protocol_fee_reduced(pool.assets) else PROTOCOL_FEE
    protocol = base if pool.fee_config else 0
    for rec, amount in pool.fee_config:
        frac = amount.fraction_at(ts)
        if pool.fee_config_version == "V2" and amount.kind == "scheduled" and ts < amount.end_ts:
            share = frac * PROTOCOL_SHARE_PERCENT // 100
            frac -= share
            protocol += share
        receivers.append((rec.kind == "pool", frac))
    receivers.append((False, protocol))
    return receivers


def total_fee(pool: Pool, ts: int) -> int:
    return sum(f for _, f in current_fees(pool, ts))


@dataclass(frozen=True)
class SwapResult:
    amount_in: int
    amount_out: int
    fee_total: int  # всё удержанное с входа
    new_in: int
    new_out: int


def swap_exact_in(reserve_in: int, reserve_out: int, amount_in: int, fees: list[tuple[bool, int]]) -> SwapResult:
    total = 0
    pool_fee = 0
    for to_pool, frac in fees:
        fa = amount_in * frac // FULL
        total += fa
        if to_pool:
            pool_fee += fa
    after = amount_in - total
    if reserve_in <= 0 or reserve_out <= 0 or after <= 0:
        return SwapResult(amount_in, 0, total, reserve_in, reserve_out)
    out = after * reserve_out // (reserve_in + after)
    return SwapResult(amount_in, out, total, reserve_in + after + pool_fee, reserve_out - out)


def buy(pool: Pool, asset_in: str, asset_out: str, amount_in: int, ts: int) -> SwapResult:
    return swap_exact_in(pool.reserve(asset_in), pool.reserve(asset_out), amount_in, current_fees(pool, ts))


# ── формат ───────────────────────────────────────────────────────────────────

def pct(fraction: int | float) -> str:
    return f"{fraction / FULL * 100:.2f}%"


def fmt_ts(ts_ns: int) -> str:
    import datetime as _dt
    return _dt.datetime.fromtimestamp(ts_ns / NS, _dt.timezone.utc).strftime("%H:%M:%S")


def fmt_amount(raw: int, decimals: int = 24, digits: int = 4) -> str:
    v = raw / 10 ** decimals
    if abs(v) >= 1000:
        return f"{v:,.0f}".replace(",", " ")
    return f"{v:.{digits}f}"


# ── стратегия ────────────────────────────────────────────────────────────────

@dataclass
class Params:
    amount_in: int = 22 * NEAR
    lead_s: float = 50.0          # плановое исполнение за столько секунд до конца расписания: впереди волн
                                  # на порогах 5/3/2% и у конца (модель: худший случай −4% вместо −28%); на Meteora
                                  # (N3ON, 3 мин) волна ботов началась за ~45 с до конца и собрала ~75% покупок
    max_fee: int = 80_000         # 8% — раньше этой комиссии не стреляем ни при каком раже
    slippage: float = 0.30        # допуск к ожиданию в constraint (min_out): чужие сделки в том же блоке
    slippage_step: float = 0.15   # +к допуску после каждого отката «Output amount is less than constraint»
    slippage_max: float = 0.60    # потолок допуска на повторных попытках
    max_price_x: float | None = None  # потолок эффективной цены к стартовой цене пула (None — без потолка)
    max_price: float | None = None  # абсолютный потолок NEAR за 1 TEAR (перекрывает max_price_x)
    rush_window_s: float = 3.0    # окно оценки притока чужих покупок
    rush_accel: float = 1.3       # ожидаем, что приток к плановому моменту только растёт
    rush_min_inflow_pct: float = 0.5  # шум: приток за окно меньше этой доли резерва NEAR — не ража
    rush_min_buys: int = 2        # одна покупка — ещё не ража (не экстраполируем разовую сделку)
    # второй уровень: комиссия max_fee..max_fee_sustained — только при доказанном ровном потоке покупок
    # (симуляция: при ровном потоке в середине окна 8% теряет до ×3 токенов, а простой подъём предела
    # ловит разовые всплески и теряет 9–15%)
    max_fee_sustained: int = 250_000  # 25%
    # 9 с / 5 покупок: на волнах вида N3ON (Meteora) вход через ~9 с, а не на пике через 15 с (−8…−28% против
    # −22…−60% к идеалу); кит посреди мелкого потока — худший случай −7%
    sustained_window_s: float = 9.0
    sustained_min_buys: int = 5
    sustained_min_share: float = 0.15  # каждая треть окна — не меньше этой доли притока (не один всплеск)
    sustained_min_inflow_pct: float = 0.25  # приток за 3 с ≥ этой доли резерва NEAR
    sustained_margin: float = 0.10  # ранний вход выгоднее плана минимум на 10%


@dataclass
class Flow:
    """Изменение резервов между двумя снимками пула."""
    t_local: float
    d_near: int
    d_token: int

    @property
    def kind(self) -> str:
        if self.d_near > 0 and self.d_token < 0:
            return "buy"
        if self.d_near < 0 and self.d_token > 0:
            return "sell"
        if self.d_near == 0 and self.d_token == 0:
            return "none"
        return "liquidity"


@dataclass
class Decision:
    fire: bool
    reason: str
    land_ts: int = 0
    fee_land: int = 0
    expected_out: int = 0
    min_out: int = 0
    eff_price: float = 0.0       # NEAR за 1 TEAR с учётом комиссии
    plan_land_ts: int | None = None
    plan_out: int = 0
    inflow_rate: float = 0.0     # yoctoNEAR/с чужих покупок за окно
    projected_plan_out: int = 0


def inflow_rate(flows: list[Flow], now_local: float, window_s: float) -> float:
    """Чистый приток NEAR от чужих покупок/продаж за окно, yoctoNEAR/с (не меньше 0)."""
    net = sum(f.d_near for f in flows if f.kind in ("buy", "sell") and now_local - f.t_local <= window_s)
    return max(0.0, net / window_s)


def slippage_for(params: Params, constraint_fails: int) -> float:
    """Допуск для очередной попытки: откат по цене = сильная ража, следующая попытка ещё дороже."""
    return min(params.slippage_max, params.slippage + params.slippage_step * constraint_fails)


def price_cap(params: Params, p0: float | None) -> float | None:
    if params.max_price is not None:
        return params.max_price
    if p0:
        return p0 * params.max_price_x if params.max_price_x else None
    return None


def decide(pool: Pool, near: str, token: str, params: Params, amount_in: int, now_local: float, latency_ns: int,
           flows: list[Flow], p0: float | None) -> Decision:
    """Стрелять ли сейчас. now_local — секунды (time.time()), latency_ns — калибровка L."""
    land = int(now_local * NS) + latency_ns
    if pool.reserve(near) <= 0 or pool.reserve(token) <= 0:
        return Decision(False, "пул пустой — ждём ликвидность", land)
    fees_land = current_fees(pool, land)
    fee_land = sum(f for _, f in fees_land)
    res = swap_exact_in(pool.reserve(near), pool.reserve(token), amount_in, fees_land)
    eff = (amount_in / res.amount_out) if res.amount_out else float("inf")
    end = pool.scheduled_end()
    plan_land = end - int(params.lead_s * NS) if end else None
    cap = price_cap(params, p0)
    min_out = int(res.amount_out * (1 - params.slippage))
    if cap:
        min_out = max(min_out, int(amount_in / cap))
    d = Decision(False, "", land, fee_land, res.amount_out, min_out, eff, plan_land)
    if plan_land is not None:
        d.plan_out = buy(pool, near, token, amount_in, plan_land).amount_out

    if fee_land > max(params.max_fee, params.max_fee_sustained):
        d.reason = f"комиссия при исполнении {pct(fee_land)} > допустимых {pct(params.max_fee)}"
        return d
    if cap and eff > cap:
        d.reason = f"дорого: эффективная цена {eff:.8f} > потолка {cap:.8f}"
        return d
    fast = fee_land <= params.max_fee
    if (plan_land is None or land >= plan_land) and fast:
        d.fire = True
        d.reason = "плановый момент" if plan_land is not None else "расписания нет, комиссия в пределах — сразу"
        return d

    if plan_land is None or land >= plan_land:  # план наступил, но комиссия выше max_fee (Fixed/растянули окно)
        d.reason = f"комиссия при исполнении {pct(fee_land)} > допустимых {pct(params.max_fee)}"
        return d

    # ража: приток чужих покупок такой, что ждать до плана хуже, чем зайти сейчас
    if fast:
        window, min_buys, accel, margin = params.rush_window_s, params.rush_min_buys, params.rush_accel, 0.0
        min_inflow_pct = params.rush_min_inflow_pct
    else:
        window, min_buys, accel, margin = (params.sustained_window_s, params.sustained_min_buys, 1.0,
                                           params.sustained_margin)
        min_inflow_pct = params.sustained_min_inflow_pct
    rate = inflow_rate(flows, now_local, window)
    d.inflow_rate = rate
    buys = sum(1 for f in flows if f.kind == "buy" and now_local - f.t_local <= window)
    quiet = rate * 3 < pool.reserve(near) * min_inflow_pct / 100 or buys < min_buys
    if not fast and not quiet:
        third = window / 3
        parts = [sum(f.d_near for f in flows if f.kind in ("buy", "sell")
                     and k * third <= now_local - f.t_local < (k + 1) * third) for k in range(3)]
        total = sum(parts)
        quiet = total <= 0 or min(parts) < total * params.sustained_min_share
    if quiet:
        d.reason = ("ждём план, ражи нет" if fast else
                    f"комиссия {pct(fee_land)} > {pct(params.max_fee)}: ждём, ровного потока покупок нет")
        return d
    wait_s = (plan_land - land) / NS
    others = int(rate * accel * wait_s)
    mid = land + (plan_land - land) // 2
    after_others = buy(pool, near, token, others, mid)
    proj_pool_in, proj_pool_out = after_others.new_in, after_others.new_out
    proj = swap_exact_in(proj_pool_in, proj_pool_out, amount_in, current_fees(pool, plan_land))
    d.projected_plan_out = proj.amount_out
    if res.amount_out >= proj.amount_out * (1 + margin):
        d.fire = True
        d.reason = (f"{'ража' if fast else 'ровный поток'}: приток {rate / NEAR:.1f} NEAR/с — к плану получили бы "
                    f"{fmt_amount(proj.amount_out)} против {fmt_amount(res.amount_out)} сейчас")
        return d
    d.reason = (f"ждём план: приток {rate / NEAR:.1f} NEAR/с, к плану {fmt_amount(proj.amount_out)} "
                f"> {fmt_amount(res.amount_out)} сейчас")
    return d
