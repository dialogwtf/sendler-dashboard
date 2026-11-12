# Sendler Dash API (`/v2/dash`)

The public dashboard dash.sendler.xyz. The data is written by the indexer (`DashWorker`; the contract is
[indexer/docs/DASH_DATA.md](../../indexer/docs/DASH_DATA.md)); the API only reads it and never calls RPC — except
for wallet sign-in (one key check per sign-in) — and writes only its own sign-in and layout collections. No
`X-API-Key` is needed (`DASH_REQUIRE_API_KEY=1` puts it back behind a key). Rate limit: 300 requests per minute per IP.

The interactive reference (OpenAPI) is served at `/docs`.

Response: `{"data": …}`. Everyone gets the same, current data: no sign-in, no delay. In-memory cache per endpoint,
`Cache-Control: public, max-age=<ttl>`.
Prices in the collections are NEAR per raw unit (`px`); `price_near` in responses is already per token.
Time is ISO UTC everywhere (`…Z`); `fee_farm` is a flag.
Errors are `{"detail": "<code>"}`: `bad_period`, `bad_period_or_sort`, `bad_interval`, `bad_before`.

| Method | Parameters | Cache | What |
|---|---|---|---|
| `GET /v2/dash/health` | — | — | height, lag `behind_s`, WebSocket clients, `near_usd` rate |
| `GET /v2/dash/launches` | `pad` (nearly,justhoot,intear), `hide_fee_farm`, `hide_blocked` (creators on the trade bot's shared auto-block), `before` (ISO), `limit` ≤ 200 | 2 s | launches: stage, creator + `creator_facts`, dev buy, tax, `x` / `x_max` from the start price, cap in NEAR / $; `backfill: true` — a launch from history (dev buy, tax, fee_farm unknown) |
| `GET /v2/dash/launches/movers` | `period` 10m·1h·24h, `limit` ≤ 50 | 10 s | growing launches (period volume from $500), `spark` — closes of the period's candles (NEAR per token) |
| `GET /v2/dash/tokens/top` | `period` 1h·24h·7d, `sort` volume·growth·trades | 30 s | top tokens by candles; `decimals`, `traders` — unique wallets over the period |
| `GET /v2/dash/tokens/meta` | `ids` (comma-separated, ≤ 100) | 300 s | ticker, name, `decimals`, `icon` (render only in `<img>`), `bot_code`; unknown — `null` |
| `GET /v2/dash/search` | `q` (from 2 characters), `limit` ≤ 20 | 30 s | `tokens` — by ticker (exact first, then by 24 h turnover) and address; `accounts` — if it looks like an account: label, launches, smart money rank |
| `GET /v2/dash/tokens/{token_id}` | — | 5 s | price, 24 h (volume, trades, `traders_24h` — unique wallets), launch, smart money over 24 h |
| `GET /v2/dash/tokens/{token_id}/candles` | `i` 1m·1h·1d, `from` / `to` (unix), `limit` ≤ 1000 | 5 s | OHLCV, `buys` / `sells`, `traders` (unique wallets of the candle) + `decimals` |
| `GET /v2/dash/tokens/{token_id}/trades` | `limit`, `before` (ISO) | 2 s | trades with `smart` / `creator` marks, `decimals` |
| `GET /v2/dash/creators/{account}` | — | 60 s | creator dossier: launches, dumps, blocklists, linked wallets |
| `GET /v2/dash/smart/top` | `limit` | 60 s | smart money, top over 30 days |
| `GET /v2/dash/smart/feed` | `limit`, `before` (ISO) | 5 s | smart money trades, `decimals` |
| `GET /v2/dash/moves` | `min_usd` (10000), `token_id`, `kind` transfer·swap, `before` (ISO) | 2 s | big transfers and swaps, `decimals` |
| `GET /v2/dash/network` | `period` 1h·24h·7d | 10 s | series of txs / receipts / tgas / burnt / new_accounts / signers, `today`, `tps`, `near_usd` |
| `GET /v2/dash/new` | `kind` contract·ft·nft·pool, `before` (ISO) | 10 s | new contracts, tokens, NFTs, pools |
| `GET /v2/dash/tokens/quotes` | `ids` (comma-separated, ≤ 100) | 10 s | watchlist rows: `price_near` / `price_usd`, `change_24h`, `volume_24h_near`, cap (`cap_near` / `cap_usd` — Nearly / JustHoot only), `last_trade`, `pad`, `bot_code`; no trades — `null` |
| `GET /v2/dash/tokens/{token_id}/holders` | `limit` ≤ 100 | 30 s | holders of a launch token: `available` (if not — `reason` `not_tracked` / `pending`), `holders` (count, hourly `series` [{t, n}]), `complete` / `top_only` (reconciled by the top 100 — the count is a lower bound), `supply` (string, raw), shares `top10_share` (the 10 largest wallets, without pools and contracts), `dev_share`, `linked_share` (wallets linked to the creator), `contracts_share`; `rows` [{account, bal (string), amount, share, label, tags pool·contract·exchange·hub·creator·linked·smart}] |
| `GET /v2/dash/nft/collections` | `sort` volume_24h·volume_7d·volume_30d·sales_24h·floor·last_trade, `limit` | 60 s | NFT market: collections of sendler-market (name, image, floor, volumes, sales) |
| `GET /v2/dash/nft/sales` | `limit`, `cursor` (`next_cursor` of the previous page) | 10 s | latest sales `{items, next_cursor}` |
| `GET /v2/dash/nft/stats` | `days` ≤ 90 | 300 s | market totals: volume 24 h / 7 d, sales 24 h, listings, active collections, `daily` [{date, volume_usd, sales}] |
| `GET /v2/dash/labels` | `accounts` (comma-separated, ≤ 100) | 300 s | account labels: `label` — a proper name with no language, or `null`; `kind` dex·aggregator·bridge·launchpad·token·contract·hub·exchange·team·wallet·implicit — translated by the site |

## Link previews for `/token/<id>`

`GET /v2/dash/page/token/{token_id}` — the site's built `index.html` (`DASH_SITE_INDEX`, re-read when the file
changes) with the token's title and `og:` / `twitter:` tags; the site's web server proxies `/token/<id>` here and
serves the static `index.html` on any error. `GET /v2/dash/og/token/{token_id}.png` — a 1200×630 image (logo,
ticker, price in NEAR / $, 24 h change, cap, volume, holders, sparkline), cached for 10 minutes; the link carries
`?v=<hour>` so that messengers fetch a fresh one. An invalid address — 404.

## Sign-in and personal data

Not cached (`Cache-Control: private, no-store`). The session is `Authorization: Bearer <token>` from the sign-in
response, 30 days (only its SHA-256 is stored). Errors: `bad_request`, `bad_nonce`, `bad_signature`,
`key_not_owned`, `rpc_unavailable`, `unauthorized`, `not_found`, `bad_layout`, `layout_too_big`, `bad_watchlist`.
Sign-in endpoints: 20 requests per minute per IP.

| Method | Body / parameters | What |
|---|---|---|
| `POST /v2/dash/auth/wallet/challenge` | — | `{nonce, message, recipient, expires_at}` — a one-time nonce (10 min): the wallet signs `message` = `Sign in to Sendler Dash`, `recipient` = `dash.sendler.xyz` (NEP-413 `signMessage`) |
| `POST /v2/dash/auth/wallet` | `{account_id, public_key, signature, nonce, message?, recipient?, callback_url?}` | checks the signature and the FullAccess key (RPC) → `{token, expires_at, me}`. With the Bearer of a session signed in through Telegram — Telegram is attached to the wallet |
| `POST /v2/dash/auth/telegram` | (Bearer optional) | `{nonce, secret, url, expires_at}`: open `url` (`t.me/<bot>?start=dl_<nonce>`), the bot confirms the sign-in. With a Bearer — Telegram is attached to that session |
| `GET /v2/dash/auth/telegram/{nonce}` | `secret` | `{status: pending·confirmed·expired·used}`; `confirmed` returns `{token, expires_at, me}` once (`token: null` — attached to the current session) |
| `POST /v2/dash/auth/logout` | Bearer | ends the session |
| `GET /v2/dash/me` | Bearer | `{account, telegram: {username, name, bot_user, ref_code} \| null, expires_at}` |
| `DELETE /v2/dash/me/telegram` | Bearer | detach Telegram |
| `GET /v2/dash/me/layout` | Bearer | `{layout, watchlist, updated_at}` |
| `PUT /v2/dash/me/layout` | `{layout}` (the site's JSON, ≤ 64 KB) | save the layout |
| `PUT /v2/dash/me/watchlist` | `{tokens: [...]}` (≤ 100) | save the watchlist; prices come from `/tokens/quotes` |
| `POST /v2/dash/layouts` | `{layout}` (no sign-in needed, 30 per hour per IP) | `{code, url}` — the link `?layout=<code>` |
| `GET /v2/dash/layouts/{code}` | — | `{code, layout, created_at}` |

## WebSocket `/v2/dash/ws`

Open to everyone, no token. More than 3 connections from one IP — closed with code 4429; more than 2000 per process
— 4503. Client: `{"op": "sub"|"unsub"|"ping", "ch": ["launch", "trade", "trade:<token>", "move", "new", "network"]}`
(up to 20 subscriptions) → `{"ok": true, "subs": [...]}` or `{"error": …}` (for `unknown_channel` the wrong channels
are in `bad`). Events: `{"ch", "ts", "d"}` — from the indexer's `dash_events`, one tailable cursor per process; a
slow client loses events (queue of 500).

## Bot links

`bot_code` (in `/tokens/meta`, `/tokens/{id}`, `/creators/{account}`, `/search`) is the code for
`https://t.me/<bot>?start=t_<code>` (token screen), `wt_<code>` (watch the token's price), `ww_<code>` (watch the
wallet); a source mark `__dash` may be appended. The code is the first 10 characters of base32 of the address's
SHA-256, lowercase (as `token_code` in `indexer/services/dashboard/botlinks.py`). The code → address map is written
in advance by the dashboard worker.

## Errors

**403 Forbidden** (only when a key is required):
```json
{"detail": "Invalid or missing API key"}
```

**429 Too Many Requests:**
```json
{"detail": "Rate limit exceeded: 300 per 1 minute"}
```

**500 Internal Server Error:**
```json
{"detail": "Internal server error"}
```
