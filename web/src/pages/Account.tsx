import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router-dom";
import { useAccountLabel, useTokenMeta } from "@/api/batch";
import { creatorQuery, smartTopQuery } from "@/api/queries";
import type { Creator, CreatorHistory, CreatorLink, SmartWallet } from "@/api/types";
import { TokenIcon } from "@/components/common/TokenIcon";
import { botCode, watchWalletUrl } from "@/lib/botLinks";
import { age, count, mult, multTrend, near, signedPct, usd } from "@/lib/format";
import { EXTERNAL_REL, explorerAccountUrl } from "@/lib/safeUrl";
import { clean, tickerOf } from "@/lib/text";
import { useLang } from "@/lib/useLang";
import { cn } from "@/lib/utils";
import { AccountCell, accountPath, useLabelText } from "@/ui/AccountCell";
import { Age } from "@/ui/Age";
import { CreatorTag, PadChip, SmartTag, StagePill, Tag } from "@/ui/Chip";
import { CopyButton } from "@/ui/CopyButton";
import { Num } from "@/ui/Num";
import { INNER, Row, TokenActions, WatchInBot } from "@/ui/Row";
import { Fact, Tile, TileSkeletons } from "@/ui/Tile";
import { tokenPath } from "@/ui/TokenCell";
import { Widget, WidgetBoundary } from "@/ui/Widget";
import { BackLink } from "./List";
import { launchLines, type LaunchLine } from "./account/lines";

/**
 * /account/:id (plan step 7): the creator's dossier and launches; for a smart wallet — its
 * line of the top-200. Any account gets the page: what we know, or a plain «nothing yet».
 * Large moves of the account and its tokens come in a later phase (no API by account yet).
 */
export default function AccountPage() {
    const { id = "" } = useParams();
    return <AccountView key={id} id={id} />;
}

function AccountView({ id }: { id: string }) {
    const { t } = useTranslation();
    const creatorQ = useQuery(creatorQuery(id));
    const top = useQuery(smartTopQuery(200));
    const smart = top.data?.find((w) => w._id === id) ?? null;
    const c = creatorQ.data;
    const isCreator = !!c && (c.launches > 0 || c.recent.length > 0 || c.history.length > 0);
    const loading = creatorQ.isPending || top.isPending;

    React.useEffect(() => {
        document.title = `${id} — Sendler Dash`;
        return () => { document.title = "Sendler Dash"; };
    }, [id]);

    return (
        <div className="mx-auto max-w-[1100px]">
            <BackLink />
            <Header id={id} creator={c} smart={smart} isCreator={isCreator} />
            {loading ? (
                <TileSkeletons n={4} className="mt-5 grid grid-cols-2 gap-3 md:grid-cols-4" />
            ) : (
                <>
                    {smart && <SmartTiles w={smart} />}
                    {isCreator && c && <CreatorTiles c={c} />}
                </>
            )}
            {!loading && !isCreator && !smart && (
                <div className="mt-5">
                    <Widget
                        title={t("account.title")}
                        state={creatorQ.isError ? "error" : "empty"}
                        error={creatorQ.error}
                        onRetry={() => void creatorQ.refetch()}
                        live={false}
                        empty={{ title: t("account.nothing"), hint: t("account.nothingHint") }}
                    />
                </div>
            )}
            {isCreator && c && (
                <div className="mt-4 grid items-start gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
                    <WidgetBoundary title={t("account.launchesTitle")}><LaunchesSection c={c} /></WidgetBoundary>
                    <div className="flex min-w-0 flex-col gap-4">
                        <WidgetBoundary title={t("account.dossier")}><DossierSection c={c} /></WidgetBoundary>
                        {c.links.length > 0 && <WidgetBoundary title={t("account.linksTitle")}><LinksSection links={c.links} /></WidgetBoundary>}
                    </div>
                </div>
            )}
        </div>
    );
}

// --- header ------------------------------------------------------------------------------------

function Header({ id, creator, smart, isCreator }: { id: string; creator: Creator | undefined; smart: SmartWallet | null; isCreator: boolean }) {
    const { t } = useTranslation();
    const label = useAccountLabel(id);
    const text = useLabelText(label.data);
    const code = creator?.bot_code ?? botCode(id);
    return (
        <div className="flex flex-wrap items-center gap-x-5 gap-y-3">
            <div className="min-w-0 flex-[1_1_320px]">
                <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1.5">
                    <h1 className="min-w-0 font-mono text-[24px] leading-tight font-semibold break-all max-md:text-[18px]" translate="no">
                        {clean(id, 80)}
                    </h1>
                    {text && <Tag>{text}</Tag>}
                    {smart && <SmartTag rank={smart.rank} />}
                    {isCreator && <CreatorTag />}
                </div>
                <div className="mt-1 flex flex-wrap items-center gap-x-2">
                    <CopyButton value={id} label={t("account.copy")} />
                    <a href={explorerAccountUrl(id)} target="_blank" rel={EXTERNAL_REL} className="text-[12px] text-brand-text hover:underline pointer-coarse:inline-flex pointer-coarse:min-h-11 pointer-coarse:items-center">
                        {t("token.explorer")}
                    </a>
                </div>
            </div>
            {(smart || isCreator) && <WatchInBot url={watchWalletUrl(id, code)} wide />}
        </div>
    );
}

// --- tiles -------------------------------------------------------------------------------------

function SmartTiles({ w }: { w: SmartWallet }) {
    const { t } = useTranslation();
    const lang = useLang();
    const tone = (n: number) => (n > 0 ? "up" : n < 0 ? "down" : "flat");
    return (
        <section aria-label={t("account.smartTitle")} className="mt-5">
            <h2 className="mb-2 text-[13px] font-semibold text-fg-2">{t("account.smartTitle")}</h2>
            <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                <Tile label={t("account.rank")} value={`#${w.rank}`} sub={t("account.ofTop")} />
                <Tile
                    label={t("account.pnl")}
                    value={`${w.pnl > 0 ? "+" : ""}${near(w.pnl, lang)}`}
                    tone={tone(w.pnl)}
                    sub={t("account.invested", { near: near(w.invested, lang) })}
                />
                <Tile label={t("account.roi")} value={signedPct(w.roi)} tone={tone(w.roi)} sub={t("account.days30")} />
                <Tile
                    label={t("account.winRate")}
                    value={`${Math.round(w.win_rate * 100)}%`}
                    sub={t("account.winsOf", { wins: count(w.wins, lang), tokens: count(w.tokens, lang) })}
                />
            </div>
        </section>
    );
}

function CreatorTiles({ c }: { c: Creator }) {
    const { t } = useTranslation();
    const lang = useLang();
    return (
        <section aria-label={t("account.creatorTitle")} className="mt-5">
            <h2 className="mb-2 text-[13px] font-semibold text-fg-2">{t("account.creatorTitle")}</h2>
            <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                <Tile label={t("account.launches")} value={count(c.launches, lang)} />
                <Tile
                    label={t("account.dumped")}
                    value={count(c.dumped, lang)}
                    tone={c.dumped > 0 ? "down" : undefined}
                    sub={t("account.dumpedHint")}
                />
                <Tile label={t("account.blockedBy")} value={count(c.blocked_by, lang)} tone={c.blocked_by > 0 ? "down" : undefined} sub={t("account.blockedByHint")} />
                <Tile label={t("account.linked")} value={count(c.links.length, lang)} tone={c.links.length > 0 ? "down" : undefined} sub={t("account.linkedHint")} />
            </div>
        </section>
    );
}

// --- launches ----------------------------------------------------------------------------------

function DevLine({ h }: { h: CreatorHistory | null }) {
    const { t } = useTranslation();
    const lang = useLang();
    if (!h) return <span className="text-fg-3">—</span>;
    if (!h.sold_share) return <span className="text-up-fg">✓ {t("token.notSold")}</span>;
    return (
        <span className={h.dumped ? "text-amber-fg" : "text-fg-2"}>
            {h.dumped ? "◆ " : ""}
            {t("token.soldShare", { pct: Math.round(h.sold_share * 100) })}
            {h.first_sell_s !== null ? ` ${t("token.firstSellAfter", { age: age(h.first_sell_s, lang) })}` : ""}
            {h.sold_near ? ` — ${near(h.sold_near, lang)}` : ""}
        </span>
    );
}

function LaunchLineRow({ line }: { line: LaunchLine }) {
    const lang = useLang();
    const meta = useTokenMeta(line.token);
    const l = line.launch;
    const ticker = tickerOf(l?.symbol ?? meta.data?.symbol, line.token);
    const pad = l?.pad ?? line.stats?.pad;
    return (
        <Row
            to={tokenPath(line.token)}
            label={`$${ticker}`}
            className="h-[60px] grid-cols-[minmax(0,1fr)_auto] @min-[640px]:grid-cols-[minmax(150px,1.2fr)_minmax(0,1.6fr)_76px_60px_84px]"
            actions={<TokenActions token={line.token} code={meta.data?.bot_code} />}
        >
            <div className="flex min-w-0 items-center gap-2.5">
                <TokenIcon symbol={ticker} icon={meta.data?.icon} seed={line.token} size={28} />
                <div className="min-w-0">
                    <div className="truncate font-semibold">{`$${ticker}`}</div>
                    <div className="mt-0.5 flex min-w-0 items-center gap-1.5">
                        {pad && <PadChip pad={pad} />}
                        <Age at={line.started} className="text-[11px] text-fg-3" />
                    </div>
                </div>
            </div>
            {/* narrow: cap and growth on the right, the dev's line under them */}
            <div className="text-right @min-[640px]:hidden">
                <div className="font-semibold tabular-nums">{l?.cap_usd != null ? usd(l.cap_usd, lang) : "—"}</div>
                <div className="max-w-[160px] truncate text-[11px]"><DevLine h={line.stats} /></div>
            </div>
            <div className="hidden min-w-0 truncate text-[12px] @min-[640px]:block"><DevLine h={line.stats} /></div>
            <Num strong className="hidden text-right @min-[640px]:block">{l?.cap_usd != null ? usd(l.cap_usd, lang) : "—"}</Num>
            <Num strong trend={multTrend(l?.x ?? null)} className="hidden text-right @min-[640px]:block">{mult(l?.x ?? null, lang)}</Num>
            <span className="hidden justify-self-end @min-[640px]:block">{l ? <StagePill stage={l.stage} /> : null}</span>
        </Row>
    );
}

function LaunchesSection({ c }: { c: Creator }) {
    const { t } = useTranslation();
    const lines = launchLines(c);
    return (
        <Widget
            title={t("account.launchesTitle")}
            state={lines.length ? "ready" : "empty"}
            live={false}
            empty={{ title: t("account.noLaunches") }}
            footer={{ hint: t("account.launchesHint") }}
        >
            <div className="hidden h-[34px] shrink-0 grid-cols-[minmax(150px,1.2fr)_minmax(0,1.6fr)_76px_60px_84px] items-center gap-3 border-b border-row-line px-4 text-[11px] font-medium tracking-[0.05em] text-fg-3 uppercase @min-[640px]:grid" aria-hidden="true">
                <span>{t("launches.colToken")}</span>
                <span>{t("account.colDev")}</span>
                <span className="text-right">{t("launches.colCap")}</span>
                <span className="text-right">{t("launches.colX")}</span>
                <span className="text-right">{t("launches.colStage")}</span>
            </div>
            <div>
                {lines.map((line) => <LaunchLineRow key={line.token} line={line} />)}
            </div>
        </Widget>
    );
}

// --- dossier -----------------------------------------------------------------------------------

function DossierSection({ c }: { c: Creator }) {
    const { t } = useTranslation();
    const sold = c.history.filter((h) => h.sold_share);
    const fastest = sold.reduce<number | null>((m, h) => (h.first_sell_s !== null && (m === null || h.first_sell_s < m) ? h.first_sell_s : m), null);
    const lang = useLang();
    return (
        <Widget title={t("account.dossier")} state="ready" live={false}>
            <div className="px-4 py-2">
                {c.links.length > 0 ? (
                    <Fact mark="!" tone="down">{t("token.linked", { count: c.links.length })}</Fact>
                ) : (
                    <Fact mark="✓" tone="up">{t("token.notLinked")}</Fact>
                )}
                <Fact mark="#" tone={c.blocked_by > 0 ? "down" : "neutral"}>{t("token.blockedBy", { count: c.blocked_by })}</Fact>
                {c.launches > 0 && (
                    <Fact mark="▼" tone={c.dumped > 0 ? "amber" : "neutral"}>{t("token.dumpedOf", { dumped: c.dumped, launches: c.launches })}</Fact>
                )}
                {fastest !== null && (
                    <Fact mark="◆" tone="amber">{t("account.fastestSell", { age: age(fastest, lang) })}</Fact>
                )}
            </div>
        </Widget>
    );
}

function LinksSection({ links }: { links: CreatorLink[] }) {
    const { t } = useTranslation();
    const lang = useLang();
    const shown = links.slice(0, 20);
    return (
        <Widget title={t("account.linksTitle")} state="ready" live={false} footer={{ hint: t("account.linksHint") }}>
            <div>
                {shown.map((l, i) => {
                    const root = typeof l.root === "string" ? l.root : null;
                    const peer = typeof l.peer === "string" && l.peer !== root ? l.peer : null;
                    return (
                        <div key={`${root}:${peer}:${i}`} className="border-b border-row-line px-4 py-2.5 text-[12px] last:border-0">
                            <div className="flex min-w-0 items-center gap-1.5">
                                {root ? (
                                    <Link to={accountPath(root)} className={cn(INNER, "min-w-0 truncate font-mono hover:text-brand-text")} translate="no">
                                        <AccountCell id={root} link={false} />
                                    </Link>
                                ) : (
                                    <span className="text-fg-3">—</span>
                                )}
                                <Tag tone="down">{t("account.blocked")}</Tag>
                            </div>
                            <div className="mt-1 text-fg-3">
                                {l.hop && l.hop > 1 && peer ? t("account.viaPeer", { peer: clean(peer, 64) }) : t("account.direct")}
                                {typeof l.near === "number" ? ` · ${near(l.near, lang)}` : ""}
                                {l.at !== undefined ? <> · <Age at={l.at} /></> : null}
                            </div>
                        </div>
                    );
                })}
            </div>
        </Widget>
    );
}
