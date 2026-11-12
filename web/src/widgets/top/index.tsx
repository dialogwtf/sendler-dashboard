import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useTokenMeta } from "@/api/batch";
import { topQuery } from "@/api/queries";
import type { TopSort, TopToken } from "@/api/types";
import { count, price, ratioChange, trendOf, usd } from "@/lib/format";
import { tickerOf } from "@/lib/text";
import { useLang } from "@/lib/useLang";
import { Freshness } from "@/ui/Freshness";
import { Num } from "@/ui/Num";
import { Row, TokenActions } from "@/ui/Row";
import { Segmented } from "@/ui/Segmented";
import { TokenCell, tokenPath } from "@/ui/TokenCell";
import { Widget } from "@/ui/Widget";
import type { WidgetDef, WidgetProps } from "../types";

type Period = "1h" | "24h" | "7d";
interface Settings extends Record<string, unknown> {
    period: Period;
    sort: TopSort;
}

/** No channel: the API caches 30 s, so the widget polls at that pace. */
const POLL_MS = 30_000;
const COLS = "grid-cols-[20px_minmax(0,1.3fr)_minmax(0,1fr)_70px] @lg:grid-cols-[20px_minmax(0,1.3fr)_minmax(0,1fr)_70px_80px_70px]";

function TopRow({ r, n, period }: { r: TopToken; n: number; period: Period }) {
    const { t } = useTranslation();
    const lang = useLang();
    const meta = useTokenMeta(r.token);
    const ticker = tickerOf(r.symbol ?? meta.data?.symbol, r.token);
    const ch = r.growth === null ? null : r.growth - 1;
    return (
        <Row
            to={tokenPath(r.token)}
            label={`$${ticker}`}
            className={`h-[50px] ${COLS}`}
            actions={<TokenActions token={r.token} code={meta.data?.bot_code} />}
        >
            <span className="font-semibold text-fg-3 tabular-nums">{n}</span>
            <TokenCell token={r.token} symbol={r.symbol ?? meta.data?.symbol} icon={meta.data?.icon} />
            <Num className="text-right">{price(r.price_near, lang)}</Num>
            <Num trend={trendOf(ch)} strong className="text-right" title={t(`period.${period}`)}>
                {ratioChange(r.growth, lang)}
            </Num>
            <Num className="hidden text-right @lg:block">{r.volume_usd !== null ? usd(r.volume_usd, lang) : "—"}</Num>
            <Num className="hidden text-right text-fg-2 @lg:block">{count(r.traders, lang)}</Num>
        </Row>
    );
}

function TopWidget({ settings, setSettings, height }: WidgetProps<Settings>) {
    const { t } = useTranslation();
    const q = useQuery({ ...topQuery(settings.period, settings.sort, 20), refetchInterval: POLL_MS });
    const rows = q.data ?? [];
    return (
        <Widget
            title={t("widgets.top.title")}
            freshness={<Freshness updatedAt={q.dataUpdatedAt} polled pollMs={POLL_MS} />}
            filters={
                <>
                    <Segmented
                        label={t("top.sort")}
                        value={settings.sort}
                        onChange={(s) => setSettings({ sort: s })}
                        options={[
                            { value: "volume", label: t("top.byVolume") },
                            { value: "growth", label: t("top.byGrowth") },
                            { value: "trades", label: t("top.byTrades") },
                        ]}
                    />
                    <Segmented
                        label={t("net.period")}
                        value={settings.period}
                        onChange={(p) => setSettings({ period: p })}
                        options={[
                            { value: "1h", label: t("period.1h") },
                            { value: "24h", label: t("period.24h") },
                            { value: "7d", label: t("period.7d") },
                        ]}
                    />
                </>
            }
            live={false}
            state={q.isPending ? "loading" : q.isError && !q.data ? "error" : rows.length ? "ready" : "empty"}
            error={q.error}
            onRetry={() => void q.refetch()}
            empty={{ title: t("top.empty"), hint: t("top.emptyHint") }}
            height={height}
            skeletonRows={6}
        >
            <div
                className={`grid h-[34px] shrink-0 items-center gap-3 border-b border-row-line px-4 text-[11px] font-medium tracking-[0.05em] text-fg-3 uppercase ${COLS}`}
                aria-hidden="true"
            >
                <span>#</span>
                <span>{t("top.token")}</span>
                <span className="text-right">{t("top.price")}</span>
                <span className="text-right">{t(`period.${settings.period}`)}</span>
                <span className="hidden text-right @lg:block">{t("top.volume")}</span>
                <span className="hidden text-right @lg:block">{t("top.traders")}</span>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto">
                {rows.map((r, i) => <TopRow key={r.token} r={r} n={i + 1} period={settings.period} />)}
            </div>
        </Widget>
    );
}

export const topWidget: WidgetDef<Settings> = {
    type: "top",
    title: "widgets.top.title",
    size: { default: "M", min: "S" },
    settings: [],
    defaults: { period: "24h", sort: "volume" },
    channels: () => [],
    Component: TopWidget,
};
