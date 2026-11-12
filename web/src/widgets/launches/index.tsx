import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { useTokenMeta } from "@/api/batch";
import { api } from "@/api/client";
import { useOlderRows } from "@/api/paging";
import { launchesQuery } from "@/api/queries";
import type { Launch } from "@/api/types";
import { useChannels, usePollWhenOffline } from "@/app/live";
import { TokenIcon } from "@/components/common/TokenIcon";
import { msOf } from "@/lib/clock";
import { mult, multTrend, near, usd } from "@/lib/format";
import { clean, tickerOf } from "@/lib/text";
import { useLang } from "@/lib/useLang";
import { cn } from "@/lib/utils";
import { AccountCell } from "@/ui/AccountCell";
import { Age } from "@/ui/Age";
import { PadChip, StagePill, Tag } from "@/ui/Chip";
import { Freshness } from "@/ui/Freshness";
import { LiveList } from "@/ui/LiveList";
import { MoreRows } from "@/ui/MoreRows";
import { Num } from "@/ui/Num";
import { INNER, Row, TokenActions } from "@/ui/Row";
import { Segmented } from "@/ui/Segmented";
import { SettingsButton } from "@/ui/SettingsPanel";
import { tokenPath } from "@/ui/TokenCell";
import { Widget } from "@/ui/Widget";
import type { SettingsSchema, WidgetDef, WidgetProps } from "../types";
import { creatorFact, devBuyNear, isFeeFarm, passesMinCap, taxPct, type FactTone } from "./facts";

type PadFilter = "all" | "nearly" | "justhoot" | "intear";
interface Settings extends Record<string, unknown> {
    pad: PadFilter;
    hideFeeFarm: boolean;
    hideBlocked: boolean;
    /** Minimum cap in $, client-side; "0" = any. */
    minCap: string;
}

const ALL_PADS = ["nearly", "justhoot", "intear"];
/** Polite announcements of new launches, no more than once in 10 s (UI spec «Доступность»). */
const ANNOUNCE_EVERY_MS = 10_000;
const TABLE = "@min-[720px]:grid-cols-[minmax(190px,2fr)_minmax(170px,2fr)_70px_56px_82px_78px_76px]";

const TONE: Record<FactTone, string> = { neutral: "text-fg-3", amber: "text-amber-fg", down: "text-down-fg" };

function CreatorNote({ l }: { l: Launch }) {
    const { t } = useTranslation();
    const fact = creatorFact(l.creator_facts);
    if (!fact) return null;
    return <span className={cn("truncate text-[12px] @min-[720px]:text-[11px]", TONE[fact.tone])}>{t(fact.key, fact.values)}</span>;
}

function LaunchRow({ l }: { l: Launch }) {
    const { t } = useTranslation();
    const lang = useLang();
    const meta = useTokenMeta(l.token);
    const ticker = tickerOf(l.symbol ?? meta.data?.symbol, l.token);
    const name = clean(l.name ?? meta.data?.name, 40);
    const dev = l.backfill ? null : devBuyNear(l.dev_buy);
    const tax = l.backfill ? null : taxPct(l.tax);
    const x = l.x;
    const farm = isFeeFarm(l.fee_farm);
    return (
        <Row
            to={tokenPath(l.token)}
            label={`$${ticker}`}
            className={cn("h-[78px] grid-cols-[minmax(0,1fr)_auto] @min-[720px]:h-[60px]", TABLE)}
            actions={<TokenActions token={l.token} code={meta.data?.bot_code} />}
        >
            {/* Token: logo, $TICKER name, pad, age; narrow (MLaunches): three lines — + dev buy and tax, + creator */}
            <div className="flex min-w-0 items-center gap-2.5">
                <TokenIcon symbol={ticker} icon={meta.data?.icon} seed={l.token} size={28} />
                <div className="min-w-0">
                    <div className="truncate font-semibold">
                        {`$${ticker}`}
                        {name && name !== ticker && <span className="font-normal text-fg-2"> {name}</span>}
                    </div>
                    <div className="mt-0.5 flex min-w-0 items-center gap-1.5 whitespace-nowrap">
                        <PadChip pad={l.pad} />
                        <Age at={l.started} className="text-[12px] text-fg-3 @min-[720px]:text-[11px]" />
                        {(dev !== null || tax !== null) && (
                            <span className="min-w-0 truncate text-[12px] text-fg-3 @min-[720px]:hidden">
                                {dev !== null && ` · ${t("launches.devShort", { near: near(dev, lang) })}`}
                                {tax !== null && ` · ${t("launches.taxShort", { pct: tax })}`}
                            </span>
                        )}
                        {farm && <Tag tone="amber">{t("launches.feeFarm")}</Tag>}
                    </div>
                    <div className="mt-0.5 flex min-w-0 items-center gap-1.5 text-[12px] @min-[720px]:hidden">
                        {l.creator && <AccountCell id={l.creator} link={false} className="min-w-0 shrink text-fg-2 [&_span]:!text-[12px]" />}
                        <CreatorNote l={l} />
                    </div>
                </div>
            </div>
            {/* narrow: cap, growth and stage on the right */}
            <div className="flex flex-col items-end gap-0.5 @min-[720px]:hidden">
                <div className="font-semibold tabular-nums">{l.cap_usd !== null ? usd(l.cap_usd, lang) : "—"}</div>
                <Num trend={multTrend(x)} className="text-[12px] font-semibold">
                    {x === null ? t("launches.atStart") : mult(x, lang)}
                </Num>
                <StagePill stage={l.stage} />
            </div>
            {/* wide: the table columns */}
            <div className="hidden min-w-0 flex-col @min-[720px]:flex">
                {l.creator ? <AccountCell id={l.creator} className={INNER} /> : <span className="text-fg-3">—</span>}
                <CreatorNote l={l} />
            </div>
            <Num className="hidden text-right @min-[720px]:block">{dev === null ? "—" : near(dev, lang)}</Num>
            <Num className="hidden text-right text-fg-2 @min-[720px]:block">{tax === null ? "—" : `${tax}%`}</Num>
            <Num strong className="hidden text-right @min-[720px]:block">{l.cap_usd !== null ? usd(l.cap_usd, lang) : "—"}</Num>
            <Num strong trend={multTrend(x)} className="hidden text-right @min-[720px]:block">
                {mult(x, lang)}
            </Num>
            <span className="hidden justify-self-end @min-[720px]:block"><StagePill stage={l.stage} /></span>
        </Row>
    );
}

/** Polite screen-reader announcement of the newest launch, throttled. */
function useAnnouncer(rows: Launch[]): string {
    const { t } = useTranslation();
    const [text, setText] = React.useState("");
    const last = React.useRef({ token: rows[0]?.token, at: 0 });
    React.useEffect(() => {
        const top = rows[0];
        if (!top || top.token === last.current.token) return;
        const now = Date.now();
        if (now - last.current.at < ANNOUNCE_EVERY_MS) return;
        last.current = { token: top.token, at: now };
        setText(t("launches.announce", { ticker: tickerOf(top.symbol, top.token) }));
    }, [rows, t]);
    return text;
}

const launchKey = (l: Launch) => l.token;
const launchTs = (l: Launch) => l.started;

function LaunchesWidget({ settings, setSettings, height, full = false }: WidgetProps<Settings>) {
    const { t } = useTranslation();
    // trade: a launch's cap and growth move with its trades (applyLaunchTrade), not only on a new launch.
    useChannels(["launch", "trade"]);
    const pad = settings.pad === "all" ? ALL_PADS : [settings.pad];
    const limit = full ? 100 : 50;
    const q = useQuery({
        ...launchesQuery({ pad, hideFeeFarm: settings.hideFeeFarm, hideBlocked: settings.hideBlocked, limit }),
        refetchInterval: usePollWhenOffline(5_000),
    });
    const older = useOlderRows({
        id: ["launches", pad, settings.hideFeeFarm, settings.hideBlocked],
        head: q.data,
        headLimit: limit,
        enabled: full,
        keyOf: launchKey,
        tsOf: launchTs,
        fetchPage: (before, n, signal) =>
            api.launches({ pad, hide_fee_farm: settings.hideFeeFarm, hide_blocked: settings.hideBlocked || undefined, limit: n, before }, signal),
    });
    const minCap = Number(settings.minCap) || 0;
    const rows = older.rows.filter((l) => passesMinCap(l, minCap));
    const announce = useAnnouncer(rows);
    const filtered = settings.pad !== "all" || minCap > 0;

    const schema: SettingsSchema = [
        { kind: "toggle", key: "hideFeeFarm", label: t("launches.hideFeeFarm") },
        { kind: "toggle", key: "hideBlocked", label: t("launches.hideBlocked") },
        {
            kind: "chips", key: "minCap", label: t("launches.minCap"),
            options: [
                { value: "0", label: t("launches.anyCap") },
                { value: "5000", label: "$5k" },
                { value: "10000", label: "$10k" },
                { value: "50000", label: "$50k" },
            ],
        },
    ];

    return (
        <Widget
            title={t("widgets.launches.title")}
            freshness={<Freshness updatedAt={q.dataUpdatedAt} />}
            filters={
                <Segmented
                    label={t("launches.pad")}
                    value={settings.pad}
                    onChange={(p) => setSettings({ pad: p })}
                    options={[
                        { value: "all", label: t("launches.allPads") },
                        { value: "nearly", label: "Nearly" },
                        { value: "justhoot", label: "JustHoot" },
                        { value: "intear", label: "Intear" },
                    ]}
                />
            }
            settings={
                <SettingsButton
                    title={t("widgets.launches.title")}
                    schema={schema}
                    values={settings}
                    onChange={(p) => setSettings(p as Partial<Settings>)}
                />
            }
            state={q.isPending ? "loading" : q.isError && !q.data ? "error" : rows.length || (full && older.hasMore) ? "ready" : "empty"}
            error={q.error}
            onRetry={() => void q.refetch()}
            empty={{
                title: t("launches.empty"),
                hint: filtered ? t("launches.emptyFiltered") : t("launches.emptyHint"),
                action: filtered ? { label: t("launches.showAll"), onClick: () => setSettings({ pad: "all", minCap: "0" }) } : undefined,
            }}
            footer={full ? undefined : {
                node: (
                    <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
                        <label className="flex min-h-8 cursor-pointer items-center gap-2 text-fg-2">
                            <input
                                type="checkbox"
                                checked={settings.hideFeeFarm}
                                onChange={(e) => setSettings({ hideFeeFarm: e.target.checked })}
                                className="size-4 accent-[var(--brand)]"
                            />
                            {t("launches.hideFeeFarm")}
                        </label>
                        <span className="flex-1" />
                        <Link
                            to={settings.pad === "all" ? "/launches" : `/launches?pad=${settings.pad}`}
                            className="font-semibold text-brand-text hover:underline pointer-coarse:inline-flex pointer-coarse:min-h-11 pointer-coarse:items-center"
                        >
                            {t("launches.all")}
                        </Link>
                    </div>
                ),
            }}
            expandTo={full ? undefined : "/launches"}
            height={height}
            skeletonRows={9}
        >
            <div
                className={cn("hidden h-[34px] shrink-0 items-center gap-3 border-b border-row-line px-4 text-[11px] font-medium tracking-[0.05em] text-fg-3 uppercase @min-[720px]:grid", TABLE)}
                aria-hidden="true"
            >
                <span>{t("launches.colToken")}</span>
                <span>{t("launches.colCreator")}</span>
                <span className="text-right">{t("launches.colDevBuy")}</span>
                <span className="text-right">{t("launches.colTax")}</span>
                <span className="text-right">{t("launches.colCap")}</span>
                <span className="text-right">{t("launches.colX")}</span>
                <span className="text-right">{t("launches.colStage")}</span>
            </div>
            <LiveList
                items={rows}
                getKey={launchKey}
                renderRow={(l) => <LaunchRow l={l} />}
                page={full}
                footer={full ? <MoreRows older={older} /> : undefined}
            />
            <div className="sr-only" aria-live="polite">{announce}</div>
        </Widget>
    );
}

function useLaunchesPulse(s: Settings): number[] {
    useChannels(["launch"]);
    const q = useQuery(launchesQuery({ pad: s.pad === "all" ? ALL_PADS : [s.pad], hideFeeFarm: s.hideFeeFarm, hideBlocked: s.hideBlocked }));
    const minCap = Number(s.minCap) || 0;
    return (q.data ?? []).filter((l) => passesMinCap(l, minCap)).map((l) => msOf(l.started) ?? 0);
}

export const launchesWidget: WidgetDef<Settings> = {
    type: "launches",
    title: "widgets.launches.title",
    size: { default: "L", min: "M" },
    settings: [],
    defaults: { pad: "all", hideFeeFarm: true, hideBlocked: false, minCap: "0" },
    channels: () => ["launch"],
    Component: LaunchesWidget,
    usePulse: useLaunchesPulse,
    fullList: "/launches",
    choices: { pad: ["all", "nearly", "justhoot", "intear"], minCap: ["0", "5000", "10000", "50000"] },
};
