import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Table2, LineChart } from "lucide-react";
import { networkQuery } from "@/api/queries";
import type { Network } from "@/api/types";
import { useChannels, useLiveState } from "@/app/live";
import { change, compact, count, trendOf } from "@/lib/format";
import { useLang } from "@/lib/useLang";
import { AreaChart, type Point } from "@/ui/AreaChart";
import { Freshness } from "@/ui/Freshness";
import { Num } from "@/ui/Num";
import { Segmented } from "@/ui/Segmented";
import { Stat } from "@/ui/Stat";
import { Widget } from "@/ui/Widget";
import type { WidgetDef, WidgetProps } from "../types";

type Period = "1h" | "24h" | "7d";
interface Settings extends Record<string, unknown> {
    period: Period;
}

const BUCKET_MS = { "1m": 60_000, "1h": 3_600_000 } as const;
/** 24 h / 7 d have no live channel of their own (the socket feeds the 1-minute series): poll. */
const SLOW_POLL_MS = 60_000;

/** Points for the chart: the bucket still filling right now would read as a cliff — left out. */
export function completePoints(data: Network | undefined, now = Date.now()): Point[] {
    if (!data) return [];
    const step = BUCKET_MS[data.interval];
    return data.series
        .map((p) => ({ t: Date.parse(p.t), v: p.txs }))
        .filter((p) => Number.isFinite(p.t) && p.t + step <= now);
}

/** Change of the last 5 complete minutes against the first 5 of the hour (1h period only). */
export function hourChange(points: Point[]): number | null {
    if (points.length < 20) return null;
    const avg = (xs: Point[]) => xs.reduce((s, p) => s + p.v, 0) / xs.length;
    const first = avg(points.slice(0, 5));
    return first > 0 ? avg(points.slice(-5)) / first - 1 : null;
}

function NetworkWidget({ settings, setSettings, height }: WidgetProps<Settings>) {
    const { t } = useTranslation();
    const lang = useLang();
    const period = settings.period;
    useChannels(["network"]);
    const live = useLiveState();
    const q = useQuery({ ...networkQuery(period), refetchInterval: period === "1h" ? false : SLOW_POLL_MS });
    const [table, setTable] = React.useState(false);

    const data = q.data;
    const points = completePoints(data);
    const tps = (period === "1h" ? live.tps : null) ?? data?.tps ?? null;
    const delta = period === "1h" ? hourChange(points) : null;
    const today = data?.today;
    const perMinute = data?.interval === "1m";

    const fmtTime = (ms: number) =>
        new Date(ms).toLocaleString(lang, period === "7d"
            ? { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }
            : { hour: "2-digit", minute: "2-digit" });
    const ticks: [string, string, string] =
        period === "1h" ? [t("net.ago60"), t("net.ago30"), t("net.now")]
            : period === "24h" ? [t("net.ago24h"), t("net.ago12h"), t("net.now")]
                : [t("net.ago7d"), t("net.ago3d"), t("net.now")];
    const values = points.map((p) => p.v);
    const summary = values.length
        ? t("net.summary", {
            caption: t(`net.caption.${period}`),
            min: count(Math.min(...values), lang),
            max: count(Math.max(...values), lang),
            last: count(values[values.length - 1], lang),
        })
        : t(`net.caption.${period}`);

    return (
        <Widget
            title={t("widgets.network.title")}
            freshness={<Freshness updatedAt={q.dataUpdatedAt} polled={period !== "1h"} pollMs={SLOW_POLL_MS} />}
            filters={
                <Segmented
                    label={t("net.period")}
                    value={period}
                    onChange={(p) => setSettings({ period: p })}
                    options={[
                        { value: "1h", label: t("period.1h") },
                        { value: "24h", label: t("period.24h") },
                        { value: "7d", label: t("period.7d") },
                    ]}
                />
            }
            state={q.isPending ? "loading" : q.isError && !data ? "error" : !data ? "empty" : "ready"}
            error={q.error}
            onRetry={() => void q.refetch()}
            empty={{ title: t("net.empty"), hint: t("net.emptyHint") }}
            height={height}
            skeletonRows={6}
        >
            <div className="grid grid-cols-2 gap-2 px-4 pt-3 pb-1">
                <Stat
                    value={tps === null ? "—" : tps.toFixed(tps < 10 ? 1 : 0)}
                    unit={t("net.tpsUnit")}
                    caption={
                        delta === null ? t("net.now") : (
                            <>
                                {t("net.now")} · <Num trend={trendOf(delta)}>{change(delta, lang)}</Num> {t("net.vsHourAgo")}
                            </>
                        )
                    }
                />
                <Stat value={count(today?.txs, lang)} caption={t("net.txsToday")} />
                <Stat value={count(today?.signers, lang)} caption={t("net.activeToday")} />
                <Stat value={count(today?.new_accounts, lang)} caption={t("net.newToday")} />
            </div>
            <div className="flex min-h-0 flex-1 flex-col px-4 pt-2 pb-3">
                <div className="mb-1 flex items-center gap-2 text-[11px] text-fg-3">
                    <span className="flex-1 truncate">
                        {t(`net.caption.${period}`)} · {perMinute ? t("net.perMinute") : t("net.perHour")}
                    </span>
                    <button
                        type="button"
                        onClick={() => setTable((v) => !v)}
                        className="grid size-7 place-items-center rounded-md text-fg-2 hover:bg-surface-2 hover:text-fg pointer-coarse:size-11"
                        aria-pressed={table}
                        title={table ? t("net.showChart") : t("net.showTable")}
                    >
                        {table ? <LineChart className="size-4" aria-hidden="true" /> : <Table2 className="size-4" aria-hidden="true" />}
                        <span className="sr-only">{table ? t("net.showChart") : t("net.showTable")}</span>
                    </button>
                </div>
                {table ? (
                    <div className="min-h-0 flex-1 overflow-y-auto">
                        <table className="w-full text-[12px] tabular-nums">
                            <thead className="sticky top-0 bg-surface text-left text-[11px] text-fg-3">
                                <tr><th className="py-1 font-medium">{t("net.time")}</th><th className="py-1 text-right font-medium">{t("net.txs")}</th></tr>
                            </thead>
                            <tbody>
                                {[...points].reverse().map((p) => (
                                    <tr key={p.t} className="border-t border-row-line">
                                        <td className="py-1 text-fg-2">{fmtTime(p.t)}</td>
                                        <td className="py-1 text-right">{count(p.v, lang)}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                ) : (
                    <AreaChart
                        className="min-h-[140px] flex-1"
                        points={points}
                        formatY={(v) => compact(v, lang)}
                        formatTip={(v) => count(v, lang)}
                        formatX={fmtTime}
                        xTicks={ticks}
                        summary={summary}
                    />
                )}
            </div>
        </Widget>
    );
}

export const networkWidget: WidgetDef<Settings> = {
    type: "network",
    title: "widgets.network.title",
    size: { default: "M", min: "S" },
    settings: [],
    defaults: { period: "24h" },
    channels: () => ["network"],
    Component: NetworkWidget,
};
