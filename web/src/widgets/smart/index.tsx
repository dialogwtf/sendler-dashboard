import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { useTokenMeta } from "@/api/batch";
import { api } from "@/api/client";
import { useOlderRows } from "@/api/paging";
import { smartFeedQuery, smartTopQuery } from "@/api/queries";
import type { SmartTrade, SmartWallet } from "@/api/types";
import { useChannels, usePollWhenOffline } from "@/app/live";
import { TokenIcon } from "@/components/common/TokenIcon";
import { msOf, secondsSince } from "@/lib/clock";
import { count, near, signedPct } from "@/lib/format";
import { tickerOf } from "@/lib/text";
import { useLang } from "@/lib/useLang";
import { useNow } from "@/lib/useNow";
import { AccountCell, accountPath } from "@/ui/AccountCell";
import { Age } from "@/ui/Age";
import { Chip } from "@/ui/Chip";
import { Freshness } from "@/ui/Freshness";
import { LiveList } from "@/ui/LiveList";
import { MoreRows } from "@/ui/MoreRows";
import { Num } from "@/ui/Num";
import { INNER, Row, TokenActions, WalletActions } from "@/ui/Row";
import { Segmented } from "@/ui/Segmented";
import { tokenPath } from "@/ui/TokenCell";
import { Widget } from "@/ui/Widget";
import type { WidgetDef, WidgetProps } from "../types";

interface Settings extends Record<string, unknown> {
    tab: "trades" | "top";
    /** Smallest trade shown, NEAR. */
    minNear: number;
}

/** «N умных кошельков купили X за 30 мин» — from the feed, like the bot's smart-money alert. */
export const SIGNAL_WINDOW_S = 30 * 60;
export const SIGNAL_MIN_WALLETS = 2;
export const SIGNAL_MIN_NEAR = 0.3;

export function strongestSignal(feed: SmartTrade[], now: number): { token: string; symbol: string | null; wallets: number } | null {
    const byToken = new Map<string, { wallets: Set<string>; symbol: string | null }>();
    for (const tr of feed) {
        if (tr.side !== "buy" || tr.near < SIGNAL_MIN_NEAR) continue;
        const age = secondsSince(tr.ts, now);
        if (age === null || age > SIGNAL_WINDOW_S) continue;
        const e = byToken.get(tr.token) ?? { wallets: new Set<string>(), symbol: tr.symbol };
        e.wallets.add(tr.wallet);
        byToken.set(tr.token, e);
    }
    let best: { token: string; symbol: string | null; wallets: number } | null = null;
    for (const [token, e] of byToken) {
        if (e.wallets.size >= SIGNAL_MIN_WALLETS && (!best || e.wallets.size > best.wallets)) {
            best = { token, symbol: e.symbol, wallets: e.wallets.size };
        }
    }
    return best;
}

export function Signal({ feed }: { feed: SmartTrade[] }) {
    const { t } = useTranslation();
    const now = useNow();
    const s = strongestSignal(feed, now);
    const meta = useTokenMeta(s?.token);
    if (!s) return null;
    const ticker = tickerOf(s.symbol ?? meta.data?.symbol, s.token);
    return (
        <div className="mx-4 mt-3 mb-1 flex shrink-0 items-center gap-2.5 rounded-[10px] bg-brand-soft px-3 py-2.5">
            <TokenIcon symbol={ticker} icon={meta.data?.icon} seed={s.token} size={24} />
            <span className="min-w-0 flex-1 truncate">
                {t("smart.signal", { count: s.wallets })} <b className="font-semibold">{`$${ticker}`}</b> {t("smart.signalWindow")}
            </span>
            <Link to={tokenPath(s.token)} className="shrink-0 font-semibold text-brand-text hover:underline">
                {t("actions.open")}
            </Link>
        </div>
    );
}

function TradeRow({ r }: { r: SmartTrade }) {
    const { t } = useTranslation();
    const lang = useLang();
    const meta = useTokenMeta(r.token);
    const ticker = tickerOf(r.symbol ?? meta.data?.symbol, r.token);
    const buy = r.side === "buy";
    return (
        <Row
            to={tokenPath(r.token)}
            label={`${buy ? t("smart.bought") : t("smart.sold")} $${ticker}`}
            className="h-14 grid-cols-[minmax(0,1fr)_auto]"
            actions={<TokenActions token={r.token} code={meta.data?.bot_code} />}
        >
            <div className="min-w-0">
                <div className="hidden min-w-0 items-center gap-1.5 @sm:flex">
                    <AccountCell id={r.wallet} className={INNER} />
                    <Chip>{t("smart.rankRoi", { rank: r.smart.rank, roi: signedPct(r.smart.roi) })}</Chip>
                </div>
                <div className="mt-[3px] truncate text-[13px] @sm:text-[12px]">
                    <Num trend={buy ? "up" : "down"} strong>{buy ? t("smart.bought") : t("smart.sold")}</Num>{" "}
                    <b className="font-semibold">{`$${ticker}`}</b> <span className="text-fg-3">· <Age at={r.ts} /></span>
                </div>
            </div>
            <Num strong className="text-right">{near(r.near, lang)}</Num>
        </Row>
    );
}

function TopRow({ w }: { w: SmartWallet }) {
    const { t } = useTranslation();
    const lang = useLang();
    return (
        <Row
            to={accountPath(w._id)}
            label={w._id}
            className="h-14 grid-cols-[28px_minmax(0,1fr)_auto] @md:grid-cols-[28px_minmax(0,1fr)_84px_64px_56px]"
            actions={<WalletActions account={w._id} />}
        >
            <span className="font-semibold text-fg-3 tabular-nums">{w.rank}</span>
            <div className="min-w-0">
                <AccountCell id={w._id} link={false} />
                <div className="mt-[3px] truncate text-[11px] text-fg-3">
                    {t("smart.topLine", { tokens: count(w.tokens, lang), win: Math.round(w.win_rate * 100) })}
                </div>
            </div>
            <Num value={w.pnl} strong className="text-right">{`${w.pnl >= 0 ? "+" : ""}${near(w.pnl, lang)}`}</Num>
            <Num value={w.roi} className="hidden text-right @md:block">{signedPct(w.roi)}</Num>
            <span className="hidden text-right text-fg-2 tabular-nums @md:block">{`${Math.round(w.win_rate * 100)}%`}</span>
        </Row>
    );
}

const tradeKey = (r: SmartTrade) => r._id;
const tradeTs = (r: SmartTrade) => r.ts;

function SmartWidget({ settings, setSettings, height, full = false }: WidgetProps<Settings>) {
    const { t } = useTranslation();
    const top = useQuery(smartTopQuery(200));
    const limit = full ? 100 : 50;
    const feedQ = useQuery({ ...smartFeedQuery(limit), refetchInterval: usePollWhenOffline(5_000) });
    useChannels(settings.tab === "trades" ? ["trade"] : []);
    const isTop = settings.tab === "top";
    const older = useOlderRows({
        id: ["smart", "feed"],
        head: feedQ.data,
        headLimit: limit,
        enabled: full && !isTop,
        keyOf: tradeKey,
        tsOf: tradeTs,
        fetchPage: (before, n, signal) => api.smartFeed({ limit: n, before }, signal),
    });
    const q = isTop ? top : feedQ;
    const feed = older.rows.filter((r) => r.near >= settings.minNear);
    const topRows = (top.data ?? []).slice(0, full ? 200 : 50);
    const empty = isTop ? !topRows.length : !feed.length && !(full && older.hasMore);

    return (
        <Widget
            title={t("widgets.smart.title")}
            freshness={<Freshness updatedAt={q.dataUpdatedAt} polled={isTop} pollMs={60_000} />}
            filters={
                <Segmented
                    label={t("smart.view")}
                    value={settings.tab}
                    onChange={(v) => setSettings({ tab: v })}
                    options={[
                        { value: "trades", label: t("smart.trades") },
                        { value: "top", label: t("smart.top200") },
                    ]}
                />
            }
            live={!isTop}
            state={q.isPending ? "loading" : q.isError && !q.data ? "error" : empty ? "empty" : "ready"}
            error={q.error}
            onRetry={() => void q.refetch()}
            empty={{
                title: isTop ? t("smart.emptyTop") : t("smart.empty"),
                hint: isTop ? undefined : t("smart.emptyHint"),
                action: !isTop && settings.minNear > 0 ? { label: t("smart.anySize"), onClick: () => setSettings({ minNear: 0 }) } : undefined,
            }}
            footer={full ? undefined : { to: isTop ? "/smart?tab=top" : "/smart", label: isTop ? t("smart.allTop") : t("smart.all") }}
            expandTo={full ? undefined : "/smart"}
            height={height}
            skeletonRows={6}
        >
            {isTop ? (
                <div className={full ? undefined : "min-h-0 flex-1 overflow-y-auto"}>
                    {topRows.map((w) => <TopRow key={w._id} w={w} />)}
                    {full && <MoreRows older={{ hasMore: false, loading: false, error: null, loadMore: () => {} }} hint={t("smart.topHint")} />}
                </div>
            ) : (
                <>
                    <Signal feed={feed} />
                    <LiveList
                        items={feed}
                        getKey={tradeKey}
                        renderRow={(r) => <TradeRow r={r} />}
                        page={full}
                        footer={full ? <MoreRows older={older} /> : undefined}
                    />
                </>
            )}
        </Widget>
    );
}

function useSmartPulse(s: Settings): number[] {
    const trades = s.tab === "trades";
    useChannels(trades ? ["trade"] : []);
    useQuery(smartTopQuery(200));
    const q = useQuery(smartFeedQuery(50));
    if (!trades) return [];
    return (q.data ?? []).filter((r) => r.near >= s.minNear).map((r) => msOf(r.ts) ?? 0);
}

export const smartWidget: WidgetDef<Settings> = {
    type: "smart",
    title: "widgets.smart.title",
    size: { default: "M", min: "S" },
    settings: [],
    defaults: { tab: "trades", minNear: 0 },
    channels: (s) => (s.tab === "trades" ? ["trade"] : []),
    Component: SmartWidget,
    usePulse: useSmartPulse,
    fullList: "/smart",
    choices: { tab: ["trades", "top"] },
};
