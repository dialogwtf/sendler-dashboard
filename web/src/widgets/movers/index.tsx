import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useTokenMeta } from "@/api/batch";
import { moversQuery } from "@/api/queries";
import type { Mover } from "@/api/types";
import { count, mult, multTrend, usd } from "@/lib/format";
import { tickerOf } from "@/lib/text";
import { useLang } from "@/lib/useLang";
import { Sparkline } from "@/ui/AreaChart";
import { Freshness } from "@/ui/Freshness";
import { Row, TokenActions } from "@/ui/Row";
import { Segmented } from "@/ui/Segmented";
import { TokenIcon } from "@/components/common/TokenIcon";
import { tokenPath } from "@/ui/TokenCell";
import { cn } from "@/lib/utils";
import { Widget } from "@/ui/Widget";
import type { WidgetDef, WidgetProps } from "../types";

type Period = "10m" | "1h" | "24h";
interface Settings extends Record<string, unknown> {
    period: Period;
}

/** No channel: the API caches 10 s. */
const POLL_MS = 10_000;

function MoverRow({ r, n, period }: { r: Mover; n: number; period: Period }) {
    const { t } = useTranslation();
    const lang = useLang();
    const meta = useTokenMeta(r.token);
    const symbol = r.symbol ?? meta.data?.symbol;
    const trend = multTrend(r.x_period);
    return (
        <Row
            to={tokenPath(r.token)}
            label={`$${tickerOf(symbol, r.token)}`}
            className="h-[60px] grid-cols-[18px_minmax(0,1fr)_64px] @sm:grid-cols-[18px_minmax(0,1fr)_76px_64px]"
            actions={<TokenActions token={r.token} code={meta.data?.bot_code} />}
        >
            <span className="font-semibold text-fg-3 tabular-nums">{n}</span>
            <div className="flex min-w-0 items-center gap-2.5">
                <TokenIcon symbol={tickerOf(symbol, r.token)} icon={meta.data?.icon} seed={r.token} size={28} />
                <div className="min-w-0">
                    <div className="truncate font-semibold">{`$${tickerOf(symbol, r.token)}`}</div>
                    <div className="truncate text-[11px] whitespace-nowrap text-fg-3">
                        {r.cap_usd !== null ? usd(r.cap_usd, lang) : "—"} · {t("movers.buys", { count: r.buys_period, n: count(r.buys_period, lang) })}
                    </div>
                </div>
            </div>
            <span className="hidden @sm:block">
                <Sparkline values={r.spark ?? []} label={t("movers.sparkLabel", { period: t(`period.${period}`) })} />
            </span>
            <div className="text-right">
                <div className={cn("font-display text-[15px] font-bold tabular-nums", trend === "flat" ? "text-fg-2" : trend === "down" ? "text-down-fg" : "text-up-fg")}>
                    {mult(r.x_period, lang)}
                </div>
                <div className="text-[11px] whitespace-nowrap text-fg-3">{usd(r.volume_period_usd, lang)}</div>
            </div>
        </Row>
    );
}

function MoversWidget({ settings, setSettings, height }: WidgetProps<Settings>) {
    const { t } = useTranslation();
    const q = useQuery({ ...moversQuery(settings.period, 20), refetchInterval: POLL_MS });
    const rows = q.data ?? [];
    return (
        <Widget
            title={t("widgets.movers.title")}
            freshness={<Freshness updatedAt={q.dataUpdatedAt} polled pollMs={POLL_MS} />}
            filters={
                <Segmented
                    label={t("net.period")}
                    value={settings.period}
                    onChange={(p) => setSettings({ period: p })}
                    options={[
                        { value: "10m", label: t("period.10m") },
                        { value: "1h", label: t("period.1h") },
                        { value: "24h", label: t("period.24h") },
                    ]}
                />
            }
            live={false}
            state={q.isPending ? "loading" : q.isError && !q.data ? "error" : rows.length ? "ready" : "empty"}
            error={q.error}
            onRetry={() => void q.refetch()}
            empty={{
                title: t("movers.empty", { period: t(`period.${settings.period}`) }),
                hint: t("movers.emptyHint"),
                action: settings.period !== "24h" ? { label: t("movers.wider"), onClick: () => setSettings({ period: "24h" }) } : undefined,
            }}
            footer={{ hint: t("movers.footer") }}
            height={height}
            skeletonRows={8}
        >
            <div className="min-h-0 flex-1 overflow-y-auto">
                {rows.map((r, i) => <MoverRow key={r.token} r={r} n={i + 1} period={settings.period} />)}
            </div>
        </Widget>
    );
}

export const moversWidget: WidgetDef<Settings> = {
    type: "movers",
    title: "widgets.movers.title",
    size: { default: "M", min: "S" },
    settings: [],
    defaults: { period: "24h" },
    channels: () => [],
    Component: MoversWidget,
};
