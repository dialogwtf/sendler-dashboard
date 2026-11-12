# Sendler Dash — data contract

The agreement between the indexer and the dashboard API (dash.sendler.xyz).

- **Written by** `DashWorker` (`indexer/services/dashboard/worker.py`): a subscriber of the block stream started in
  `main.py`, no RPC. Smart money, creator statistics, USD rates and token metadata are written by the modules next
  to it (`smart.py`, `launch_stats.py`, `reference.py`).
- **Read by** the API (`api/` of this repository), `/v2/dash` (`api/routes/dash.py`, `services/dash_service.py`; the
  endpoint list is [api/docs/API_ROUTES.md](../../api/docs/API_ROUTES.md)). The API writes only its own sign-in and
  layout collections (section "Sign-in and personal data") and calls RPC once per wallet sign-in (key check).

All collections live in the indexer's database (`near_indexer`). Time is UTC everywhere. Change a field here and in
its reader in `api/` together.

## Prices

`px` is NEAR per **raw** unit of the token (float), without decimals. The price per token is `px × 10^decimals`,
decimals come from `ft_metadata`. The market cap of Nearly and JustHoot launches (supply 1e9) is
`px × 1e9 × 10^decimals` NEAR. The NEAR rate is `ft_prices` `_id: wrap.near` `price_usd`.

## Collections

### `dash_trades` — trades (TTL `DASH_TRADES_TTL_DAYS`, 7 days by default)

A swap of a token against NEAR/wNEAR on Rhea DCL, Rhea v1 or Intear, from `DASH_TRADE_MIN_NEAR` (0.05). A pair not
against NEAR: a swap of the token against a pair asset — a stablecoin or a launch pair asset (PRL, QTC, TEAR…) — is a
trade too; its NEAR amount is the asset amount at the asset's price in NEAR from the stream (not older than 6 h; a
stablecoin — at the NEAR rate); no price — no trade. A swap of two pair assets (PRL ↔ USDC) is not a trade.
Round-trip arbitrage within one receipt (in and out through the same token) is not written, but goes into candles.

| field | |
|---|---|
| `_id` | `<receipt_id>:<index of the swap>` |
| `ts`, `block`, `tx`, `receipt` | block time, height, transaction hash, receipt |
| `venue`, `pool` | `Rhea DCL` / `Rhea` / `Intear DEX`; pool: DCL `x\|y\|fee`, Rhea v1 `v2.ref-finance.near#<id>`, Intear `<dex>#<n>` (may be `null`) |
| `token`, `side` | the token; `buy` — the token was bought for NEAR, `sell` — the reverse |
| `near`, `usd`, `tok`, `px` | NEAR in the trade, $ at write time (`null` without a rate), token amount (string, raw units), price |
| `wallet`, `signer` | the swap's actor (a wallet or an aggregator) and the transaction's signer |
| `via` | the pair asset `near` and `px` were computed through (`prl.omft.near`, a stablecoin…); `null` / absent — a swap against NEAR |
| `backfill` | `true` — from history (see "History"): `_id` `bf:<tx>:<wallet>`, `receipt` and `pool` are `null` |

Indexes: `(token, ts↓)`, `(wallet, ts↓)`, `(ts↓)`.

### `dash_candles` — candles

`_id` `<token>:<i>:<start unix>`, `token`, `i` (`1m` kept 7 days, `1h` — a year, `1d` — forever), `t` (start),
`o h l c` (px), `v` (NEAR volume), `v_usd`, `buys`, `sells`, `traders` (unique wallets — the signer, else the actor;
at most 20,000), `w` (the wallets themselves — the API returns only `traders`), `updated_at`. Written as increments
(an update pipeline: addition, max/min, union of `w`), so an open candle keeps growing. The candle's price (o/h/l/c),
a launch's `px` / `px_max` and the token rate for moves are set only by trades of the token's **main pool** — the one
with the most NEAR traded over the last hour (`trades.MainPools`, decaying with a one-hour half-life); trades of other
pools add volume, counters and traders but do not move the price. Candles before 2026-11-09 have no `w` and
`traders`. Indexes: `(token, i, t↓)`, `(i, t↓)`.

### `dash_launches` — Nearly / JustHoot / Intear launches (kept forever)

| field | |
|---|---|
| `_id`, `token`, `pad` | token, launchpad (`nearly`, `justhoot`, `intear`) |
| `stage_n`, `t_<stage>` | the highest stage: 1 started, 2 created, 3 funding, 4 live, 5 ready; the time of each |
| `t_first` | when the launch was first seen |
| `creator`, `symbol`, `name`, `pool`, `block`, `tx` | |
| `extra` | `dev_buy`, `tax`, `shares`, `fee_pips`, `links` (`{kind: URL}`), `description`, `fallback`, `launcher` — whatever the launchpad provides |
| `fee_farm` | non-empty — a launch built on taxing bots; the database holds the reason as text (for logs), the API returns a `true` / `false` flag |
| `px0`, `first_trade` | price of the first trade |
| `px`, `px_max`, `last_trade`, `trades`, `volume` | last and highest price, trade count, NEAR volume |
| `backfill` | `true` — a launch from history: `extra`, `fee_farm`, `block`, `tx` are unknown, stage 5 (it traded) or 1 |

Indexes: `(t_first↓)`, `(creator, t_first↓)`, `(last_trade↓)`.

### `dash_moves` — big moves from $1k (TTL 30 days)

- `kind: transfer` — a NEAR or NEP-141 transfer: `token` (`near` for NEAR), `amount` (string, raw units),
  `from`, `to`, `signer`, `tx`.
- `kind: swap` — a swap: `token`, `side`, `amount`, `near`, `from` (wallet), `to` (pool), `signer`, `tx`, `venue`.

Both kinds have `_id`, `ts` and `usd`. Swap legs on venues and wrap / unwrap are not written.
Indexes: `(ts↓)`, `(token, ts↓)`, `(usd↓, ts↓)`.

### `dash_network` — network

`_id` `<i>:<start>`, `i` (`1m` kept 7 days, `1h`, `1d`), `t`, `blocks`, `txs`, `receipts`, `tgas`, `burnt_near`,
`new_accounts`, `signers`. `1m` has the exact signer count, `1h` / `1d` — a HyperLogLog estimate whose registers
are in the `hll` field (bytes: the API does not return it). Index `(i, t↓)`.

### `dash_new` — new items (TTL 90 days)

`_id` `<kind>:<id>`, `kind`, `id`, `by`, `tx`, `first_seen`:

- `kind: contract` — a new account with code. Later it gets `token_kind`: `ft` or `nft`, by its first nep141 /
  nep171 event.
- `kind: pool` — a new DCL, Rhea v1 or Intear pool, also with `venue` and `tokens`; for Intear — `pool_kind`
  (`private` / `public` / `launch` / `launch_v2`), `id` `slimedragon.near/xyk#<n>`. A new Intear pool is one whose
  number is above all known ones (`dash_state` `intear`).

Indexes: `(kind, first_seen↓)`, `(token_kind, first_seen↓)`, `(first_seen↓)`.

### `dash_labels` — account labels (kept forever)

`_id` — the account, `label`, `kind`, `source` (`manual` / `auto`). **`label` is a proper name only, with no
language** (Rhea DCL, Omni Bridge, Binance) **or `null`**; what the account is, says `kind` (`dex`, `aggregator`,
`bridge`, `launchpad`, `token`, `contract`, `hub`, `exchange`, `team`, `wallet`), and the site translates it itself.
Venues, bridges and launchpads are seeded on worker start (`source: manual`, brought to the current list); new
contracts get `kind: contract`, hubs (more than 50 NEAR counterparties a day — exchanges, bridges, services) —
`kind: hub` without a name (hourly); both are `source: auto` with `$setOnInsert` and never touch a manual label.
Exchange names are set by hand: `scripts/dash_labels.py set <account> "<name>" exchange`.

### `dash_holders` / `dash_holder_stats` — launch holders (kept forever)

Balances of launch tokens (every `dash_launches` token) from the stream's NEP-141 events: `ft_mint`, `ft_transfer`
(the Nearly tax is a separate entry with memo `tax`, to the token contract itself), `ft_burn`; successful receipts
only (`services/dashboard/holders.py`). Written as `$inc` increments once per flush (~2 s).

- `dash_holders`: `_id` `<token>:<account>`, `token`, `account`, `bal` (**Decimal128**, raw units; may be `0`, and
  negative for a token not reconciled yet), `first_at`, `updated_at`. A holder is `bal > 0`. Indexes
  `(token, bal↓)`, `(account, token)`.
- `dash_holder_stats`: `_id` token, `holders` (how many `bal > 0`, once a minute for tokens with transfers), `series`
  `[{t: hour, n}]` (30 days), `complete` — the mint was seen by the live stream, balances are exact from the token's
  birth; `seeded` (time), `src: "fastnear"`, `top_only` — the token was reconciled with a FastNear snapshot
  (`scripts/backfill_dash_holders.py`); `top_only: true` — the snapshot had 100 entries (FastNear's limit): the top
  is exact, `holders` is a lower bound.

A token with neither `complete` nor `seeded` is not shown by the API (`available: false`, `reason: pending`): its
balances are only increments since collection started. Shares (top-10 wallets, dev, linked, contracts) are computed
by the API.

### `dash_events` — feed for the WebSocket (capped, 64 MB / 200k)

`{ch, ts, d}`, ordered by `_id` (natural). The API holds one tailable cursor per process. Channels:

| `ch` | `d` |
|---|---|
| `launch` | `token`, `pad`, `stage`, `creator`, `symbol`, `at` |
| `trade` | `token`, `side`, `near`, `usd`, `px`, `wallet`, `venue`, `key` |
| `move` | a `dash_moves` document, its `_id` in the `id` field (deduplication on the client) |
| `new` | `kind`, `id`, `by` (for `ft` / `nft` — only `kind`, `id`) |
| `network` | `height`, `blocks`, `txs`, `receipts` — once per flush (~2 s) |

### `dash_state`

`_id: cursor` — `height` and `at` of the last written block: `/v2/dash/health` computes the lag from them.
`_id: lease` — a single writer across all replicas.
`_id: intear` — `max`, the highest known Intear pool number.
`_id: backfill` — the history boundary `until` (the first live trade) and candles already topped up, `overlap_done`.

## Reference data and analytics written next to the worker

| collection | written by | |
|---|---|---|
| `ft_prices` | `reference.FtPrices` | `_id` token, `price_usd`, `symbol`, `decimals` — the REF Finance price list, every minute |
| `ft_price_snapshots` | `reference.FtPrices` | `_id` `<token>:<hour>`, `contract_id`, `ts`, `price_usd` — hourly NEAR/USD (`wrap.near`), read by `thesis/build.py` |
| `ft_metadata` | `reference.TokenMeta` | `_id` token, `symbol`, `name`, `decimals`, `icon` — the contract's NEP-148 `ft_metadata`, once per token |
| `sniper_smart_flows` | `smart.SmartMoney` | `_id` `<wallet>:<token>` — `near_in`, `near_out`, `tok_in`, `tok_out` (raw, float), `buys`, `sells`, `first_at`, `last_at`; NEAR ↔ token swaps only; TTL 45 days |
| `sniper_smart_prices` | `smart.SmartMoney` | `_id` token, `price` — yocto NEAR per raw unit (last swap) |
| `sniper_smart_wallets` | `smart.SmartMoney` | the top 200 smart wallets, re-scored hourly: `_id` wallet, `rank`, `pnl`, `invested`, `roi`, `win_rate`, `tokens`, `wins` |
| `sniper_launch_stats` | `launch_stats.LaunchStats` | per launch: `_id` token, `creator`, `launchpad`, `t0`, `dev_in` / `dev_sold` / `dev_out` (u128 strings), `dumped`, `first_sell_s`, `dev_sold_near`; TTL 90 days |

The `sniper_` prefix is historical: in production the same collections also feed the Sendler trade bot.

## Bot links

The site links into the Sendler trade bot with `t.me/<bot>?start=<parameter>`: `t_<code>` — the token screen ("Buy
in the bot"), `wt_<code>` — add the token to the price watchlist, `ww_<code>` — add a wallet to the watchlist. The
code is the first 10 characters of base32 of the address's SHA-256, lowercase (`services/dashboard/botlinks.py`
`token_code`, `bot_code` in the API). The bot finds the address by code in `sniper_token_links` (`_id` code →
`token`): DashWorker writes these records in advance — for tokens from trades and launches, launch creators and
smart wallets (hourly), and `scripts/backfill_dash.py` — for history.

A source mark `__<source>` at the end of the parameter (`t_<code>__dash`) tells the bot where the user came from.
The site adds a referral code (`t_<code>-<ref>__dash`) only for a user who signed in through Telegram and has a bot
profile (`/v2/dash/me` → `telegram.ref_code`).

## Sign-in and personal data (written by the API)

These collections are written by the API (`api/services/dash_auth.py`); the indexer does not read them:

| collection | | lifetime |
|---|---|---|
| `dash_sessions` | `_id` SHA-256 of the session token, `account` (wallet, NEP-413 sign-in), `tg` `{id, username, name, ref_code, bot_user}` | TTL `expires_at`, 30 days |
| `dash_auth_nonces` | wallet sign-in nonce (one-time) | TTL 10 min |
| `dash_logins` | Telegram sign-in: the API creates `{_id: nonce, status: pending, secret (hash), session, expires_at}`, **the bot** (the Sendler trade bot, outside this repository, link `start=dl_<nonce>`) sets `status: confirmed`, `tg_id`, `tg_username`, `tg_name`, `ref_code`, `bot_user`, `confirmed_at`; the API issues the session and sets `used` | TTL 10 min |
| `dash_layouts` | `_id` owner `w:<wallet>` / `tg:<id>`: `layout`, `watchlist`, `tg`; `s:<code>` — a layout shared by the link `?layout=<code>` | — |

## History

`scripts/backfill_dash.py` (logic — `services/dashboard/history.py`) fills the time before the live stream from an
`ft_transfers` collection (NEP-141 transfers indexed by the Sendler marketplace indexer): `1h` / `1d` candles for
`--days` (90) and `1m` for 7 days, with volume in $ at the hourly NEAR rate (NEAR ↔ USDC/USDT swaps of the same
history) and traders, trades for 7 days (`backfill: true`), launches with the price from trades. A re-run is safe.
The history does not see Intear swaps from the internal balance or round-trip arbitrage; network metrics, big moves
and new items cannot be restored for the past.

Launch holders from before collection started — `scripts/backfill_dash_holders.py`: reconciliation with the FastNear
snapshot `/v1/ft/<token>/top` (correction = snapshot − recorded, a re-run is safe; `--dry-run`, `--token`, `--force`).

## What the API reads from the trade bot's collections

Optional: without them the corresponding fields are simply empty.

| collection | what for |
|---|---|
| `sniper_devs_blocked` | how many bot users have the creator on their blocklist: field `account`, without `allowed: true`; DashWorker creates the `account` index |
| `sniper_dev_links` | linked wallets: `_id` — account, `links` (holders: wallets linked to the creator — `links.root`, DashWorker creates the index) |

## Constants that must match

- `services/dashboard/events.py` `EVENTS` = `api/config/dash.py` `EVENTS_COLLECTION`.
- `services/dashboard/launches.py` `STAGES` = `api/config/dash.py` `STAGE_NAMES`.
- `services/dashboard/botlinks.py` `token_code` = `api/services/dash_service.py` `bot_code` = `web/src/lib/botLinks.ts`.
- the bot's `dash_logins` collection and the `dl_` prefix = `api/config/dash.py` `LOGINS` and the link in
  `api/services/dash_auth.py`.
