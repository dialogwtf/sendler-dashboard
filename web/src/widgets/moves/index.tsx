import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { ArrowLeftRight, ArrowRight } from "lucide-react";
import { useAccountLabel, useTokenMeta } from "@/api/batch";
import { api } from "@/api/client";
import { useOlderRows } from "@/api/paging";
import { movesQuery } from "@/api/queries";
import { moveKey } from "@/api/reducers";
import type { Label, Move } from "@/api/types";
import { useChannels, usePollWhenOffline } from "@/app/live";
import { msOf } from "@/lib/clock";
import { account as shortAccount, near, tokenAmount, usd } from "@/lib/format";
import { useMediaQuery } from "@/lib/useMediaQuery";
import { txUrl } from "@/lib/safeUrl";
import { tickerOf } from "@/lib/text";
import { useLang } from "@/lib/useLang";
import { AccountCell, accountPath } from "@/ui/AccountCell";
import { Age } from "@/ui/Age";
import { Freshness } from "@/ui/Freshness";
import { LiveList } from "@/ui/LiveList";
import { MoreRows } from "@/ui/MoreRows";
import { INNER, PanelLinks, Row } from "@/ui/Row";
import { Segmented } from "@/ui/Segmented";
import { Widget } from "@/ui/Widget";
import type { WidgetDef, WidgetProps } from "../types";

type Threshold = "1000" | "10000" | "100000";
interface Settings extends Record<string, unknown> {
    minUsd: Threshold;
    kind: "all" | "transfer" | "swap";
}

/** «1.2M NEAR», «12.3k USDT»; a swap — «USDC → NEAR» by its side. */
export function What({ m }: { m: Move }) {
    const { t } = useTranslation();
    const lang = useLang();
    const meta = useTokenMeta(m.token === "near" ? null : m.token);
    const ticker = m.token === "near" ? "NEAR" : tickerOf(m.symbol ?? meta.data?.symbol, m.token);
    const decimals = m.token === "near" ? 24 : m.decimals ?? meta.data?.decimals ?? null;
    if (m.kind === "swap") {
        const amount = m.near !== undefined ? near(m.near, lang) : tokenAmount(m.amount, decimals, lang);
        return (
            <span>
                <b className="font-semibold">{m.side === "sell" ? `${ticker} → NEAR` : `NEAR → ${ticker}`}</b>{" "}
                <span className="text-fg-2">{amount}</span>
                <span className="sr-only"> {t("moves.swap")}</span>
            </span>
        );
    }
    return (
        <b className="font-semibold">
            {tokenAmount(m.amount, decimals, lang)} {ticker}
        </b>
    );
}

/** A side of the move: its label from the row, or — for socket rows — from the batched /labels. */
export function Side({ id, label, link }: { id: string; label: Label | null | undefined; link: boolean }) {
    const fetched = useAccountLabel(id, label);
    return <AccountCell id={id} link={link} label={label === undefined ? fetched.data : label} className={`${INNER} max-w-full`} />;
}

function MoveRow({ m }: { m: Move }) {
    const { t } = useTranslation();
    const lang = useLang();
    const swap = m.kind === "swap";
    // Touch: «from → to» are too small and close to tap — the row's tap panel offers them instead.
    const touch = useMediaQuery("(hover: none)");
    return (
        <Row
            href={m.tx ? txUrl(m.tx) : undefined}
            label={t("moves.rowLabel", { usd: usd(m.usd, lang) })}
            className="h-[58px] grid-cols-[32px_minmax(0,1fr)_auto]"
            actions={touch ? (
                <PanelLinks
                    links={[
                        { label: <>{t("moves.from")} <span className="truncate font-mono font-normal" translate="no">{shortAccount(m.from)}</span></>, to: accountPath(m.from) },
                        { label: <>{t("moves.toShort")} <span className="truncate font-mono font-normal" translate="no">{shortAccount(m.to)}</span></>, to: accountPath(m.to) },
                    ]}
                />
            ) : undefined}
        >
            <span className="grid size-8 place-items-center rounded-[9px] bg-surface-2 text-fg-2">
                {swap ? (
                    <ArrowLeftRight className="size-4" role="img" aria-label={t("moves.swap")} />
                ) : (
                    <ArrowRight className="size-4" role="img" aria-label={t("moves.transfer")} />
                )}
            </span>
            <div className="min-w-0">
                <div className="truncate text-[12px]">
                    <What m={m} /> <span className="text-fg-3">· <Age at={m.ts} /></span>
                </div>
                <div className="mt-[3px] flex min-w-0 items-center gap-1 text-[11px] text-fg-2">
                    <Side id={m.from} label={m.from_label} link={!touch} />
                    <span aria-hidden="true" className="shrink-0">→</span>
                    <span className="sr-only">{t("moves.to")}</span>
                    <Side id={m.to} label={m.to_label} link={!touch} />
                </div>
            </div>
            <span className="text-right font-display text-[16px] font-semibold tabular-nums">{usd(m.usd, lang)}</span>
        </Row>
    );
}

const moveTs = (m: Move) => m.ts;

function MovesWidget({ settings, setSettings, height, full = false }: WidgetProps<Settings>) {
    const { t } = useTranslation();
    useChannels(["move"]);
    const minUsd = Number(settings.minUsd) || 10_000;
    const kind = settings.kind === "all" ? undefined : settings.kind;
    const limit = full ? 100 : 50;
    const q = useQuery({ ...movesQuery({ minUsd, kind, limit }), refetchInterval: usePollWhenOffline(5_000) });
    const older = useOlderRows({
        id: ["moves", minUsd, kind ?? null],
        head: q.data,
        headLimit: limit,
        enabled: full,
        keyOf: moveKey,
        tsOf: moveTs,
        fetchPage: (before, n, signal) => api.moves({ min_usd: minUsd, kind, limit: n, before }, signal),
    });
    const rows = older.rows;
    return (
        <Widget
            title={t("widgets.moves.title")}
            freshness={<Freshness updatedAt={q.dataUpdatedAt} />}
            filters={
                <>
                    {full && (
                        <Segmented
                            label={t("moves.kindFilter")}
                            value={settings.kind}
                            onChange={(v) => setSettings({ kind: v })}
                            options={[
                                { value: "all", label: t("actions.all") },
                                { value: "transfer", label: t("moves.transfers") },
                                { value: "swap", label: t("moves.swaps") },
                            ]}
                        />
                    )}
                    <Segmented
                        label={t("moves.threshold")}
                        value={settings.minUsd}
                        onChange={(v) => setSettings({ minUsd: v })}
                        options={[
                            { value: "1000", label: "$1k" },
                            { value: "10000", label: "$10k" },
                            { value: "100000", label: "$100k" },
                        ]}
                    />
                </>
            }
            state={q.isPending ? "loading" : q.isError && !q.data ? "error" : rows.length ? "ready" : "empty"}
            error={q.error}
            onRetry={() => void q.refetch()}
            empty={{
                title: t("moves.empty"),
                hint: t("moves.emptyHint"),
                action: minUsd > 1000 ? { label: t("moves.lower"), onClick: () => setSettings({ minUsd: "1000" }) } : undefined,
            }}
            footer={full ? undefined : { to: `/moves?min_usd=${minUsd}`, label: t("moves.all") }}
            expandTo={full ? undefined : "/moves"}
            height={height}
            skeletonRows={6}
        >
            <LiveList
                items={rows}
                getKey={moveKey}
                renderRow={(m) => <MoveRow m={m} />}
                page={full}
                footer={full ? <MoreRows older={older} /> : undefined}
            />
        </Widget>
    );
}

function useMovesPulse(s: Settings): number[] {
    useChannels(["move"]);
    const q = useQuery(movesQuery({ minUsd: Number(s.minUsd) || 10_000, kind: s.kind === "all" ? undefined : s.kind }));
    return (q.data ?? []).map((m) => msOf(m.ts) ?? 0);
}

export const movesWidget: WidgetDef<Settings> = {
    type: "moves",
    title: "widgets.moves.title",
    size: { default: "M", min: "S" },
    settings: [],
    defaults: { minUsd: "10000", kind: "all" },
    channels: () => ["move"],
    Component: MovesWidget,
    usePulse: useMovesPulse,
    fullList: "/moves",
    choices: { minUsd: ["1000", "10000", "100000"], kind: ["all", "transfer", "swap"] },
};
