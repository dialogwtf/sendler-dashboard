"""Слежение за кошельками: покупки токенов отслеживаемыми кошельками — из того же потока блоков, без RPC.

Своп виден в квитанции площадки:
- Rhea DCL (`dclv2.ref-labs.near`) — событие `dcl.ref` `swap` (`swapper`, `token_in/out`, `amount_in/out`);
- Intear DEX (`dex.intear.near`) — событие `inteardex` `swap` (`trader`, `request.asset_in/out`); вложенный
  `dex_event` того же свопа не считаем;
- Rhea v2 (`v2.ref-finance.near`) — лог `Swapped A X for B Y`; кошелёк — `sender_id` из `ft_on_transfer` или
  `predecessor` прямого `swap`.
Кошелёк совпадает по исполнителю свопа или по подписанту транзакции: агрегаторы (aggregatedex.near) свопают от
своего имени, а подписывает кошелёк. Ноги одной транзакции (маршрут через несколько пулов и площадок, в разных
блоках) сводятся в чистый поток: что ушло и что пришло. Покупка — пришёл не котируемый актив (не NEAR/wNEAR/
стейблы). Свой своп кошелька сообщается сразу; через агрегатор — через SETTLE_BLOCKS блоков после последней ноги,
когда маршрут закончился.

Регистрация в токене (`storage_deposit` на контракте токена) — частый шаг перед покупкой, отдельной транзакцией.
Сигнал — только своя регистрация: account_id — сам кошелёк, и вызывает его он сам (подписант или predecessor —
мета-транзакция через релеер). Чужая регистрация «на всякий случай» — не сигнал: так Nearly перед выплатой
комиссии создателю (`push_creator_token_fees`: storage_deposit + ft_transfer memo "fees", подписант
nearlytrade.near). Ждём REG_SETTLE_BLOCKS: если в той же транзакции был своп, это часть покупки (о ней — свой
алерт). Повторная регистрация — не сигнал: лог «already registered» есть не у всех токенов (у Nearly нет), поэтому
главный признак — токен вернул вызывающему весь депозит (дочерняя квитанция Transfer на ту же сумму, через блок).
Котируемые активы (wNEAR, стейблы) и контракт без ft_metadata (не токен) — тоже не сигнал.

Символ и decimals — из ft_metadata индексатора (Mongo) или кэша Tokens, цена NEAR — из ft_prices: тоже без RPC.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import logging
import re
import time
from dataclasses import dataclass, field

from pymongo import ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError

from utils.rpc_meter import labeled

from .feed import DCL, INTEAR_ENGINE, _calls, _events, _items
from .store import bson_safe, now
from .tokens import launchpad_of, valid_account

logger = logging.getLogger("sniper")

REF = "v2.ref-finance.near"
VENUES = {DCL: "Rhea DCL", INTEAR_ENGINE: "Intear DEX", REF: "Rhea"}
NEAR = "near"  # NEAR и wNEAR — один актив
USDC = "17208628f84f5d6ad33f0da3bbbeb27ffcb398eac501a31bd6ad2011e36133a1"
USDT = "usdt.tether-token.near"
KNOWN = {NEAR: ("NEAR", 24), USDC: ("USDC", 6), USDT: ("USDT", 6),
         "a0b86991c6218b36c1d19d4a2e9eb0ce3606eb48.factory.bridge.near": ("USDC.e", 6),
         "dac17f958d2ee523a2206206994597c13d831ec7.factory.bridge.near": ("USDT.e", 6)}
QUOTES = frozenset(KNOWN)  # получить их — не покупка (продажа или обмен стейблов)
SETTLE_BLOCKS = 3  # маршрут агрегатора: ноги приходят через 1–2 блока друг за другом
REG_SETTLE_BLOCKS = SETTLE_BLOCKS + 2  # регистрация: своп той же транзакции приходит на 1–3 блока позже
REG_SKIP = frozenset({*VENUES, "wrap.near", *QUOTES})
PENDING_MAX_BLOCKS = 60
DONE_KEEP_BLOCKS = 200
RELOAD_S = 30.0
MAX_WALLETS = 100
BUYS_TTL_S = 30 * 86400
REF_SWAPPED = re.compile(r"^Swapped (\d+) (\S+) for (\d+) ([^\s,]+)")
NEARBLOCKS = re.compile(r"nearblocks\.io/(?:[a-z]{2}/)?(?:address|account)/([a-z0-9._-]+)", re.I)


@dataclass
class Leg:
    token_in: str
    amount_in: int
    token_out: str
    amount_out: int
    venue: str


@dataclass
class WalletBuy:
    wallet: str
    tx_hash: str
    block: int
    block_ts: float
    venues: list[str]
    bought: list[tuple[str, int]]  # [(токен, сколько)] — по убыванию не сортируем: обычно один
    spent: list[tuple[str, int]]

    @property
    def near_spent(self) -> int:
        return next((a for t, a in self.spent if t == NEAR), 0)


@dataclass
class WalletRegistration:
    wallet: str
    token: str
    tx_hash: str
    block: int
    block_ts: float
    signer: str | None = None  # кто подписал, если не сам кошелёк (агрегатор, чужая оплата)


@dataclass
class _Pending:
    block: int
    block_ts: float
    last: int
    direct: bool = False
    legs: list[Leg] = field(default_factory=list)


def asset(a: str | None) -> str:
    """wrap.near, near, nep141:x → единый id актива."""
    a = (a or "").removeprefix("nep141:")
    return NEAR if a in ("near", "wrap.near") else a


def wid_of(account: str) -> str:
    """Короткий id кошелька для кнопок (callback_data ≤ 64 байт)."""
    return hashlib.sha1(account.encode()).hexdigest()[:10]


def extract_wallet(text: str) -> str | None:
    """Аккаунт из текста: «whale.near», «@whale.near», ссылка nearblocks."""
    t = (text or "").strip()
    m = NEARBLOCKS.search(t)
    acc = (m.group(1) if m else t.lstrip("@")).lower()
    return acc if valid_account(acc) else None


def swap_legs(executor: str, receipt: dict, outcome: dict) -> list[tuple[str | None, Leg]]:
    """Свопы квитанции площадки: [(кто свопает, нога)]."""
    venue = VENUES.get(executor)
    if venue is None:
        return []
    out: list[tuple[str | None, Leg]] = []
    logs = outcome.get("logs") or []
    if executor == REF:
        calls = _calls(receipt)
        sender = next((a.get("sender_id") for m, a in calls if m == "ft_on_transfer"), None)
        actor = sender or receipt.get("predecessor_id")
        for line in logs:
            m = REF_SWAPPED.match(line)
            if m:
                out.append((actor, Leg(asset(m.group(2)), int(m.group(1)), asset(m.group(4)), int(m.group(3)), venue)))
        return out
    for ev in _events(logs):
        if executor == DCL and (ev.get("standard"), ev.get("event")) == ("dcl.ref", "swap"):
            for d in _items(ev.get("data")):
                out.append((d.get("swapper"), Leg(asset(d.get("token_in")), int(d.get("amount_in") or 0),
                                                  asset(d.get("token_out")), int(d.get("amount_out") or 0), venue)))
        elif executor == INTEAR_ENGINE and (ev.get("standard"), ev.get("event")) == ("inteardex", "swap"):
            for d in _items(ev.get("data")):
                req = d.get("request") or {}
                out.append((d.get("trader"), Leg(asset(req.get("asset_in")), int(d.get("amount_in") or 0),
                                                 asset(req.get("asset_out")), int(d.get("amount_out") or 0), venue)))
    return out


def registered_accounts(receipt: dict, outcome: dict) -> list[tuple[str, int]]:
    """Кого регистрирует storage_deposit квитанции: [(account_id или вызывающий, депозит)]; [] — не он или
    повторный по логу. Имя метода проверяется до разбора аргументов: так проходит каждая квитанция блока."""
    actions = ((((receipt.get("receipt") or {}).get("Action")) or {}).get("actions")) or []
    deposits = [int(fc.get("deposit") or 0) for a in actions if isinstance(a, dict)
                for fc in [a.get("FunctionCall") or {}] if fc.get("method_name") == "storage_deposit"]
    if not deposits:
        return []
    if any("already registered" in line for line in outcome.get("logs") or []):
        return []
    pred = receipt.get("predecessor_id")
    accounts = [a.get("account_id") or pred for m, a in _calls(receipt) if m == "storage_deposit"]
    return list(zip(accounts, deposits))


def refund_of(receipt: dict) -> int | None:
    """Сумма квитанции-перевода (возврат депозита); None — не перевод."""
    actions = ((((receipt.get("receipt") or {}).get("Action")) or {}).get("actions")) or []
    if len(actions) != 1 or not isinstance(actions[0], dict) or "Transfer" not in actions[0]:
        return None
    return int((actions[0]["Transfer"] or {}).get("deposit") or 0)


def net_flow(legs: list[Leg]) -> tuple[list[tuple[str, int]], list[tuple[str, int]]]:
    """Чистый поток по ногам → (пришло, ушло); промежуточные активы маршрута взаимно гасятся."""
    net: dict[str, int] = {}
    for leg in legs:
        net[leg.token_in] = net.get(leg.token_in, 0) - leg.amount_in
        net[leg.token_out] = net.get(leg.token_out, 0) + leg.amount_out
    return [(t, a) for t, a in net.items() if a > 0], [(t, -a) for t, a in net.items() if a < 0]


class SwapTracker:
    """Состояние между блоками: ноги транзакции копятся, пока маршрут не закончится."""

    def __init__(self) -> None:
        self._pending: dict[tuple[str, str], _Pending] = {}
        self._done: dict[tuple[str, str], int] = {}  # уже разобрано (сообщено или не покупка) → высота
        self._regs: dict[tuple[str, str, str], WalletRegistration] = {}  # (кошелёк, токен, tx) → ждёт своп той же tx
        self._regs_done: dict[tuple[str, str, str], int] = {}
        self._refunds: dict[str, tuple[tuple[str, str, str], int, int]] = {}  # дочерняя квитанция → (ключ, депозит, высота)
        self.registrations: list[WalletRegistration] = []  # готовые к алерту — забирает WalletWatcher

    def parse(self, block: dict, tracked) -> list[WalletBuy]:
        header = (block.get("block") or {}).get("header") or {}
        height = int(header.get("height") or 0)
        ts = int(header.get("timestamp") or 0) / 1e9
        for shard in block.get("shards") or []:
            for reo in shard.get("receipt_execution_outcomes") or []:
                outcome = (reo.get("execution_outcome") or {}).get("outcome") or {}
                ex = outcome.get("executor_id")
                status = outcome.get("status") or {}
                if "SuccessValue" not in status and "SuccessReceiptId" not in status:
                    continue
                receipt = reo.get("receipt") or {}
                if self._refunds and receipt.get("receipt_id") in self._refunds:
                    self._refunded(receipt, height)
                if ex not in VENUES:
                    if ex and ex not in REG_SKIP:
                        self._registration(ex, reo, receipt, outcome, height, ts, tracked)
                    continue
                try:
                    legs = swap_legs(ex, receipt, outcome)
                except (KeyError, TypeError, ValueError) as e:
                    logger.debug("sniper wallets: квитанция %s не разобрана: %s", reo.get("tx_hash"), e)
                    continue
                if not legs:
                    continue
                signer = (((receipt.get("receipt") or {}).get("Action")) or {}).get("signer_id")
                tx_hash = reo.get("tx_hash") or receipt.get("receipt_id") or ""
                for actor, leg in legs:
                    wallet = actor if actor in tracked else signer if signer in tracked else None
                    if wallet is None or (wallet, tx_hash) in self._done:
                        continue
                    p = self._pending.setdefault((wallet, tx_hash), _Pending(height, ts, height))
                    p.legs.append(leg)
                    p.last = height
                    p.direct = p.direct or actor == wallet
        buys = self._flush(height)
        self._flush_regs(height)
        return buys

    def _registration(self, token: str, reo: dict, receipt: dict, outcome: dict, height: int, ts: float,
                      tracked) -> None:
        accounts = registered_accounts(receipt, outcome)
        if not accounts:
            return
        signer = (((receipt.get("receipt") or {}).get("Action")) or {}).get("signer_id")
        pred = receipt.get("predecessor_id")
        tx_hash = reo.get("tx_hash") or receipt.get("receipt_id") or ""
        children = outcome.get("receipt_ids") or []
        for wallet, deposit in accounts:
            if wallet not in tracked or wallet not in (signer, pred):
                continue  # регистрирует не сам кошелёк (выплата комиссии, чужой перевод) — не сигнал
            key = (wallet, token, tx_hash)
            if key in self._regs or key in self._regs_done:
                continue
            self._regs[key] = WalletRegistration(wallet, token, tx_hash, height, ts,
                                                 signer if signer != wallet else None)
            if deposit > 0:
                for child in children:
                    self._refunds[child] = (key, deposit, height)

    def _refunded(self, receipt: dict, height: int) -> None:
        """Токен вернул вызывающему весь депозит — кошелёк уже был зарегистрирован: регистрацию не сообщаем."""
        key, deposit, _ = self._refunds.pop(receipt["receipt_id"])
        if refund_of(receipt) == deposit and receipt.get("predecessor_id") == key[1] and key in self._regs:
            self._regs.pop(key)
            self._regs_done[key] = height

    def _flush_regs(self, height: int) -> None:
        for key, r in list(self._regs.items()):
            swap_key = (r.wallet, r.tx_hash)
            if swap_key in self._pending and height - r.block <= PENDING_MAX_BLOCKS:
                continue  # маршрут той же транзакции ещё идёт
            if swap_key not in self._done and height - r.block < REG_SETTLE_BLOCKS:
                continue
            self._regs.pop(key)
            self._regs_done[key] = height
            if swap_key not in self._done and swap_key not in self._pending:
                self.registrations.append(r)  # своп в той же транзакции — это покупка, о ней свой алерт
        for key in [k for k, h in self._regs_done.items() if height - h > DONE_KEEP_BLOCKS]:
            self._regs_done.pop(key, None)
        for rid in [r for r, (_, _, h) in self._refunds.items() if height - h > PENDING_MAX_BLOCKS]:
            self._refunds.pop(rid, None)

    def take_registrations(self) -> list[WalletRegistration]:
        out, self.registrations = self.registrations, []
        return out

    def _flush(self, height: int) -> list[WalletBuy]:
        out = []
        for key, p in list(self._pending.items()):
            bought, spent = net_flow(p.legs)
            buys = [(t, a) for t, a in bought if t not in QUOTES]
            settled = height - p.last >= SETTLE_BLOCKS
            if not (settled or (p.direct and buys)):
                continue
            self._pending.pop(key)
            self._done[key] = height
            if buys:
                venues = list(dict.fromkeys(leg.venue for leg in p.legs))
                out.append(WalletBuy(key[0], key[1], p.block, p.block_ts, venues, buys, spent))
        for key in [k for k, p in self._pending.items() if height - p.block > PENDING_MAX_BLOCKS]:
            self._pending.pop(key, None)
        for key in [k for k, h in self._done.items() if height - h > DONE_KEEP_BLOCKS]:
            self._done.pop(key, None)
        return out


class WalletStore:
    """sniper_wallets — кого отслеживаем ({_id: аккаунт, wid, label, paused, min_near, buys (счётчик), last_buy,
    buy_alerts (False — о покупках не сообщать), regs (False — о регистрациях не сообщать)}); запуски кошелька
    (автопокупка) — в sniper_watch kind=creator, та же карточка в Telegram;
    sniper_wallet_buys — их покупки ({_id: tx:кошелёк}, TTL BUYS_TTL_S): дубль блока не даёт второго алерта."""

    def __init__(self, db) -> None:
        self.col = db["sniper_wallets"]
        self.buys = db["sniper_wallet_buys"]
        self.db = db

    async def ensure_indexes(self) -> None:
        await self.col.create_index([("wid", ASCENDING)], unique=True)
        await self.buys.create_index([("wallet", ASCENDING), ("at", DESCENDING)])
        await self.buys.create_index([("at", ASCENDING)], expireAfterSeconds=BUYS_TTL_S)

    async def list(self) -> list[dict]:
        return await self.col.find({}).sort("added_at", ASCENDING).to_list(MAX_WALLETS)

    async def get(self, wid: str) -> dict | None:
        return await self.col.find_one({"wid": wid})

    async def add(self, account: str, label: str | None = None, alerts: bool = True) -> bool:
        """False — уже в списке. alerts=False — без алертов о покупках и регистрациях (кошелёк добавлен ради
        автопокупки его запусков)."""
        flags = {} if alerts else {"buy_alerts": False, "regs": False}
        try:
            await self.col.insert_one({"_id": account, "wid": wid_of(account), "label": label, "paused": False,
                                       "min_near": 0.0, "buys": 0, "added_at": now(), **flags})
            return True
        except DuplicateKeyError:
            return False

    async def count(self) -> int:
        return await self.col.count_documents({})

    async def update(self, wid: str, **fields) -> None:
        await self.col.update_one({"wid": wid}, {"$set": bson_safe(fields)})

    async def delete(self, wid: str) -> None:
        await self.col.delete_one({"wid": wid})

    async def save_buy(self, buy: WalletBuy, symbols: dict[str, str]) -> bool:
        """False — эту покупку уже записали (блок пришёл повторно)."""
        at = dt.datetime.fromtimestamp(buy.block_ts, dt.timezone.utc) if buy.block_ts else now()
        doc = {"_id": f"{buy.tx_hash}:{buy.wallet}", "wallet": buy.wallet, "tx_hash": buy.tx_hash,
               "block": buy.block, "at": at, "venues": buy.venues,
               "bought": [{"token": t, "amount": str(a), "symbol": symbols.get(t)} for t, a in buy.bought],
               "spent": [{"token": t, "amount": str(a), "symbol": symbols.get(t)} for t, a in buy.spent]}
        try:
            await self.buys.insert_one(doc)
        except DuplicateKeyError:
            return False
        token, _ = buy.bought[0]
        await self.col.update_one({"_id": buy.wallet}, {"$inc": {"buys": 1}, "$set": {
            "last_buy": {"at": at, "token": token, "symbol": symbols.get(token)}}})
        return True

    async def save_registration(self, r: WalletRegistration, symbol: str | None) -> bool:
        """Регистрация — в той же коллекции (kind: register); False — уже записана."""
        at = dt.datetime.fromtimestamp(r.block_ts, dt.timezone.utc) if r.block_ts else now()
        try:
            await self.buys.insert_one({"_id": f"{r.tx_hash}:{r.wallet}:reg:{r.token}", "kind": "register",
                                        "wallet": r.wallet, "tx_hash": r.tx_hash, "block": r.block, "at": at,
                                        "token": r.token, "symbol": symbol, "signer": r.signer})
        except DuplicateKeyError:
            return False
        await self.col.update_one({"_id": r.wallet}, {"$set": {
            "last_reg": {"at": at, "token": r.token, "symbol": symbol}}})
        return True

    async def recent_buys(self, account: str, limit: int = 5) -> list[dict]:
        return await self.buys.find({"wallet": account, "kind": {"$ne": "register"}}).sort(
            "at", DESCENDING).to_list(limit)

    async def meta(self, tokens: list[str]) -> dict[str, tuple[str, int]]:
        docs = await self.db["ft_metadata"].find({"_id": {"$in": tokens}}, {"symbol": 1, "decimals": 1}).to_list(None)
        return {d["_id"]: (str(d.get("symbol") or "?")[:24], int(d["decimals"])) for d in docs
                if d.get("decimals") is not None}

    async def near_usd(self) -> float | None:
        doc = await self.db["ft_prices"].find_one({"_id": "wrap.near"}, {"price_usd": 1})
        return float(doc["price_usd"]) if doc and doc.get("price_usd") else None


@dataclass
class BuyView:
    """Покупка для алерта: суммы уже с символами и decimals (None — метаданных нет, сумма не показывается)."""
    buy: WalletBuy
    wallet: dict
    meta: dict[str, tuple[str, int]]
    near_usd: float | None = None

    def symbol(self, token: str) -> str | None:
        hit = self.meta.get(token)
        return hit[0] if hit else None


@dataclass
class RegView:
    reg: WalletRegistration
    wallet: dict
    symbol: str | None


class WalletWatcher:
    """Подписчик потока блоков: покупки отслеживаемых кошельков → sink.wallet_alert(BuyView).

    Разбор блока синхронный и дешёвый (только квитанции трёх площадок); Mongo и Telegram — в фоновых задачах:
    поток ждёт подписчиков по очереди, и лента запусков не должна ждать алертов."""

    def __init__(self, store: WalletStore, sink, tokens=None) -> None:
        self.store = store
        self.sink = sink
        self.tokens = tokens  # Tokens: метаданные из кэша, без RPC
        self.tracker = SwapTracker()
        self.tracked: dict[str, dict] = {}  # аккаунт → запись (без paused)
        self._loaded = 0.0
        self._reloading = False
        self.background = True
        self._tasks: set[asyncio.Task] = set()

    async def reload(self) -> None:
        docs = await self.store.list()
        self.tracked = {d["_id"]: d for d in docs if not d.get("paused")
                        and (d.get("buy_alerts") is not False or d.get("regs") is not False)}
        self._loaded = time.time()

    async def handle(self, block: dict) -> None:
        if time.time() - self._loaded > RELOAD_S and not self._reloading:
            self._reloading = True
            await self._spawn(self._reload_quiet())
        if not self.tracked:
            return
        for buy in self.tracker.parse(block, self.tracked):
            await self._spawn(self.process(buy))
        for reg in self.tracker.take_registrations():
            await self._spawn(self.process_registration(reg))

    async def _reload_quiet(self) -> None:
        try:
            await self.reload()
        finally:
            self._reloading = False

    async def _spawn(self, coro) -> None:
        if not self.background:  # тесты: по порядку, сразу
            await coro
            return
        task = asyncio.create_task(labeled("sniper.wallets", self._guard(coro)))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    @staticmethod
    async def _guard(coro) -> None:
        try:
            await coro
        except Exception:  # noqa: BLE001 — сбой одного алерта не должен теряться молча
            logger.exception("sniper wallets: обработка покупки упала")

    async def process(self, buy: WalletBuy) -> None:
        wallet = self.tracked.get(buy.wallet)
        if wallet is None or wallet.get("buy_alerts") is False:
            return
        min_near = float(wallet.get("min_near") or 0)
        if min_near and buy.near_spent and buy.near_spent < int(min_near * 10 ** 24):
            return  # мелочь; покупки за другие активы (USDC, RHEA…) порог не режет
        meta = await self.metadata([t for t, _ in buy.bought + buy.spent])
        if not await self.store.save_buy(buy, {t: m[0] for t, m in meta.items()}):
            return
        near_usd = await self.store.near_usd() if buy.near_spent else None
        await self.sink.wallet_alert(BuyView(buy, wallet, meta, near_usd))

    async def process_registration(self, r: WalletRegistration) -> None:
        wallet = self.tracked.get(r.wallet)
        if wallet is None or wallet.get("regs") is False:
            return
        meta = (await self.metadata([r.token])).get(r.token)
        if meta is None and self.tokens is not None:
            try:  # новый токен ещё не в ft_metadata: один view — регистрации редки
                tok = await self.tokens.get(r.token)
                meta = (tok.symbol, tok.decimals)
            except Exception:  # noqa: BLE001 — не FT (NFT, соцсеть, маркет) или сеть
                meta = None
        if meta is None and not launchpad_of(r.token):
            return  # storage_deposit не в токене — не сигнал покупки
        symbol = meta[0] if meta else None
        if not await self.store.save_registration(r, symbol):
            return
        await self.sink.wallet_registration_alert(RegView(r, wallet, symbol))

    async def metadata(self, tokens: list[str]) -> dict[str, tuple[str, int]]:
        out = {t: KNOWN[t] for t in tokens if t in KNOWN}
        rest = [t for t in tokens if t not in out]
        for t in list(rest):
            tok = self.tokens.cached(t) if self.tokens is not None else None
            if tok is not None:
                out[t] = (tok.symbol, tok.decimals)
                rest.remove(t)
        if rest:
            out.update(await self.store.meta(rest))
        return out
