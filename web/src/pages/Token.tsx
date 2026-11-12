import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { ChevronDown, ChevronLeft, LineChart, CandlestickChart, Share2 } from "lucide-react";
import { DropdownMenu, DropdownMenuContent, DropdownMenuLabel, DropdownMenuRadioGroup, DropdownMenuRadioItem, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { candlesQuery, creatorQuery, healthQuery, smartTopQuery, tokenQuery, tradesQuery } from "@/api/queries";
import { bucketStart } from "@/api/reducers";
import type { CandleInterval, Creator, Launch, SmartWallet, TokenInfo, Trade } from "@/api/types";
import { useChannels, usePollWhenOffline } from "@/app/live";
import { TokenIcon } from "@/components/common/TokenIcon";
import { account as shortAccount, age, axisValue, change, count, mult, multTrend, near, perToken, price, tokenAmount, trendOf, usd } from "@/lib/format";
import { watchTokenUrl } from "@/lib/botLinks";
import { EXTERNAL_REL, explorerAccountUrl, txUrl } from "@/lib/safeUrl";
import { clean, tickerOf } from "@/lib/text";
import { useLang } from "@/lib/useLang";
import { useMediaQuery } from "@/lib/useMediaQuery";
import { telegramBack } from "@/lib/telegram";
import { cn } from "@/lib/utils";
import { AccountCell, accountPath } from "@/ui/AccountCell";
import { Age } from "@/ui/Age";
import { BuyInBot } from "@/ui/BuyInBot";
import { Chip, CreatorTag, PadChip, SmartTag, venueName } from "@/ui/Chip";
import { CopyButton } from "@/ui/CopyButton";
import { Freshness } from "@/ui/Freshness";
import { LiveList } from "@/ui/LiveList";
import { Num } from "@/ui/Num";
import { INNER, PanelLinks, Row, WalletActions, WatchInBot } from "@/ui/Row";
import { Segmented } from "@/ui/Segmented";
import { UnderlineTabs, panelId, tabId } from "@/ui/UnderlineTabs";
import { Fact, Tile, TileSkeletons } from "@/ui/Tile";
import { PhoneFrame, Widget, WidgetBoundary } from "@/ui/Widget";
import { useWidgetSettings } from "@/widgets/settings";
import { creatorFact } from "@/widgets/launches/facts";
import { HoldersSection } from "./token/Holders";
import { PriceChart, type Bar, type Mark } from "./token/PriceChart";

/** /token/:id (Token and MToken boards): header, stats, price chart, trades, creator, smart money. */
export default function TokenPage() {
    const { id = "" } = useParams();
    return <TokenView key={id} id={id} />;
}

type Unit = "near" | "usd";
interface ChartPrefs extends Record<string, unknown> {
    i: CandleInterval;
    unit: Unit;
    mode: "auto" | "candles" | "line";
    /** Vertical axis: price per token or market cap (price × supply; only when the supply is known). */
    axis: "price" | "cap";
}

function TokenView({ id }: { id: string }) {
    const { t } = useTranslation();
    const lang = useLang();
    useChannels([`trade:${id}`]);
    const poll = usePollWhenOffline(5_000);
    const info = useQuery({ ...tokenQuery(id), refetchInterval: poll });
    const health = useQuery(healthQuery());
    const nearUsd = health.data?.near_usd ?? null;
    const d = info.data;
    const ticker = tickerOf(d?.symbol, id);
    const launch = d?.launch ?? null;
    const creator = launch?.creator ?? null;
    const phone = !useMediaQuery("(min-width: 768px)");
    const [tab, setTab] = React.useState<PhoneTab>("trades");
    const tabsId = React.useId();

    React.useEffect(() => {
        document.title = `$${ticker} — Sendler Dash`;
        return () => { document.title = "Sendler Dash"; };
    }, [ticker]);

    const holders = <WidgetBoundary title={t("token.holders")}><HoldersSection id={id} /></WidgetBoundary>;
    // Each section behind its own boundary (plan step 10): one crashing section never blanks the page.
    const chart = <WidgetBoundary title={t("token.price")}><ChartSection id={id} info={d} nearUsd={nearUsd} creator={creator} /></WidgetBoundary>;
    const trades = <WidgetBoundary title={t("token.tradesTitle")}><TradesSection id={id} info={d} creator={creator} /></WidgetBoundary>;
    const creatorBox = creator && launch
        ? <WidgetBoundary title={t("token.creator")}><CreatorSection account={creator} launch={launch} /></WidgetBoundary>
        : null;
    const smartBox = <WidgetBoundary title={t("token.smartTitle")}><SmartSection info={d} /></WidgetBoundary>;

    if (phone) {
        // Variant B («Плотная сводка», canvas «экран токена»): one-row header, a strip of four numbers,
        // the chart under one row of controls, tabs; name, contract and the rest at the bottom.
        const tabs: { value: PhoneTab; label: string }[] = [
            { value: "trades", label: t("token.tradesTitle") },
            ...(creator && launch ? [{ value: "creator" as const, label: t("token.creator") }] : []),
            { value: "holders", label: t("token.holders") },
        ];
        const shown = tabs.some((x) => x.value === tab) ? tab : "trades";
        return (
            <div className="pb-24">
                <PhoneHeader id={id} info={d} ticker={ticker} />
                <PhoneStrip info={d} loading={info.isPending} />
                {chart}
                {/* Tabs like the home's (underline), the open section edge to edge under them: the tab
                    names it, so its own title goes (PhoneFrame) and its filters are one row of chips. */}
                <UnderlineTabs
                    label={t("token.sections")}
                    baseId={tabsId}
                    value={shown}
                    onChange={setTab}
                    options={tabs}
                    className="-mx-4 mt-1 border-b border-surface-2"
                />
                <div id={panelId(tabsId, shown)} role="tabpanel" aria-labelledby={tabId(tabsId, shown)} className="-mx-4">
                    <PhoneFrame value={true}>
                        {shown === "trades" && trades}
                        {shown === "creator" && creatorBox}
                        {shown === "holders" && holders}
                    </PhoneFrame>
                </div>
                <div className="mt-4">{smartBox}</div>
                <div className="mt-4"><AboutSection id={id} info={d} /></div>
                {/* «Купить в боте» pinned to the bottom (MToken board); the bottom tabs are not shown here. */}
                <div className="fixed inset-x-0 bottom-0 z-20 flex gap-2.5 border-t border-surface-2 bg-inset px-4 pt-3 pb-[max(12px,var(--safe-bottom))]">
                    <BuyInBot token={id} className="h-[50px] flex-1 rounded-xl text-[15px] font-bold" />
                    <WatchInBot url={watchTokenUrl(id)} />
                </div>
                <span className="sr-only" aria-live="polite">{d?.price_near != null ? `${price(d.price_near, lang)} NEAR` : ""}</span>
            </div>
        );
    }

    return (
        <div>
            <Header id={id} info={d} ticker={ticker} />
            <Stats info={d} loading={info.isPending} />
            <div className="mt-4 grid items-start gap-4 xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
                <div className="flex min-w-0 flex-col gap-4">
                    {chart}
                    {trades}
                </div>
                <div className="flex min-w-0 flex-col gap-4">
                    {creatorBox}
                    {smartBox}
                    {holders}
                </div>
            </div>
            <span className="sr-only" aria-live="polite">{d?.price_near != null ? `${price(d.price_near, lang)} NEAR` : ""}</span>
        </div>
    );
}

// --- header ------------------------------------------------------------------------------------

function shareToken(ticker: string) {
    const data = { title: `$${ticker} — Sendler Dash`, url: window.location.href };
    if (navigator.share) void navigator.share(data).catch(() => {});
    else void navigator.clipboard?.writeText(data.url);
}

/** Phone header (MToken board): back · logo · $TICKER · pad · share; name, age and address under it. */
function PhoneHeader({ id, info, ticker }: { id: string; info: TokenInfo | undefined; ticker: string }) {
    const { t } = useTranslation();
    const navigate = useNavigate();
    const location = useLocation();
    const launch = info?.launch;
    // One row: the name, the contract and the explorer link live in «О токене» at the bottom.
    return (
        <div className={cn("-mt-2 flex h-12 items-center gap-2", telegramBack() ? "-mr-2" : "-mx-2")}>
            {/* Inside Telegram its own «Назад» does this (App: TelegramBack) — no second arrow. */}
            {!telegramBack() && (
                <button
                    type="button"
                    onClick={() => (location.key !== "default" ? navigate(-1) : navigate("/"))}
                    className="grid size-11 shrink-0 place-items-center rounded-lg text-fg-2 hover:text-fg"
                    aria-label={t("nav.back")}
                >
                    <ChevronLeft className="size-[22px]" aria-hidden="true" />
                </button>
            )}
            <TokenIcon symbol={ticker} icon={info?.icon} seed={id} size={30} />
            <h1 className="min-w-0 truncate font-display text-[18px] leading-tight font-bold">{`$${ticker}`}</h1>
            {launch && <PadChip pad={launch.pad} />}
            {launch?.started && (
                <span className="shrink-0 text-[12.5px] text-fg-3">
                    <span className="sr-only">{t("token.launched")} </span>
                    <Age at={launch.started} />
                    <span className="sr-only"> {t("token.ago")}</span>
                </span>
            )}
            <span className="flex-1" />
            <button
                type="button"
                onClick={() => shareToken(ticker)}
                className="grid size-11 shrink-0 place-items-center rounded-lg text-fg-2 hover:text-fg"
                aria-label={t("token.share")}
            >
                <Share2 className="size-[18px]" aria-hidden="true" />
            </button>
        </div>
    );
}

function Header({ id, info, ticker }: { id: string; info: TokenInfo | undefined; ticker: string }) {
    const { t } = useTranslation();
    const name = clean(info?.name, 60);
    const launch = info?.launch;
    const share = () => shareToken(ticker);
    return (
        <div className="flex flex-wrap items-center gap-x-5 gap-y-3">
            <TokenIcon symbol={ticker} icon={info?.icon} seed={id} size={64} />
            <div className="min-w-0 flex-[1_1_320px]">
                <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
                    <h1 className="font-display text-[32px] leading-tight font-bold break-all max-md:text-[24px]">{`$${ticker}`}</h1>
                    {name && name !== ticker && <span className="text-[16px] text-fg-2">{name}</span>}
                    {launch && <PadChip pad={launch.pad} />}
                    {launch?.started && (
                        <Chip>{t("token.launched")} <Age at={launch.started} /> {t("token.ago")}</Chip>
                    )}
                </div>
                <div className="mt-1 flex min-w-0 flex-wrap items-center gap-x-2">
                    <span className="min-w-0 truncate font-mono text-[12px] text-fg-2" translate="no">{id}</span>
                    <CopyButton value={id} label={t("token.copyAddress")} />
                    <a href={explorerAccountUrl(id)} target="_blank" rel={EXTERNAL_REL} className="text-[12px] text-brand-text hover:underline pointer-coarse:inline-flex pointer-coarse:min-h-11 pointer-coarse:items-center">
                        {t("token.explorer")}
                    </a>
                </div>
            </div>
            <div className="flex flex-wrap gap-2 max-md:hidden">
                <BuyInBot token={id} className="h-10 px-4" />
                <WatchInBot url={watchTokenUrl(id)} wide />
                <button
                    type="button"
                    onClick={share}
                    className="grid h-9 w-11 place-items-center rounded-[9px] bg-surface-2 text-fg hover:bg-line"
                    aria-label={t("token.share")}
                >
                    <Share2 className="size-4" aria-hidden="true" />
                </button>
            </div>
        </div>
    );
}

type PhoneTab = "trades" | "creator" | "holders";

/** Phone: the price large, its 24 h change and the $ price under it (MToken board). */
/**
 * Phone: four numbers in one strip, edge to edge. A launch: cap (the lead, larger) · 24 h · since
 * start · volume — the same cap the chart draws. Any other token: price in $ instead of the cap,
 * traders instead of «since start». Price in Ⓝ is next to the chart legend; traders and the max cap
 * of a launch are in «О токене».
 */
function PhoneStrip({ info, loading }: { info: TokenInfo | undefined; loading: boolean }) {
    const { t } = useTranslation();
    const lang = useLang();
    if (loading) return <div className="skeleton -mx-4 mt-1 h-[54px] !rounded-none" aria-hidden="true" />;
    const l = info?.launch;
    // Sums in the strip without cents ($216, not $215.85): a quarter of the width has no room for them.
    const sum = (v: number | null | undefined) => (v != null && v >= 10 && v < 1000 ? `$${Math.round(v)}` : usd(v, lang));
    const cells: { label: string; value: string; tone?: "up" | "down" | "flat" }[] = [
        l
            ? { label: t("token.cap"), value: sum(l.cap_usd) }
            : { label: t("token.price"), value: usd(info?.price_usd, lang) },
        { label: t("period.24h"), value: change(info?.change_24h, lang), tone: trendOf(info?.change_24h) },
        l
            ? { label: t("token.fromStartTitle"), value: mult(l.x, lang), tone: l.x != null ? multTrend(l.x) : undefined }
            : { label: t("token.traders24h"), value: count(info?.traders_24h, lang) },
        { label: t("token.volume24h"), value: info ? sum(info.volume_24h_usd) : "—" },
    ];
    return (
        <dl className="-mx-4 mt-1 grid grid-cols-4 border-y border-surface-2">
            {cells.map((c, i) => (
                <div key={c.label} className={cn("min-w-0 py-2", i === 0 ? "pr-1 pl-4" : "border-l border-surface-2 px-3")}>
                    <dt className="truncate text-[11px] text-fg-3">{c.label}</dt>
                    <dd
                        className={cn(
                            "mt-0.5 truncate leading-tight font-semibold tabular-nums",
                            i === 0 ? "font-display text-[18px]" : "text-[15px]",
                            c.tone === "up" && "text-up-fg",
                            c.tone === "down" && "text-down-fg",
                        )}
                    >
                        {c.value}
                    </dd>
                </div>
            ))}
        </dl>
    );
}

/** The highest cap of a launch: today's cap scaled by its best multiple over today's. */
function maxCapOf(l: Launch | null | undefined): number | null {
    return l && l.cap_usd !== null && l.x && l.x_max ? (l.cap_usd * l.x_max) / l.x : null;
}

/** Phone «О токене»: what the one-row header left out — name, contract, explorer, traders, max cap. */
function AboutSection({ id, info }: { id: string; info: TokenInfo | undefined }) {
    const { t } = useTranslation();
    const lang = useLang();
    const name = clean(info?.name, 60);
    const l = info?.launch;
    const maxCap = maxCapOf(l);
    const row = "flex min-h-11 items-center gap-3 border-b border-row-line last:border-0";
    const dt = "w-28 shrink-0 text-[12px] text-fg-3";
    return (
        <Widget title={t("token.about")} state="ready" live={false}>
            <dl className="px-4 py-1 text-[13px]">
                {name && (
                    <div className={row}>
                        <dt className={dt}>{t("token.name")}</dt>
                        <dd className="min-w-0 truncate">{name}</dd>
                    </div>
                )}
                <div className={row}>
                    <dt className={dt}>{t("token.contract")}</dt>
                    <dd className="flex min-w-0 flex-1 items-center gap-1">
                        <span className="min-w-0 truncate font-mono text-[12px] text-fg-2" translate="no">{id}</span>
                        <CopyButton value={id} label={t("token.copyAddress")} />
                        <a href={explorerAccountUrl(id)} target="_blank" rel={EXTERNAL_REL} className="inline-flex min-h-11 shrink-0 items-center text-[12px] text-brand-text">
                            {t("token.explorer")}
                        </a>
                    </dd>
                </div>
                <div className={row}>
                    <dt className={dt}>{t("token.traders24h")}</dt>
                    <dd className="tabular-nums">
                        {count(info?.traders_24h, lang)}
                        {info && <span className="text-fg-3"> · {t("token.trades", { n: count(info.trades_24h, lang) })}</span>}
                    </dd>
                </div>
                {l && (
                    <div className={row}>
                        <dt className={dt}>{t("token.cap")}</dt>
                        <dd className="tabular-nums">
                            {usd(l.cap_usd, lang)}
                            {maxCap !== null && <span className="text-fg-3"> · {t("token.maxCap", { cap: usd(maxCap, lang) })}</span>}
                        </dd>
                    </div>
                )}
            </dl>
        </Widget>
    );
}

// --- stat tiles --------------------------------------------------------------------------------

function Stats({ info, loading }: { info: TokenInfo | undefined; loading: boolean }) {
    const { t } = useTranslation();
    const lang = useLang();
    if (loading) return <TileSkeletons n={5} className="mt-5 grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5" />;
    const l = info?.launch;
    const maxCap = maxCapOf(l);
    return (
        <div className="mt-5 grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
            <Tile
                label={t("token.price")}
                value={info?.price_near != null ? `${price(info.price_near, lang)} Ⓝ` : "—"}
                sub={info?.price_usd != null ? usd(info.price_usd, lang) : undefined}
            />
            <Tile
                label={t("period.24h")}
                value={change(info?.change_24h, lang)}
                tone={trendOf(info?.change_24h)}
                sub={l?.x != null ? t("token.fromStart", { x: mult(l.x, lang) }) : undefined}
            />
            <Tile
                label={t("token.cap")}
                value={l?.cap_usd != null ? usd(l.cap_usd, lang) : "—"}
                sub={maxCap !== null ? t("token.maxCap", { cap: usd(maxCap, lang) }) : l ? undefined : t("token.capOnlyLaunches")}
            />
            <Tile
                label={t("token.volume24h")}
                value={info ? usd(info.volume_24h_usd, lang) : "—"}
                sub={info ? near(info.volume_24h_near, lang) : undefined}
            />
            <Tile
                label={t("token.traders24h")}
                value={count(info?.traders_24h, lang)}
                sub={info ? t("token.trades", { n: count(info.trades_24h, lang) }) : undefined}
            />
        </div>
    );
}

// --- chart -------------------------------------------------------------------------------------

function ChartSection({ id, info, nearUsd, creator }: { id: string; info: TokenInfo | undefined; nearUsd: number | null; creator: string | null }) {
    const { t } = useTranslation();
    const lang = useLang();
    const phone = !useMediaQuery("(min-width: 768px)");
    // 1m by default, like the launchpads: a fresh token has a handful of trades, and an hour bar hides them.
    // Market cap by default where the supply is known, as on the launchpads.
    const [prefs, setPrefs] = useWidgetSettings<ChartPrefs>("token:chart", { i: "1m", unit: "near", mode: "auto", axis: "cap" });
    const mode = prefs.mode === "auto" ? (phone ? "line" : "candles") : prefs.mode;
    const unit: Unit = prefs.unit === "usd" && nearUsd ? "usd" : "near";
    // The API has no supply; a Nearly / JustHoot launch carries its cap and price (supply 1e9), others have none.
    const launch = info?.launch ?? null;
    const supply = launch?.cap_near && launch.price_near ? Math.round(launch.cap_near / launch.price_near) : null;
    const cap = prefs.axis === "cap" && supply !== null;
    const q = useQuery({ ...candlesQuery(id, prefs.i, 500), refetchInterval: usePollWhenOffline(10_000) });
    const trades = useQuery(tradesQuery(id, 100));
    const top = useQuery(smartTopQuery(200));
    const decimals = q.data?.decimals ?? info?.decimals ?? null;
    const k = unit === "usd" && nearUsd ? nearUsd : 1;
    const ks = cap && supply !== null ? k * supply : k;

    const bars = React.useMemo<Bar[]>(() => {
        if (decimals === null || !q.data) return [];
        const out: Bar[] = [];
        for (const c of q.data.candles) {
            const o = perToken(c.o, decimals), h = perToken(c.h, decimals), l = perToken(c.l, decimals), cl = perToken(c.c, decimals);
            const time = Math.floor(Date.parse(c.t) / 1000);
            if (o === null || h === null || l === null || cl === null || !Number.isFinite(time)) continue;
            out.push({ time, open: o * ks, high: h * ks, low: l * ks, close: cl * ks, volume: unit === "usd" ? c.v_usd ?? c.v * k : c.v });
        }
        return out;
    }, [q.data, decimals, k, ks, unit]);

    const marks = React.useMemo<Mark[]>(() => {
        const smart = new Set((top.data ?? []).map((w) => w._id));
        const out: Mark[] = [];
        for (const tr of trades.data ?? []) {
            const tags = tagsOf(tr, creator, smart);
            const time = Math.floor(Date.parse(bucketStart(tr.ts, prefs.i)) / 1000);
            if (tags.creator && tr.side === "sell") out.push({ time, kind: "creatorSell" });
            else if (tags.smart && tr.side === "buy") out.push({ time, kind: "smartBuy" });
        }
        return out;
    }, [trades.data, top.data, creator, prefs.i]);

    const fmtPrice = React.useCallback((v: number, sig?: number) => {
        // Cap: "$5.17k" / "1.421k" — fixed digits in k/M units, so axis labels a step apart differ.
        if (cap) return (unit === "usd" ? "$" : "") + axisValue(v, lang, sig ?? 3);
        return unit === "usd" ? usd(v, lang, sig) : price(v, lang, sig);
    }, [cap, unit, lang]);
    // A bar's move is often a fraction of a percent: two decimals under 1%.
    const fmtChange = React.useCallback((f: number) => {
        const p = Math.abs(f * 100);
        const body = p < 1 ? p.toFixed(2) : p < 10 ? p.toFixed(1) : Math.round(p).toString();
        return `${f > 0 ? "+" : f < 0 ? "−" : ""}${body}%`;
    }, []);
    const fmtVol = React.useCallback((v: number) => (unit === "usd" ? usd(v, lang) : near(v, lang)), [unit, lang]);
    const summary = bars.length
        ? t(cap ? "token.chartSummaryCap" : "token.chartSummary", {
            first: fmtPrice(bars[0].close), last: fmtPrice(bars[bars.length - 1].close),
            low: fmtPrice(Math.min(...bars.map((b) => b.low))), high: fmtPrice(Math.max(...bars.map((b) => b.high))),
        })
        : t("token.noTrades");

    const intervals = (
        <Segmented
            label={t("token.interval")}
            value={prefs.i}
            onChange={(i) => setPrefs({ i })}
            options={[
                { value: "1m", label: t("token.i1m") },
                { value: "1h", label: t("token.i1h") },
                { value: "1d", label: t("token.i1d") },
            ]}
        />
    );
    const unitOptions = [{ value: "near" as const, label: "Ⓝ" }, ...(nearUsd ? [{ value: "usd" as const, label: "$" }] : [])];
    const modeButton = (
        <button
            type="button"
            onClick={() => setPrefs({ mode: mode === "candles" ? "line" : "candles" })}
            className="grid size-8 shrink-0 place-items-center rounded-lg text-fg-2 hover:bg-surface-2 hover:text-fg max-md:size-11"
            aria-label={mode === "candles" ? t("token.showLine") : t("token.showCandles")}
        >
            {mode === "candles" ? <LineChart className="size-4" aria-hidden="true" /> : <CandlestickChart className="size-4" aria-hidden="true" />}
        </button>
    );
    const widget = (
        <Widget
            title={t(cap ? "token.cap" : "token.price")}
            freshness={<Freshness updatedAt={q.dataUpdatedAt} />}
            // Phone: no title, one row — intervals, then «Капа · $ ▾» (axis and unit) and line / candles.
            className={phone ? "-mx-4 bg-transparent" : undefined}
            filters={
                phone ? intervals : (
                    <>
                        {supply !== null && (
                            <Segmented
                                label={t("token.axis")}
                                value={cap ? "cap" : "price"}
                                onChange={(axis) => setPrefs({ axis })}
                                options={[
                                    { value: "cap", label: t("token.axisCap") },
                                    { value: "price", label: t("token.axisPrice") },
                                ]}
                            />
                        )}
                        {intervals}
                        <Segmented label={t("token.unit")} value={unit} onChange={(u) => setPrefs({ unit: u })} options={unitOptions} />
                        {modeButton}
                    </>
                )
            }
            settings={
                phone ? (
                    <div className="flex shrink-0 items-center">
                        <DropdownMenu>
                            <DropdownMenuTrigger
                                className="inline-flex h-11 items-center gap-1 rounded-lg px-2.5 text-[13px] font-semibold whitespace-nowrap text-fg hover:bg-surface-2"
                                aria-label={t("token.chartView")}
                            >
                                {`${cap ? t("token.axisCap") : t("token.axisPrice")} · ${unit === "usd" ? "$" : "Ⓝ"}`}
                                <ChevronDown className="size-3.5 text-fg-3" aria-hidden="true" />
                            </DropdownMenuTrigger>
                            <DropdownMenuContent align="end" className="min-w-44">
                                {supply !== null && (
                                    <>
                                        <DropdownMenuLabel>{t("token.axis")}</DropdownMenuLabel>
                                        <DropdownMenuRadioGroup value={cap ? "cap" : "price"} onValueChange={(v) => setPrefs({ axis: v as "cap" | "price" })}>
                                            <DropdownMenuRadioItem value="cap" className="min-h-11">{t("token.axisCap")}</DropdownMenuRadioItem>
                                            <DropdownMenuRadioItem value="price" className="min-h-11">{t("token.axisPrice")}</DropdownMenuRadioItem>
                                        </DropdownMenuRadioGroup>
                                        <DropdownMenuSeparator />
                                    </>
                                )}
                                <DropdownMenuLabel>{t("token.unit")}</DropdownMenuLabel>
                                <DropdownMenuRadioGroup value={unit} onValueChange={(v) => setPrefs({ unit: v as Unit })}>
                                    {unitOptions.map((o) => (
                                        <DropdownMenuRadioItem key={o.value} value={o.value} className="min-h-11">{o.label}</DropdownMenuRadioItem>
                                    ))}
                                </DropdownMenuRadioGroup>
                                {/* On the phone the note lives here, not as a second legend line. */}
                                {nearUsd ? <p className="max-w-52 px-2 pt-1 pb-1.5 text-[11px] text-fg-3">{t("token.usdNote")}</p> : null}
                            </DropdownMenuContent>
                        </DropdownMenu>
                        {modeButton}
                    </div>
                ) : undefined
            }
            state={q.isPending ? "loading" : q.isError && !q.data ? "error" : bars.length ? "ready" : "empty"}
            error={q.error}
            onRetry={() => void q.refetch()}
            empty={{ title: t("token.noTrades"), hint: t("token.noTradesHint") }}
            skeletonRows={5}
        >
            <div className="flex flex-wrap gap-x-4 gap-y-1 px-4 pt-2.5 text-[12px] text-fg-2">
                <span className="inline-flex items-center gap-1.5"><i className="size-2 bg-amber" aria-hidden="true" />{t("token.markCreator")}</span>
                <span className="inline-flex items-center gap-1.5"><i className="size-2 rounded-full bg-brand" aria-hidden="true" />{t("token.markSmart")}</span>
                {unit === "usd" && !phone && <span className="text-fg-3">{t("token.usdNote")}</span>}
                {/* Phone: the strip above shows the cap, so the price in Ⓝ sits here. */}
                {phone && info?.price_near != null && <span className="ml-auto text-fg-2 tabular-nums">{`${price(info.price_near, lang)} Ⓝ`}</span>}
            </div>
            <div className="px-2 pt-1 pb-3">
                <PriceChart
                    bars={bars}
                    marks={marks}
                    mode={mode}
                    intraday={prefs.i !== "1d"}
                    formatPrice={fmtPrice}
                    formatVolume={fmtVol}
                    formatChange={fmtChange}
                    summary={summary}
                    height={phone ? 320 : 380}
                    visibleBars={phone ? 36 : 60}
                />
            </div>
        </Widget>
    );
    return phone ? <PhoneFrame value={true}>{widget}</PhoneFrame> : widget;
}

// --- trades ------------------------------------------------------------------------------------

/** Tags of a trade: from the API when present; socket rows get them from what the page knows. */
function tagsOf(tr: Trade, creator: string | null, smart: Set<string> | Map<string, SmartWallet>): { smart: boolean; creator: boolean } {
    const tags = tr.tags ?? [];
    return {
        smart: tags.includes("smart") || smart.has(tr.wallet),
        creator: tags.includes("creator") || (!!creator && (tr.wallet === creator || tr.signer === creator)),
    };
}

type TradeFilter = "all" | "big" | "smart" | "creator";
/**
 * Wide (≥640px of the widget): a table — age, side, Ⓝ, $, tokens, price, wallet, and the venue
 * only when the list has more than one (otherwise it is named once, in the footer).
 * Narrow: two lines per trade, nothing hidden — side + Ⓝ + $ / wallet on the left, age /
 * tokens @ price on the right.
 */
const TRADE_WIDE = "@min-[640px]:grid-cols-[72px_66px_60px_64px_68px_76px_minmax(0,1fr)]";
const TRADE_WIDE_VENUE = "@min-[640px]:grid-cols-[72px_66px_60px_64px_68px_76px_minmax(0,1fr)_76px]";
const WIDE = "hidden @min-[640px]:block";

function TradesSection({ id, info, creator }: { id: string; info: TokenInfo | undefined; creator: string | null }) {
    const { t } = useTranslation();
    const lang = useLang();
    const [filter, setFilter] = React.useState<TradeFilter>("all");
    const q = useQuery({ ...tradesQuery(id, 100), refetchInterval: usePollWhenOffline(5_000) });
    const top = useQuery(smartTopQuery(200));
    const smart = React.useMemo(() => new Map((top.data ?? []).map((w) => [w._id, w])), [top.data]);
    const decimals = info?.decimals ?? null;
    const rows = (q.data ?? []).filter((tr) => {
        if (filter === "big") return tr.near >= 1;
        const tags = tagsOf(tr, creator, smart);
        return filter === "smart" ? tags.smart : filter === "creator" ? tags.creator : true;
    });

    const touch = useMediaQuery("(hover: none)");
    const venues = new Set(rows.map((tr) => tr.venue).filter(Boolean));
    const oneVenue = venues.size === 1 ? [...venues][0] : null;
    const cols = venues.size > 1 ? TRADE_WIDE_VENUE : TRADE_WIDE;

    const renderRow = (tr: Trade) => {
        const tags = tagsOf(tr, creator, smart);
        const trend = tr.side === "buy" ? "up" : "down";
        const side = tr.side === "buy" ? t("token.buy") : t("token.sell");
        const dec = tr.decimals ?? decimals;
        const tokens = tokenAmount(tr.tok, dec, lang);
        const px = price(perToken(tr.px, dec), lang);
        const wallet = (
            <span className="flex min-w-0 items-center gap-1.5">
                <AccountCell id={tr.wallet} label={tr.wallet_label} link={!touch} className={INNER} />
                {tags.smart && <SmartTag rank={smart.get(tr.wallet)?.rank ?? 0} />}
                {tags.creator && <CreatorTag />}
            </span>
        );
        return (
            <Row
                href={tr.tx ? txUrl(tr.tx) : undefined}
                label={t("token.tradeLabel")}
                className={cn("h-[54px] grid-cols-[minmax(0,1fr)_auto] @min-[640px]:h-11", cols)}
                // Touch: the wallet is too small to tap inside the row — the tap panel offers it.
                actions={touch ? (
                    <PanelLinks
                        links={[{
                            label: <>{t("token.colWallet")} <span className="truncate font-mono font-normal" translate="no">{shortAccount(tr.wallet)}</span></>,
                            to: accountPath(tr.wallet),
                        }]}
                    />
                ) : undefined}
            >
                {/* narrow: two lines */}
                <div className="flex min-w-0 flex-col gap-1 @min-[640px]:hidden">
                    <span className="flex items-baseline gap-2">
                        <Num trend={trend} strong>{side}</Num>
                        <Num trend={trend} strong>{near(tr.near, lang)}</Num>
                        {tr.usd !== null && <Num className="text-[12px] text-fg-3">{usd(tr.usd, lang)}</Num>}
                    </span>
                    {wallet}
                </div>
                <div className="flex flex-col items-end gap-1 @min-[640px]:hidden">
                    <Age at={tr.ts} className="text-[12px] text-fg-3" />
                    <Num className="text-[11px] text-fg-3">{`${tokens} @ ${px}`}</Num>
                </div>
                {/* wide: the table columns */}
                <Age at={tr.ts} className={cn(WIDE, "text-fg-3")} />
                <Num trend={trend} strong className={WIDE}>{side}</Num>
                <Num trend={trend} strong className={cn(WIDE, "text-right")}>{near(tr.near, lang, { unit: false })}</Num>
                <Num className={cn(WIDE, "text-right text-fg-3")}>{usd(tr.usd, lang)}</Num>
                <Num className={cn(WIDE, "text-right text-fg-2")}>{tokens}</Num>
                <Num className={cn(WIDE, "text-right")}>{px}</Num>
                <span className={cn(WIDE, "min-w-0")}>{wallet}</span>
                {venues.size > 1 && (
                    <span className={cn(WIDE, "truncate text-[12px] text-fg-3")}>{tr.venue ? venueName(tr.venue) : "—"}</span>
                )}
            </Row>
        );
    };

    return (
        <Widget
            title={t("token.tradesTitle")}
            freshness={<Freshness updatedAt={q.dataUpdatedAt} />}
            filters={
                <Segmented
                    label={t("token.tradesFilter")}
                    value={filter}
                    onChange={setFilter}
                    options={[
                        { value: "all", label: t("actions.all") },
                        { value: "big", label: t("token.from1") },
                        { value: "smart", label: t("token.smartOnly") },
                        ...(creator ? [{ value: "creator" as const, label: t("token.creator") }] : []),
                    ]}
                />
            }
            state={q.isPending ? "loading" : q.isError && !q.data ? "error" : rows.length ? "ready" : "empty"}
            error={q.error}
            onRetry={() => void q.refetch()}
            empty={{
                title: filter === "all" ? t("token.noTrades") : t("token.noTradesFiltered"),
                hint: filter === "all" ? t("token.tradesKept") : undefined,
                action: filter !== "all" ? { label: t("token.showAllTrades"), onClick: () => setFilter("all") } : undefined,
            }}
            footer={{ hint: oneVenue ? `${t("token.tradesKept")} · ${venueName(oneVenue)}` : t("token.tradesKept") }}
            height={520}
            skeletonRows={8}
        >
            <div
                className={cn("hidden h-[34px] shrink-0 items-center gap-3 border-b border-row-line px-4 text-[11px] font-medium tracking-[0.05em] text-fg-3 uppercase @min-[640px]:grid", cols)}
                aria-hidden="true"
            >
                <span>{t("token.colTime")}</span>
                <span>{t("token.colSide")}</span>
                <span className="text-right">Ⓝ</span>
                <span className="text-right">$</span>
                <span className="text-right">{t("token.colTokens")}</span>
                <span className="text-right">{t("token.colPrice")}</span>
                <span>{t("token.colWallet")}</span>
                {venues.size > 1 && <span>{t("token.colVenue")}</span>}
            </div>
            <LiveList items={rows} getKey={(r) => r._id} renderRow={renderRow} />
        </Widget>
    );
}

// --- creator -----------------------------------------------------------------------------------

function CreatorSection({ account, launch }: { account: string; launch: Launch }) {
    const { t } = useTranslation();
    const lang = useLang();
    const q = useQuery(creatorQuery(account));
    const c: Creator | undefined = q.data;
    const here = c?.history.find((h) => h.token === launch.token);
    const past = (c?.history ?? []).filter((h) => h.token !== launch.token).slice(0, 5);
    const fact = creatorFact(launch.creator_facts);
    return (
        <Widget
            title={t("token.creator")}
            filters={<Link to={accountPath(account)} className="pr-2 text-[12px] text-brand-text hover:underline">{t("token.allLaunches")}</Link>}
            state={q.isPending ? "loading" : q.isError && !c ? "error" : "ready"}
            error={q.error}
            onRetry={() => void q.refetch()}
            live={false}
            skeletonRows={4}
        >
            <div className="px-4 py-3.5">
                <div className="flex items-center gap-3">
                    <div className="min-w-0 flex-1">
                        <AccountCell id={account} className="text-[14px] [&_span]:!text-[14px]" />
                        <div className="text-[12px] text-fg-3">
                            {t("token.creatorLaunches", { count: c?.launches ?? launch.creator_facts?.launches ?? 1 })}
                            {fact && fact.tone !== "neutral" && <> · <span className={fact.tone === "down" ? "text-down-fg" : "text-amber-fg"}>{t(fact.key, fact.values)}</span></>}
                        </div>
                    </div>
                    <WalletActions account={account} code={c?.bot_code} />
                </div>
                <div className="mt-3 border-t border-line-soft pt-1">
                    {here && here.sold_share !== null && here.sold_share > 0 ? (
                        <Fact mark="◆" tone="amber">
                            <b className="font-semibold">{t("token.inThisLaunch")}</b>{" "}
                            {t("token.soldShare", { pct: Math.round(here.sold_share * 100) })}
                            {here.first_sell_s !== null && <> {t("token.firstSellAfter", { age: age(here.first_sell_s, lang) })}</>}
                            {here.sold_near ? <> — {near(here.sold_near, lang)}</> : null}
                        </Fact>
                    ) : here ? (
                        <Fact mark="✓" tone="up"><b className="font-semibold">{t("token.inThisLaunch")}</b> {t("token.notSold")}</Fact>
                    ) : null}
                    {c && (c.links.length > 0 || launch.creator_facts?.linked) ? (
                        <Fact mark="!" tone="down">{t("token.linked", { count: Math.max(1, c.links.length) })}</Fact>
                    ) : c ? (
                        <Fact mark="✓" tone="up">{t("token.notLinked")}</Fact>
                    ) : null}
                    {c && (
                        <Fact mark="#" tone={c.blocked_by > 0 ? "down" : "neutral"}>{t("token.blockedBy", { count: c.blocked_by })}</Fact>
                    )}
                    {c && c.launches > 1 && (
                        <Fact mark="▼" tone={c.dumped > 0 ? "amber" : "neutral"}>{t("token.dumpedOf", { dumped: c.dumped, launches: c.launches })}</Fact>
                    )}
                </div>
                {past.length > 0 && (
                    <>
                        <div className="mt-3 mb-1.5 text-[11px] text-fg-3">{t("token.pastLaunches")}</div>
                        {past.map((h) => (
                            <Link key={h.token} to={`/token/${encodeURIComponent(h.token)}`} className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-3 rounded-md py-1.5 text-[12px] hover:bg-inset">
                                <span className="truncate">
                                    <b className="font-semibold">{`$${tickerOf(c?.recent.find((r) => r.token === h.token)?.symbol, h.token)}`}</b>{" "}
                                    <span className="text-fg-3">· <Age at={h.started} /></span>
                                </span>
                                <span className={h.dumped ? "text-amber-fg" : "text-fg-2"}>
                                    {h.sold_share ? t("token.soldShare", { pct: Math.round(h.sold_share * 100) }) : t("token.notSold")}
                                    {h.sold_share && h.first_sell_s !== null ? ` ${t("token.firstSellAfter", { age: age(h.first_sell_s, lang) })}` : ""}
                                </span>
                            </Link>
                        ))}
                    </>
                )}
            </div>
        </Widget>
    );
}

// --- smart money in the token ------------------------------------------------------------------

const TREND_TEXT = { up: "text-up-fg", down: "text-down-fg", flat: "" } as const;

/**
 * Do the smart wallets sit in the token, and at what price did they get in. The main row is their
 * position (bought / still held / price vs their entry — how late a buy here is); the 24h flow is a
 * line under it, without a green «+» that read as profit.
 */
function SmartSection({ info }: { info: TokenInfo | undefined }) {
    const { t } = useTranslation();
    const lang = useLang();
    const s = info?.smart_24h;
    const p = info?.smart_pos ?? null;
    // Price vs entry follows the live price of the header; the API's x is the fallback.
    const x = p?.entry_near && info?.price_near ? info.price_near / p.entry_near : (p?.x ?? null);
    const held = p ? Math.round(p.held * 100) : null;
    const value = "font-display text-[18px] font-semibold tabular-nums";
    const caption = "text-[11px] text-fg-3";
    return (
        <Widget title={t("token.smartTitle")} state={info ? "ready" : "loading"} live={false} skeletonRows={1}>
            {p && (
                <div className="grid grid-cols-3 gap-3 px-4 pt-3.5">
                    <div>
                        <div className={value}>{count(p.wallets, lang)}</div>
                        <div className={caption}>{t("token.smartBought", { count: p.wallets })}</div>
                        <div className="text-[11px] text-fg-2">{t("token.smartHolding", { hold: count(p.holding, lang), out: count(p.wallets - p.holding, lang) })}</div>
                    </div>
                    <div>
                        <div className={cn(value, held !== null && held < 50 ? "text-down-fg" : "")}>{held === null ? "—" : `${held}%`}</div>
                        <div className={caption}>{t("token.smartHeld")}</div>
                    </div>
                    <div title={p.entry_near !== null ? t("token.smartEntryHint", { price: `${price(p.entry_near, lang)} Ⓝ` }) : undefined}>
                        <div className={cn(value, TREND_TEXT[multTrend(x)])}>{mult(x, lang)}</div>
                        <div className={caption}>{t("token.smartVsEntry")}</div>
                    </div>
                </div>
            )}
            <div className={cn("px-4 pb-3.5 text-[12px] text-fg-2", p ? "pt-2.5" : "pt-3.5")}>
                {s && s.wallets > 0
                    ? t("token.smart24h", { count: s.wallets, buy: near(s.buy, lang), sell: near(s.sell, lang) })
                    : p ? null : t("token.smartNone")}
            </div>
        </Widget>
    );
}
