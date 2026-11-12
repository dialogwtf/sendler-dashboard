# Sendler Dash — frontend

Public dashboard of NEAR memes and network (dash.sendler.xyz). Static SPA: `vite build` → `dist`.
Data: REST `https://api.sendler.xyz/v2/dash` + WebSocket `wss://api.sendler.xyz/v2/dash/ws`
(written by `indexer/`, served by `api/` of this repository).

```bash
cp .env.example .env
npm install
npm run dev        # http://localhost:5173 — talks to the production API (CORS allows localhost:5173)
npm test           # vitest: format, bot links, theme contrast, WebSocket manager, cache sync
npm run lint
npm run i18n:check # en/ru keys and placeholders, unknown t() keys, Russian text in code
npm run build
```

Dev pages (only in `npm run dev`, or a build with `VITE_DEV_PAGES=1`): `/dev/ui` — every building
block in both themes (compare with the «Основы» and «Состояния» boards); `/dev/live` — socket
status, block height and raw events from production.

## Build

`npm run build` produces a static site in `dist/` — serve it with any web server, with a fallback to
`index.html` for client-side routes. The API and its WebSocket are a separate origin (`VITE_DASH_API`, `VITE_DASH_WS`).

## Layout

```
src/
  app/        App (router), Shell (header), providers (one QueryClient + one socket per tab), i18n, live hooks
  api/        client.ts (fetch, 429 pause, server clock), types.ts, queries.ts (keys + staleTime),
              ws.ts (subscriptions, reconnect, fallback), reducers.ts + live.ts (events → react-query cache)
  widgets/    one folder per widget + registry (step 5)
  layouts/    preset layouts «Мемы», «Сеть»; choice.ts — the chosen one (localStorage)
  pages/      Home, Token, List, Account, NotFound, dev/
  ui/         Widget frame (states, S/M/L by container width), LiveList, TokenCell, AccountCell, Num, Age,
              Freshness, Chip/Tag/StagePill, BuyInBot
  lib/        format.ts (all number rules), botLinks.ts (t_<code> = promo.py token_code), clock.ts,
              theme.ts, safeUrl.ts, text.ts (chain strings)
  locales/    ru/*.json, en/*.json (common of both in the main bundle)
  components/ shadcn primitives copied from near-nft-market, recoloured to the dashboard roles
```

## Rules that matter

- Chain strings are untrusted: text only, no `dangerouslySetInnerHTML`; links only `https:` with
  `rel="noopener noreferrer nofollow"`; token icons only as `<img src>`.
- Numbers only through `lib/format.ts`; raw u128 amounts through BigInt; a missing value is «—», never 0.
- Colour never carries meaning alone: ▲/▼ and a sign on every change.
- Live data comes only through the socket; REST on mount, after a reconnect, and polling only for
  widgets without a channel (or while the socket is in 4429/4503 fallback). 300 requests/min per IP.
