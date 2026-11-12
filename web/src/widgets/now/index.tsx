import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { ArrowLeftRight, ArrowRight, Brain, Rocket, Sparkles } from "lucide-react";
import { useTokenMeta } from "@/api/batch";
import { launchesQuery, movesQuery, newItemsQuery, smartFeedQuery, smartTopQuery } from "@/api/queries";
import { moveKey } from "@/api/reducers";
import type { Label, Launch, Move, NewItem, SmartTrade } from "@/api/types";
import { useChannels, usePollWhenOffline } from "@/app/live";
import { msOf } from "@/lib/clock";
import { account as shortAccount, mult, multTrend, near, usd } from "@/lib/format";
import { useMediaQuery } from "@/lib/useMediaQuery";
import { txUrl } from "@/lib/safeUrl";
import { clean, tickerOf } from "@/lib/text";
import { useLang } from "@/lib/useLang";
import { AccountCell, accountPath } from "@/ui/AccountCell";
import { Age } from "@/ui/Age";
import { PadChip, SmartTag } from "@/ui/Chip";
import { Freshness } from "@/ui/Freshness";
import { LiveList } from "@/ui/LiveList";
import { Num } from "@/ui/Num";
import { INNER, PanelLinks, Row, TokenActions } from "@/ui/Row";
import { Segmented } from "@/ui/Segmented";
import { tokenPath } from "@/ui/TokenCell";
import { usePhoneFrame, Widget } from "@/ui/Widget";
import { isFeeFarm } from "../launches/facts";
import { Side, What } from "../moves/index";
import { kindOf, NameCell, targetOf } from "../new/index";
import { SIGNAL_MIN_NEAR, Signal } from "../smart/index";
import type { WidgetDef, WidgetProps } from "../types";

/**
 * «Сейчас» (plan step 12.3): one feed of what is happening — new launches, smart-money trades,
 * big moves, new tokens and pools — newest first. Built from the caches the other widgets use
 * (same query keys as their defaults), so it adds no API requests: the socket already writes
 * every event there.
 */

export type NowKind = "launch" | "smart" | "move" | "new";
type Filter = "all" | NowKind;
interface Settings extends Record<string, unknown> {
    kind: Filter;
}

const ALL_PADS = ["nearly", "justhoot", "intear"];
/** Rows kept in the feed. */
const MAX_ROWS = 120;
/**
 * Big moves in the mixed feed: from $250k, without bridges, hubs and exchanges. Measured on prod
 * 2026-11-11 (24 h): moves from $10k without bridges — 1223, against 24 launches, ~15 smart trades
 * and ~50 new tokens and pools — the feed was all transfers between hubs. Without hub or exchange
 * sides: from $100k — 47 (and they come in bursts: one whale's money hops through 4–5 wallets in
 * minutes, filling the screen), from $250k — 16, about a sixth of the feed. The rest stays in «Крупные».
 */
export const NOW_MOVE_MIN_USD = 250_000;

export type NowItem =
    | { kind: "launch"; key: string; at: number; row: Launch }
    | { kind: "smart"; key: string; at: number; row: SmartTrade }
    | { kind: "move"; key: string; at: number; row: Move }
    | { kind: "new"; key: string; at: number; row: NewItem };

/**
 * Bridge plumbing: NEAR Intents and the bridges' connectors (`omni.bridge.near`,
 * `btc-connector.bridge.near`, …) move big sums all the time — the bulk of moves from $10k.
 * Labels alone miss them: only `intents.near` carries the `bridge` kind, the connectors none.
 */
function isBridgeSide(id: string, label: Label | null | undefined): boolean {
    return id === "intents.near" || id.endsWith(".bridge.near") || label?.kind === "bridge";
}

export function isBridgeMove(m: Move): boolean {
    return isBridgeSide(m.from, m.from_label) || isBridgeSide(m.to, m.to_label);
}

/** Hubs (accounts with > 50 counterparties a day) and exchanges move money around all day long. */
export function isHubMove(m: Move): boolean {
    const hub = (l: Label | null | undefined) => l?.kind === "hub" || l?.kind === "exchange";
    return hub(m.from_label) || hub(m.to_label);
}

/** A hop: the same money (±3 % in $) leaves the wallet it reached within this time. */
const HOP_MS = 30 * 60_000;
const HOP_SHARE = 0.03;

/**
 * One whale's money hops through 2–5 wallets in minutes (c0d0 → 75b5 → f14a…), each hop a row of
 * the same sum. The feed keeps the first move of such a chain; «Крупные» shows every hop.
 */
export function dropHops(moves: Move[], at: (ts: string) => number): Move[] {
    const byTime = [...moves].sort((a, b) => at(a.ts) - at(b.ts));
    const arrived = new Map<string, { usd: number; at: number }[]>();
    const keep = new Set<Move>();
    for (const m of byTime) {
        const t = at(m.ts);
        const into = arrived.get(`${m.from}|${m.token}`) ?? [];
        const hop = into.some((x) => t - x.at <= HOP_MS && t >= x.at && Math.abs(m.usd - x.usd) <= x.usd * HOP_SHARE);
        if (!hop) keep.add(m);
        const k = `${m.to}|${m.token}`;
        arrived.set(k, [...(arrived.get(k) ?? []), { usd: m.usd, at: t }]);
    }
    return moves.filter((m) => keep.has(m));
}

/**
 * The rows each source contributes: launches with a start, smart trades from 0.3 Ⓝ, moves from
 * NOW_MOVE_MIN_USD without bridges, hubs, exchanges and the hops of one chain (they stay in
 * «Крупные»), new tokens and pools.
 */
export function mergeNow(
    src: { launches?: Launch[]; smart?: SmartTrade[]; moves?: Move[]; news?: NewItem[] },
    filter: Filter = "all",
    max = MAX_ROWS,
): NowItem[] {
    const out: NowItem[] = [];
    const want = (k: NowKind) => filter === "all" || filter === k;
    const at = (ts: string | number | null | undefined) => msOf(ts) ?? 0;
    if (want("launch")) {
        for (const r of src.launches ?? []) {
            if (r.started && !isFeeFarm(r.fee_farm)) out.push({ kind: "launch", key: `l:${r.token}`, at: at(r.started), row: r });
        }
    }
    if (want("smart")) {
        for (const r of src.smart ?? []) {
            if (r.near >= SIGNAL_MIN_NEAR) out.push({ kind: "smart", key: `s:${r._id}`, at: at(r.ts), row: r });
        }
    }
    if (want("move")) {
        const moves = (src.moves ?? []).filter((r) => r.usd >= NOW_MOVE_MIN_USD && !isBridgeMove(r) && !isHubMove(r));
        for (const r of dropHops(moves, at)) out.push({ kind: "move", key: `m:${moveKey(r)}`, at: at(r.ts), row: r });
    }
    if (want("new")) {
        for (const r of src.news ?? []) {
            const k = kindOf(r);
            if (k === "ft" || k === "pool") out.push({ kind: "new", key: `n:${r._id}`, at: at(r.first_seen), row: r });
        }
    }
    // Newest first; equal times keep a stable order by key so rows never swap places.
    out.sort((a, b) => b.at - a.at || (a.key < b.key ? -1 : a.key > b.key ? 1 : 0));
    return out.slice(0, max);
}

/** The data of the feed: the four sources and the channels that keep them live. */
export function useNowSources() {
    useChannels(["launch", "trade", "move", "new"]);
    const poll = usePollWhenOffline(5_000);
    const launches = useQuery({ ...launchesQuery({ pad: ALL_PADS, hideFeeFarm: true }), refetchInterval: poll });
    // The ranking of socket trades comes from the top-200 (LiveSync reads it from the cache).
    useQuery(smartTopQuery(200));
    const smart = useQuery({ ...smartFeedQuery(50), refetchInterval: poll });
    // Its own threshold: the page of «Крупные» from $10k would hold only an hour of $100k moves.
    const moves = useQuery({ ...movesQuery({ minUsd: NOW_MOVE_MIN_USD }), refetchInterval: poll });
    const news = useQuery({ ...newItemsQuery({}), refetchInterval: usePollWhenOffline(10_000) });
    return { launches, smart, moves, news };
}

function KindIcon({ kind, swap }: { kind: NowKind; swap?: boolean }) {
    const { t } = useTranslation();
    const Icon = kind === "launch" ? Rocket : kind === "smart" ? Brain : kind === "new" ? Sparkles : swap ? ArrowLeftRight : ArrowRight;
    return (
        <span className={kind === "launch" ? "grid size-8 place-items-center rounded-[9px] bg-brand-soft text-brand-text" : "grid size-8 place-items-center rounded-[9px] bg-surface-2 text-fg-2"}>
            <Icon className="size-4" role="img" aria-label={t(`now.kind.${kind}`)} />
        </span>
    );
}

const ROW = "h-[58px] grid-cols-[32px_minmax(0,1fr)_auto]";
const LINE2 = "mt-[3px] flex min-w-0 items-center gap-1.5 text-[12px] text-fg-3";

function LaunchLine({ l }: { l: Launch }) {
    const { t } = useTranslation();
    const lang = useLang();
    const meta = useTokenMeta(l.token);
    const ticker = tickerOf(l.symbol ?? meta.data?.symbol, l.token);
    const name = clean(l.name ?? meta.data?.name, 40);
    return (
        <Row to={tokenPath(l.token)} label={`${t("now.kind.launch")} $${ticker}`} className={ROW} actions={<TokenActions token={l.token} code={meta.data?.bot_code} />}>
            <KindIcon kind="launch" />
            <div className="min-w-0">
                <div className="truncate font-semibold">
                    {`$${ticker}`}
                    {name && name !== ticker && <span className="font-normal text-fg-2"> {name}</span>}
                </div>
                <div className={LINE2}>
                    <span className="shrink-0">{t("now.launched")}</span>
                    <PadChip pad={l.pad} />
                    <Age at={l.started} className="shrink-0" />
                    {l.creator && <AccountCell id={l.creator} link={false} className="min-w-0 shrink text-fg-2 [&_span]:!text-[12px]" />}
                </div>
            </div>
            <div className="flex flex-col items-end gap-0.5">
                <Num strong>{l.cap_usd !== null ? usd(l.cap_usd, lang) : "—"}</Num>
                <Num trend={multTrend(l.x)} className="text-[12px] font-semibold">{l.x === null ? t("launches.atStart") : mult(l.x, lang)}</Num>
            </div>
        </Row>
    );
}

function SmartLine({ r }: { r: SmartTrade }) {
    const { t } = useTranslation();
    const lang = useLang();
    const meta = useTokenMeta(r.token);
    const ticker = tickerOf(r.symbol ?? meta.data?.symbol, r.token);
    const buy = r.side === "buy";
    return (
        <Row to={tokenPath(r.token)} label={`${buy ? t("smart.bought") : t("smart.sold")} $${ticker}`} className={ROW} actions={<TokenActions token={r.token} code={meta.data?.bot_code} />}>
            <KindIcon kind="smart" />
            <div className="min-w-0">
                <div className="truncate">
                    <Num trend={buy ? "up" : "down"} strong>{buy ? t("smart.bought") : t("smart.sold")}</Num>{" "}
                    <b className="font-semibold">{`$${ticker}`}</b>
                </div>
                <div className={LINE2}>
                    <SmartTag rank={r.smart.rank} />
                    <AccountCell id={r.wallet} link={false} className="min-w-0 shrink text-fg-2 [&_span]:!text-[12px]" />
                    <Age at={r.ts} className="shrink-0" />
                </div>
            </div>
            <Num strong className="text-right">{near(r.near, lang)}</Num>
        </Row>
    );
}

function MoveLine({ m }: { m: Move }) {
    const { t } = useTranslation();
    const lang = useLang();
    // Touch: «from → to» are too small to tap — the row's tap panel offers them (as in «Крупные»).
    const touch = useMediaQuery("(hover: none)");
    return (
        <Row
            href={m.tx ? txUrl(m.tx) : undefined}
            label={t("moves.rowLabel", { usd: usd(m.usd, lang) })}
            className={ROW}
            actions={touch ? (
                <PanelLinks
                    links={[
                        { label: <>{t("moves.from")} <span className="truncate font-mono font-normal" translate="no">{shortAccount(m.from)}</span></>, to: accountPath(m.from) },
                        { label: <>{t("moves.toShort")} <span className="truncate font-mono font-normal" translate="no">{shortAccount(m.to)}</span></>, to: accountPath(m.to) },
                    ]}
                />
            ) : undefined}
        >
            <KindIcon kind="move" swap={m.kind === "swap"} />
            <div className="min-w-0">
                <div className="truncate">
                    <What m={m} /> <span className="text-[12px] text-fg-3">· <Age at={m.ts} /></span>
                </div>
                <div className="mt-[3px] flex min-w-0 items-center gap-1 text-[11px] text-fg-2">
                    <Side id={m.from} label={m.from_label} link={!touch} />
                    <span aria-hidden="true" className="shrink-0">→</span>
                    <span className="sr-only">{t("moves.to")}</span>
                    <Side id={m.to} label={m.to_label} link={!touch} />
                </div>
            </div>
            <Num strong className="text-right">{usd(m.usd, lang)}</Num>
        </Row>
    );
}

function NewLine({ item }: { item: NewItem }) {
    const { t } = useTranslation();
    const k = kindOf(item);
    const meta = useTokenMeta(k === "ft" ? item.id : null);
    return (
        <Row
            to={targetOf(item)}
            label={`${t(`now.new.${k}`)} ${item.id}`}
            className={ROW}
            actions={k === "ft" ? <TokenActions token={item.id} code={meta.data?.bot_code} /> : undefined}
        >
            <KindIcon kind="new" />
            <div className="min-w-0">
                <div className="flex min-w-0"><NameCell item={item} /></div>
                <div className={LINE2}>
                    <span className="shrink-0">{t(`now.new.${k}`)}</span>
                    {item.by && <AccountCell id={item.by} className={`${INNER} min-w-0 shrink text-fg-2 [&_span]:!text-[12px]`} />}
                </div>
            </div>
            <Age at={item.first_seen} className="text-right text-[12px] text-fg-3" />
        </Row>
    );
}

function NowRow({ it }: { it: NowItem }) {
    switch (it.kind) {
        case "launch": return <LaunchLine l={it.row} />;
        case "smart": return <SmartLine r={it.row} />;
        case "move": return <MoveLine m={it.row} />;
        case "new": return <NewLine item={it.row} />;
    }
}

const nowKey = (it: NowItem) => it.key;

function NowWidget({ settings, setSettings, height }: WidgetProps<Settings>) {
    const { t } = useTranslation();
    const { launches, smart, moves, news } = useNowSources();
    const all = [launches, smart, moves, news];
    // Phone tab: no filter of its own — the tabs next to it are those filters («Запуски», «Умные»…).
    const phone = usePhoneFrame();
    const kind: Filter = phone ? "all" : settings.kind;
    const rows = React.useMemo(
        () => mergeNow({ launches: launches.data, smart: smart.data, moves: moves.data, news: news.data }, kind),
        [launches.data, smart.data, moves.data, news.data, kind],
    );
    const pending = all.some((q) => q.isPending);
    const failed = all.every((q) => q.isError && !q.data);
    const filtered = kind !== "all";
    return (
        <Widget
            title={t("widgets.now.title")}
            freshness={<Freshness updatedAt={Math.max(...all.map((q) => q.dataUpdatedAt))} />}
            filters={phone ? undefined : (
                <Segmented
                    label={t("now.filter")}
                    value={settings.kind}
                    onChange={(k) => setSettings({ kind: k })}
                    options={[
                        { value: "all", label: t("now.tab.all") },
                        { value: "launch", label: t("now.tab.launch") },
                        { value: "smart", label: t("now.tab.smart") },
                        { value: "move", label: t("now.tab.move") },
                        { value: "new", label: t("now.tab.new") },
                    ]}
                />
            )}
            state={pending && !rows.length ? "loading" : failed ? "error" : rows.length ? "ready" : "empty"}
            error={launches.error ?? smart.error}
            onRetry={() => all.forEach((q) => void q.refetch())}
            empty={{
                title: t("now.empty"),
                hint: t("now.emptyHint"),
                action: filtered ? { label: t("now.showAll"), onClick: () => setSettings({ kind: "all" }) } : undefined,
            }}
            height={height}
            skeletonRows={9}
        >
            {(kind === "all" || kind === "smart") && <Signal feed={smart.data ?? []} />}
            {/* A new filter is a new list: rows it brings back are not «new events». */}
            <LiveList key={kind} items={rows} getKey={nowKey} renderRow={(it) => <NowRow it={it} />} />
        </Widget>
    );
}

/** The tab's badge (phone only, where the feed has no filter): everything the feed shows. */
function useNowPulse(_s: Settings): number[] {
    const { launches, smart, moves, news } = useNowSources();
    return mergeNow({ launches: launches.data, smart: smart.data, moves: moves.data, news: news.data }).map((x) => x.at);
}

export const nowWidget: WidgetDef<Settings> = {
    type: "now",
    title: "widgets.now.title",
    size: { default: "L", min: "M" },
    settings: [],
    defaults: { kind: "all" },
    channels: () => ["launch", "trade", "move", "new"],
    Component: NowWidget,
    usePulse: useNowPulse,
    choices: { kind: ["all", "launch", "smart", "move", "new"] },
};
