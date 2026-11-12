"""Лента запусков: optimistic-блоки neardata → события лаунчпадов (docs/SNIPER_VENUES.md, хронологии запусков).

Стадии: started — первый сигнал (токен и создатель известны, торговать ещё нельзя); live — в пул запуска в Rhea DCL
добавлена ликвидность (событие dcl.ref liquidity_added по пулу из started), торговать уже можно — раньше ready на
3–6 блоков, а у Nearly и раньше dev buy создателя; funding (только Nearly, блоком раньше live) — lock-аккаунт
лаунчпада переводит токены в пул (сигнал выстрела автопокупки); ready — запуск завершён,
пул торгуется (Nearly — событие launch, JustHoot — первое dcl_positions_recorded, Intear — pool_updated с
LaunchV2 и dev buy в той же квитанции). Покупка по списку наблюдения — только на ready: специально опережать
dev buy создателя бот не пытается.

Блоки — из общего потока процесса (indexer/block_stream.py, один на процесс, общий с индексатором): лента на него
подписана, пока снайпер держит lease. Сама она в neardata не ходит.
"""
from __future__ import annotations

import base64
import json
import logging
import time
from dataclasses import dataclass, field

from utils import rpc_meter

from .links import from_intear, from_nearly

logger = logging.getLogger("sniper")

NEARLY = "nearlytrade.near"
JUSTHOOT = "launchpad.justhoot.near"
INTEAR_LAUNCHER = "launch-v2.intear.near"
INTEAR_ENGINE = "dex.intear.near"
DCL = "dclv2.ref-labs.near"
LAUNCH_FEE = 10000  # пулы Nearly и JustHoot в DCL — всегда 1%
TX_MEMORY_S = 180.0


@dataclass
class LaunchEvent:
    launchpad: str  # nearly | justhoot | intear
    stage: str  # started | ready
    token: str | None
    creator: str | None
    symbol: str | None = None
    name: str | None = None
    pool: str | None = None
    block: int = 0
    tx_hash: str | None = None
    extra: dict = field(default_factory=dict)
    block_ts: float = 0.0  # время блока (unix, с) — для замеров задержки покупки
    seen: float = 0.0  # когда лента разобрала блок (unix, с)

    @property
    def key(self) -> str:
        return f"{self.launchpad}:{self.token or self.tx_hash}"


def _events(logs: list[str]) -> list[dict]:
    out = []
    for line in logs or []:
        if line.startswith("EVENT_JSON:"):
            try:
                out.append(json.loads(line[len("EVENT_JSON:"):]))
            except ValueError:
                pass
    return out


def _items(data) -> list[dict]:
    return [d for d in (data if isinstance(data, list) else [data]) if isinstance(d, dict)]


def _calls(receipt: dict) -> list[tuple[str, dict]]:
    """FunctionCall квитанции: [(method, args)]."""
    action = ((receipt.get("receipt") or {}).get("Action")) or {}
    out = []
    for a in action.get("actions") or []:
        fc = a.get("FunctionCall") if isinstance(a, dict) else None
        if not fc:
            continue
        try:
            args = json.loads(base64.b64decode(fc.get("args") or ""))
        except (ValueError, TypeError):
            args = {}
        out.append((fc.get("method_name"), args if isinstance(args, dict) else {}))
    return out


class BlockParser:
    """Состояние между блоками: создатель и символ запуска Intear известны из транзакции launch_token,
    а пул — из квитанции движка той же транзакции на несколько блоков позже."""

    def __init__(self) -> None:
        self._intear_tx: dict[str, tuple[float, str, dict]] = {}  # tx_hash → (когда, создатель, args)
        self._started: dict[str, tuple[float, LaunchEvent]] = {}  # токен Nearly/JustHoot → его started
        self._ready: dict[str, float] = {}  # ready уже выдан (JustHoot шлёт dcl_positions_recorded дважды)
        self._pools: dict[str, tuple[float, LaunchEvent]] = {}  # пул DCL запуска → started (ждём ликвидность)
        self._funded: dict[str, float] = {}  # funding уже выдан (токен → когда)

    def parse(self, block: dict) -> list[LaunchEvent]:
        header = (block.get("block") or {}).get("header") or {}
        height = int(header.get("height") or 0)
        out: list[LaunchEvent] = []
        now = time.time()
        for shard in block.get("shards") or []:
            for reo in shard.get("receipt_execution_outcomes") or []:
                receipt = reo.get("receipt") or {}
                outcome = (reo.get("execution_outcome") or {}).get("outcome") or {}
                if "SuccessValue" not in (outcome.get("status") or {}) and "SuccessReceiptId" not in (
                        outcome.get("status") or {}):
                    continue
                ex = outcome.get("executor_id") or receipt.get("receiver_id")
                tx_hash = reo.get("tx_hash")
                try:
                    out += self._receipt(ex, receipt, outcome, tx_hash, height, now)
                except (KeyError, TypeError, ValueError) as e:
                    logger.debug("sniper feed: квитанция %s не разобрана: %s", tx_hash, e)
        for k in [k for k, v in self._intear_tx.items() if now - v[0] > TX_MEMORY_S]:
            self._intear_tx.pop(k, None)
        for k in [k for k, v in self._started.items() if now - v[0] > TX_MEMORY_S]:
            self._started.pop(k, None)
        for k in [k for k, v in self._ready.items() if now - v > TX_MEMORY_S]:
            self._ready.pop(k, None)
        for k in [k for k, v in self._funded.items() if now - v > TX_MEMORY_S]:
            self._funded.pop(k, None)
        for k in [k for k, v in self._pools.items() if now - v[0] > TX_MEMORY_S]:
            self._pools.pop(k, None)
        block_ts = int(header.get("timestamp") or 0) / 1e9
        for e in out:
            e.block_ts, e.seen = block_ts, now
        return out

    def _receipt(self, ex: str, receipt: dict, outcome: dict, tx_hash: str | None, height: int,
                 now: float) -> list[LaunchEvent]:
        out = []
        evs = _events(outcome.get("logs") or [])
        if ex == NEARLY:
            args = next((a.get("args") or {} for m, a in _calls(receipt) if m == "launch"), {})
            for ev in evs:
                if ev.get("standard") != "nearpad" or ev.get("event") not in ("launch_started", "launch"):
                    continue
                for d in _items(ev.get("data")):
                    started = ev["event"] == "launch_started"
                    extra = {"dev_buy": d.get("dev_buy") or d.get("dev_buy_near"), "quote": d.get("quote")}
                    if started:
                        extra["links"] = from_nearly(args)
                    else:  # launch — в квитанции без аргументов: ссылки из started
                        st = (self._started.pop(d.get("token"), None) or (0, None))[1]
                        if st:
                            extra["links"] = st.extra.get("links")
                    e = LaunchEvent("nearly", "started" if started else "ready", d.get("token"), d.get("creator"),
                                    d.get("symbol"), d.get("name"), d.get("pool_id"), height, tx_hash, extra)
                    if started and e.token:
                        self._started[e.token] = (now, e)
                        if e.pool:
                            self._pools[e.pool] = (now, e)
                    out.append(e)
        elif ex == JUSTHOOT:
            args = next((a.get("args") or {} for m, a in _calls(receipt) if m == "launch_meme"), {})
            for ev in evs:
                if ev.get("standard") == "hoot_launcher" and ev.get("event") == "launch_requested":
                    d = _items(ev.get("data"))[0]
                    started = LaunchEvent("justhoot", "started", d.get("token_id"), d.get("creator_id"),
                                          args.get("symbol"), args.get("name"), None, height, tx_hash,
                                          {"dev_buy": d.get("dev_buy_quote_max"), "quote": d.get("quote_token_id"),
                                           "reference": args.get("reference")})
                    self._started[started.token] = (now, started)
                    quote = d.get("quote_token_id")
                    if started.token and quote:
                        self._pools[_pool_key(started.token, quote, LAUNCH_FEE)] = (now, started)
                    out.append(started)
        elif ex and ex.endswith("." + JUSTHOOT):
            for ev in evs:
                if ev.get("standard") == "hoot_meme" and ev.get("event") == "dcl_positions_recorded":
                    if ex in self._ready:
                        continue
                    self._ready[ex] = now
                    lpts = _items(ev.get("data"))[0].get("lpt_ids") or []
                    pool = lpts[0].split("#")[0] if lpts else None
                    st = (self._started.pop(ex, None) or (0, None))[1]
                    out.append(LaunchEvent("justhoot", "ready", ex, st and st.creator, st and st.symbol,
                                           st and st.name, pool, height, tx_hash, dict(st.extra) if st else {}))
        elif ex in self._started and (receipt.get("predecessor_id") or "").endswith("." + NEARLY):
            out += self._funding(ex, receipt, height, tx_hash)
        elif ex == DCL and self._pools:
            out += self._live(evs, height, tx_hash, now)
        elif ex == INTEAR_LAUNCHER:
            for method, args in _calls(receipt):
                if method == "launch_token" and tx_hash:
                    creator = receipt.get("predecessor_id")
                    self._intear_tx[tx_hash] = (now, creator, args)
                    out.append(LaunchEvent("intear", "started", None, creator, args.get("symbol"), args.get("name"),
                                           None, height, tx_hash, {"links": from_intear(args)}))
        elif ex == INTEAR_ENGINE:
            out += self._intear_pool(evs, tx_hash, height)
        return out

    def _intear_pool(self, evs: list[dict], tx_hash: str | None, height: int) -> list[LaunchEvent]:
        """pool_updated с LaunchV2 внутри транзакции launch_token (пул создан и dev buy исполнен)."""
        known = self._intear_tx.get(tx_hash or "")
        if not known:
            return []
        for ev in evs:
            inner = (ev.get("data") or {}).get("event") if ev.get("event") == "dex_event" else None
            if not isinstance(inner, dict) or inner.get("event") != "pool_updated":
                continue
            data = inner.get("data") or {}
            pool = (data.get("pool") or {}).get("LaunchV2")
            if not pool:
                continue
            launched = ((pool.get("launched_asset") or {}).get("asset_id") or "").removeprefix("nep141:")
            _, creator, args = known
            self._intear_tx.pop(tx_hash, None)
            return [LaunchEvent("intear", "ready", launched or None, creator, args.get("symbol"), args.get("name"),
                                str(data.get("pool_id")), height, tx_hash,
                                {"quote": (pool.get("quote_asset") or {}).get("asset_id"), "links": from_intear(args)})]
        return []


    def _funding(self, token: str, receipt: dict, height: int, tx_hash: str | None) -> list[LaunchEvent]:
        """Nearly: lock-аккаунт лаунчпада (lock_N.nearlytrade.near) переводит токены в пул запуска (ft_transfer_call
        в DCL) — ликвидность будет в следующем блоке. Раньше это было ровно на started+4, теперь бывает и на +10:
        по этому событию автопокупка стреляет, а не по номеру блока."""
        st = self._started[token][1]
        if st.launchpad != "nearly" or token in self._funded:
            return []
        if not any(m == "ft_transfer_call" and a.get("receiver_id") == DCL for m, a in _calls(receipt)):
            return []
        self._funded[token] = time.time()
        return [LaunchEvent(st.launchpad, "funding", token, st.creator, st.symbol, st.name, st.pool, height, tx_hash,
                            dict(st.extra))]

    def _live(self, evs: list[dict], height: int, tx_hash: str | None, now: float) -> list[LaunchEvent]:
        """liquidity_added в DCL по пулу ждущего запуска → стадия live (один раз на пул)."""
        out = []
        for ev in evs:
            if ev.get("standard") != "dcl.ref" or not str(ev.get("event") or "").startswith("liquidity_add"):
                continue
            for d in _items(ev.get("data")):
                pool = d.get("pool_id") or str(d.get("lpt_id") or "").split("#")[0]
                hit = self._pools.pop(pool, None)
                if not hit:
                    continue
                st = hit[1]
                out.append(LaunchEvent(st.launchpad, "live", st.token, st.creator, st.symbol, st.name, pool, height,
                                       tx_hash, dict(st.extra)))
        return out


def _pool_key(a: str, b: str, fee: int) -> str:
    x, y = sorted((a, b))
    return f"{x}|{y}|{fee}"


class LaunchFeed:
    """Подписчик потока блоков (indexer/block_stream.py): разбирает блок и вызывает on_event(LaunchEvent)
    последовательно, без параллели. Сам блоки не скачивает — поток один на процесс, общий с индексатором.
    on_block(height, ts) — синхронный, на каждый блок до событий: по нему автопокупка стреляет на нужной высоте."""

    def __init__(self, on_event, stream=None, on_block=None) -> None:
        self.on_event = on_event
        self.stream = stream
        self.on_block = on_block
        self.parser = BlockParser()

    @property
    def last_ok(self) -> float:
        """Когда поток последний раз успешно получил блок (для «лента не обновляется» в боте)."""
        return self.stream.last_ok if self.stream is not None else 0.0

    async def handle(self, block: dict) -> None:
        token = rpc_meter.SOURCE.set("sniper.feed")  # запросы обработчиков — на счёт снайпера, не потока
        try:
            if self.on_block is not None:
                try:
                    header = (block.get("block") or {}).get("header") or {}
                    self.on_block(int(header.get("height") or 0), int(header.get("timestamp") or 0) / 1e9)
                except Exception:  # noqa: BLE001
                    logger.exception("sniper feed: on_block упал")
            for ev in self.parser.parse(block):
                try:
                    await self.on_event(ev)
                except Exception:  # noqa: BLE001 — обработчик не должен останавливать ленту
                    logger.exception("sniper feed: обработчик события %s упал", ev.key)
        finally:
            rpc_meter.SOURCE.reset(token)
