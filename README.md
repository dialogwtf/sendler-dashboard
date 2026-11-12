# Sendler Dash

A live public dashboard of the NEAR meme market and of the network itself: token launches as they happen, trades and
candles, holders, smart money, big transfers, new contracts and pools, network load.

**Live:** https://dash.sendler.xyz · **API:** https://api.sendler.xyz/v2/dash/health · built at NEAR HACKATHON
(November 2026), track **Investment Thesis** (onchain data, dashboards).

**The thesis:** [THESIS.md](THESIS.md) — what three months of this data say about NEAR's on-chain trading economy,
with the evidence for, the evidence against, and the numbers that would change the conclusion. Rebuild every chart
and number with `python thesis/build.py`.

Everything on the screen is derived from NEAR mainnet blocks. The indexer reads one stream of optimistic blocks and
makes **no RPC calls on the block path**; a launch appears on the site within about a second of its transaction.

## The problem

New NEAR tokens are launched every few minutes on three launchpads, and most of what a trader needs to judge one is
on chain but nowhere on a single screen: who the creator is and what he did with his previous launches, whether the
token is built to tax bots, who holds it, whether wallets with a record of profit are buying it. Block explorers show
transactions, DEX screens show one pool. Sendler Dash turns the raw block stream into those answers, live, for
everyone, with no sign-in.

## What it shows

| | |
|---|---|
| **Launches** | New tokens of the Nearly, JustHoot and Intear launchpads, stage by stage (started → created → funding → live → ready), with the creator, dev buy, transfer tax and a "fee farm" flag for launches built to tax bots |
| **Creator dossier** | Per launch: how many of his own tokens the creator received, sold and handed out, and whether he dumped them in the first 5 minutes of trading |
| **Tokens** | Price, market cap, 1m / 1h / 1d candles, trades, unique traders; pairs not against NEAR are priced in NEAR through the pair asset |
| **Holders** | Balances of every launch token rebuilt from its NEP-141 events: holder count over time, top holders, concentration |
| **Smart money** | Wallets that keep making money on tokens over 30 days, found from swaps alone; their trades and their position in a token |
| **Big moves** | Transfers and swaps from $1,000 (NEAR and NEP-141), with account labels |
| **Network** | Blocks, transactions, receipts, gas, burnt NEAR, new accounts, unique signers (HyperLogLog) per minute / hour / day |
| **New** | New contracts, fungible tokens, NFT contracts and DEX pools |
| **Personal** | Sign-in with a NEAR wallet (NEP-413), saved layout and watchlist, shareable layouts, link previews per token |

## Try it

**1. The live site (no setup).** Open https://dash.sendler.xyz — see the launch feed, movers, smart money and network
widgets updating without a reload. Click any token — see its chart, trades and holders.

**2. The site from this repository against the production API (2 minutes, Node 20+).**

```bash
cd web
cp .env.example .env
npm install
npm run dev
```

Open http://localhost:5173 — see the same dashboard served from your machine. Open http://localhost:5173/dev/live —
see the WebSocket status, the current block height and raw events as they arrive.

**3. The whole stack locally (Python 3.11+, Node 20+, MongoDB 6+).**

```bash
# MongoDB, if you do not have one:  docker run -d -p 27017:27017 mongo:7

# indexer — writes to MongoDB database `near_indexer`
cd indexer
cp .env.example .env                 # optional: set NEAR_DATA_API_KEY (see the note below)
pip install -r requirements.txt
python main.py
```

See `Sendler Dash: worker started from height …` in the log. Open http://localhost:8080/metrics — see
`indexer_block_stream_height` growing.

```bash
# API — reads the same database (second terminal)
cd api
cp .env.example .env
pip install -r requirements.txt
uvicorn app.main:app --port 8000
```

Open http://localhost:8000/v2/dash/health — see the indexer's `height` and `behind_s` (lag in seconds), and
`near_usd`. Open http://localhost:8000/v2/dash/network?period=1h — see per-minute transactions, receipts, gas and
signers. Open http://localhost:8000/docs — see every endpoint.

```bash
# site against the local API (third terminal)
cd web
cp .env.example .env
#   in .env set:  VITE_DASH_API=http://localhost:8000/v2/dash
#                 VITE_DASH_WS=ws://localhost:8000/v2/dash/ws
npm install
npm run dev
```

Open http://localhost:5173 — see trades, big moves and network metrics filling in from your own indexer within a
minute; launches appear as they happen on mainnet (every few minutes), smart money after enough history is collected
(the ranking needs wallets with at least 5 traded tokens).

**Note on the block source.** Without `NEAR_DATA_API_KEY` the public neardata endpoint rate-limits the stream (HTTP
429) after a few seconds; the indexer keeps retrying and refetches gaps, so data still arrives, with pauses. For a
continuous stream use a FastNear key.

### Tests

```bash
cd indexer && python -m pytest        # 61 tests: swap parsing, candles, launches, holders, smart money, worker
cd api     && python -m pytest        # 26 tests: endpoints, WebSocket hub, sign-in, link previews
cd web     && npm test && npm run lint && npm run i18n:check   # 190 tests
```

A few tests in `indexer/` and `api/` need a throwaway `mongod` on port 27999 and are skipped without it.

## Architecture

```
 NEAR mainnet
     │  optimistic blocks (neardata)
     ▼
┌─────────────────────────────┐        ┌──────────────────────────┐        ┌───────────────────────┐
│ indexer/  (Python, asyncio) │        │ api/  (FastAPI)          │        │ web/  (React + Vite)  │
│                             │        │                          │        │                       │
│ BlockStream ─► DashWorker   │ Mongo  │ REST  /v2/dash/*  cached │  HTTP  │ widgets, token page,  │
│             ├► SmartMoney   ├───────►│ WS    /v2/dash/ws        ├───────►│ live lists, i18n en/ru│
│             └► LaunchStats  │ dash_* │ wallet sign-in (NEP-413) │   WS   │ Telegram Mini App     │
└─────────────────────────────┘        └──────────────────────────┘        └───────────────────────┘
```

- **`indexer/`** — one block stream per process and its subscribers. `DashWorker` parses each block (swaps of Rhea DCL,
  Intear DEX and Rhea v1; launchpad events; NEP-141 transfers; account and contract creation) and writes the `dash_*`
  collections in batches. Writes are idempotent (upserts by receipt) or increments, gaps are detected by `prev_height`
  and refetched, a lease keeps a single writer per cluster. `SmartMoney` nets every swap per transaction into
  per-wallet flows and re-scores wallets hourly. `LaunchStats` follows what each creator does with his own tokens.
  Two background tasks fetch reference data: USD rates (REF Finance price list) and token metadata (one NEP-148
  `ft_metadata` view per new token).
- **`api/`** — read-only REST with a single-flight TTL cache and a WebSocket hub that tails a capped collection of
  events (`launch`, `trade`, `move`, `new`, `network` channels). The only RPC call is `view_access_key`, once per
  wallet sign-in.
- **`web/`** — static SPA. REST on mount, then live data only through the socket (events are reduced straight into
  the react-query cache). Chain strings are treated as untrusted everywhere.

The contract between the indexer and the API — every collection and field — is
[indexer/docs/DASH_DATA.md](indexer/docs/DASH_DATA.md); the endpoint reference is
[api/docs/API_ROUTES.md](api/docs/API_ROUTES.md) (also served as OpenAPI at `/docs`).

## What NEAR does here

- **Block data** — neardata optimistic blocks, parsed statelessly in [indexer/services/chain/](indexer/services/chain/):
  receipts, `EVENT_JSON` logs, function calls.
- **DEX swaps** — Rhea DCL (`dclv2.ref-labs.near`, `dcl.ref` events), Rhea v1 (`v2.ref-finance.near`, swap logs),
  Intear DEX (`dex.intear.near`, lz4 + borsh `dex_events_borsh` events decoded in
  [intear.py](indexer/services/chain/intear.py)).
- **Launchpads** — Nearly (`nearlytrade.near`), JustHoot (`*.justhoot.near`), Intear launch-v2, recognised by root
  account so a new launcher version is still picked up; a DCL `create_pool` fallback catches format changes
  ([feed.py](indexer/services/dashboard/feed.py)).
- **NEP-141** — `ft_mint` / `ft_transfer` / `ft_burn` events for holders, creator statistics and big moves;
  **NEP-148** for token metadata; **NEP-171** events to tell NFT contracts from fungible tokens.
- **NEP-413** — wallet sign-in: the site gets a one-time nonce, the wallet signs it, the API verifies the ed25519
  signature and that the key is a FullAccess key of the account ([dash_near.py](api/services/dash_near.py)).

## Repository layout

```
indexer/
  main.py                    entry point: block stream + workers + health server
  indexer/block_stream.py    optimistic block stream (long-poll "next block", polling fallback)
  services/chain/            stateless block parsing: receipts, events, swaps, Intear borsh
  services/dashboard/        worker, trades & candles, launches, holders, network, smart money,
                             creator statistics, reference data, event feed
  scripts/                   history backfill, holder reconciliation, manual account labels
  docs/DASH_DATA.md          data contract with the API
api/
  app/main.py                FastAPI app
  api/routes/                /v2/dash (public data, WebSocket) and /v2/dash/me (sign-in, layouts, watchlist)
  services/                  queries + cache, WebSocket hub, auth, holders, link previews (OG images)
  docs/API_ROUTES.md         endpoint reference
web/
  src/                       app shell, API client + live reducers, widgets, pages, ui kit, locales
thesis/
  build.py, render.py        recompute the snapshot from the database; rebuild numbers and charts (plain Python)
  data/                      snapshot.json (aggregates), numbers.md (every number of THESIS.md and its source)
  charts/                    SVG charts, light and dark
THESIS.md                    the investment thesis
```

## Hackathon window and earlier work

The dashboard — the indexer worker, the API, the site and the thesis — was built between 9 and 12 November 2026,
inside the hackathon window (announced 2026-10-19). It runs in production next to other Sendler services (an NFT
marketplace indexer and a Telegram trading bot), and a few building blocks were taken from that codebase. This
repository is an extraction of the dashboard so that it runs on its own.

About 93% of the code here was written inside the window. The rest — eight files, about 1,560 of 23,400 lines of
source — is adapted from code that existed **before** the announcement (the baseline):

| In this repository | Origin (as of 2026-10-18) |
|---|---|
| `indexer/indexer/block_stream.py` | the optimistic block stream |
| `indexer/services/dashboard/feed.py`, `links.py` | the launchpad event parser and creator links |
| `indexer/services/chain/intear.py` | the Intear DEX pool decoder |
| `indexer/services/chain/swaps.py`, `block.py` (in part) | swap parsing of Rhea DCL / Intear / Rhea v1 |
| `indexer/services/dashboard/launch_stats.py` | creator statistics per launch |
| `indexer/health/server.py` | health and metrics server |

The original versions of those files are in the commit tagged `pre-hackathon`
(`baseline/`); everything else is new. The whole hackathon diff:

```bash
git diff pre-hackathon hackathon-submission --stat
```

On GitHub: https://github.com/dialogwtf/sendler-dashboard/compare/pre-hackathon...hackathon-submission

Written inside the window: all of `web/`, all of `api/`, and in `indexer/` — `worker.py`, `trades.py`,
`launches.py`, `holders.py`, `network.py`, `blocks.py`, `prices.py`, `events.py`, `history.py`, `smart.py`,
`fee_farm.py`, `reference.py`, the scripts and the tests; and all of `thesis/`.

### What is not in this repository

- **Telegram sign-in** and the "Buy in the bot" deep links are answered by the Sendler trade bot, a separate
  product. Wallet sign-in works on its own.
- **Creator blocklists and linked wallets** (`sniper_devs_blocked`, `sniper_dev_links`) are data of the bot's users;
  the corresponding fields of the creator dossier and of the holders view are empty in a standalone run.
- **The NFT market widget** reads collections written by the Sendler marketplace indexer (`collection_stats`,
  `market_trades`); it is empty without them.
- **History before the first run**: the backfill scripts read the marketplace indexer's `ft_transfers`. A fresh
  standalone run starts from the current block.

Collections prefixed `sniper_` keep their production names (smart money, creator statistics and bot link codes are
shared with the bot).

## Libraries and tools

- **indexer:** Python, asyncio, aiohttp, Motor / PyMongo, MongoDB.
- **api:** FastAPI, Starlette, Uvicorn, slowapi, Motor, httpx, PyNaCl, Pillow. Fonts: Onest and Unbounded (SIL OFL,
  licences in `api/assets/fonts/`).
- **web:** React 19, TypeScript, Vite, Tailwind CSS 4, TanStack Query, React Router, Radix UI primitives, cmdk,
  lightweight-charts, i18next, Vitest.
- **Data sources:** neardata (FastNear) block API, REF Finance price list, NEAR RPC (`view_access_key`,
  `ft_metadata`), FastNear FT API (holder snapshots in one script).
- **AI assistance:** the code was written with AI coding assistants (Claude Code) under the author's direction and
  review.

## License

[MIT](LICENSE).
