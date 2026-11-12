import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { holdersQuery, smartTopQuery } from "@/api/queries";
import type { Holder, HoldersReady, SmartWallet } from "@/api/types";
import { Button } from "@/components/ui/button";
import { serverNow } from "@/lib/clock";
import { account as shortAccount, age, compact, count, tokenAmount } from "@/lib/format";
import { clean } from "@/lib/text";
import { useLang } from "@/lib/useLang";
import { cn } from "@/lib/utils";
import { AccountCell, accountPath, useLabelText } from "@/ui/AccountCell";
import { Age } from "@/ui/Age";
import { Sparkline } from "@/ui/AreaChart";
import { CreatorTag, SmartTag, Tag } from "@/ui/Chip";
import { Num } from "@/ui/Num";
import { Row, WalletActions } from "@/ui/Row";
import { Fact } from "@/ui/Tile";
import { Widget } from "@/ui/Widget";
import { creatorRisk, holdersChange, sharePct } from "./holdersMath";

/** The list shows this many first; «Показать ещё» reveals the rest of the one request (≤ 100). */
const FIRST = 50;

/**
 * «Холдеры» of a launch token (plan, next steps row 3): the count with its 24 h change and the
 * hourly series, concentration in one strip (top 10 wallets · creator · linked · pools and
 * contracts), then the largest holders. Phone: a tab of the token screen (PhoneFrame, no title);
 * desktop: a widget of the right column, the list scrolling inside.
 */
export function HoldersSection({ id }: { id: string }) {
    const { t } = useTranslation();
    const q = useQuery({ ...holdersQuery(id), refetchInterval: 30_000 });
    const d = q.data;

    if (d && !d.available) {
        const tracked = d.reason === "not_tracked";
        return (
            <Widget
                title={t("token.holders")}
                state="empty"
                live={false}
                empty={tracked
                    ? { title: t("token.holdersNotTracked") }
                    : { title: t("token.holdersPending"), hint: t("token.holdersPendingHint") }}
            />
        );
    }

    return (
        <Widget
            title={t("token.holders")}
            state={q.isPending ? "loading" : q.isError && !d ? "error" : "ready"}
            error={q.error}
            onRetry={() => void q.refetch()}
            live={false}
            skeletonRows={6}
        >
            {d && <HoldersBody d={d} />}
        </Widget>
    );
}

function HoldersBody({ d }: { d: HoldersReady }) {
    const { t } = useTranslation();
    const [all, setAll] = React.useState(false);
    const top = useQuery(smartTopQuery(200));
    const smart = React.useMemo(() => new Map((top.data ?? []).map((w) => [w._id, w])), [top.data]);
    const rows = all ? d.rows : d.rows.slice(0, FIRST);
    const rest = d.rows.length - rows.length;

    return (
        <>
            <Summary d={d} />
            <Concentration d={d} />
            {d.rows.length > 0 ? (
                <div className="md:max-h-[560px] md:overflow-y-auto">
                    <div
                        className="hidden h-[34px] items-center gap-3 border-b border-row-line px-4 text-[11px] font-medium tracking-[0.05em] text-fg-3 uppercase @min-[640px]:grid @min-[640px]:grid-cols-[28px_minmax(0,1fr)_96px_64px]"
                        aria-hidden="true"
                    >
                        <span>#</span>
                        <span>{t("token.colAccount")}</span>
                        <span className="text-right">{t("token.colTokens")}</span>
                        <span className="text-right">{t("token.colShare")}</span>
                    </div>
                    <ol aria-label={t("token.holdersList")}>
                        {rows.map((r, i) => (
                            <li key={r.account}>
                                <HolderRow r={r} n={i + 1} decimals={d.decimals} smart={smart} />
                            </li>
                        ))}
                    </ol>
                    {rest > 0 && (
                        <div className="flex justify-center px-4 py-3">
                            <Button variant="secondary" size="sm" className="pointer-coarse:h-11 pointer-coarse:px-4" onClick={() => setAll(true)}>
                                {t("token.holdersMore", { n: rest })}
                            </Button>
                        </div>
                    )}
                </div>
            ) : (
                <p className="px-4 py-6 text-center text-[12px] text-fg-2">{t("token.holdersNone")}</p>
            )}
            <Caption d={d} />
        </>
    );
}

/** The count large («≥ N» when only the top 100 was checked), its change, the hourly series. */
function Summary({ d }: { d: HoldersReady }) {
    const { t } = useTranslation();
    const lang = useLang();
    const ch = holdersChange(d.series, d.holders, serverNow());
    const period = ch ? (ch.hours >= 24 ? t("period.24h") : age(ch.hours * 3600, lang)) : "";
    const delta = ch ? `${ch.delta > 0 ? "+" : ch.delta < 0 ? "−" : ""}${count(Math.abs(ch.delta), lang)}` : "";
    const values = d.series.map((p) => p.n);
    return (
        <div className="flex items-center gap-4 px-4 pt-3.5 pb-3">
            <div className="min-w-0 flex-1">
                <div className="flex items-baseline gap-2">
                    <span className="font-display text-[28px] leading-none font-bold tabular-nums">
                        {d.top_only ? `≥ ${count(d.holders, lang)}` : count(d.holders, lang)}
                    </span>
                    <span className="text-[13px] text-fg-2">{t("token.holdersCount", { count: d.holders })}</span>
                </div>
                {ch && (
                    <div className="mt-1.5 text-[12px] tabular-nums">
                        <span className={cn("font-semibold", ch.delta > 0 ? "text-up-fg" : ch.delta < 0 ? "text-down-fg" : "text-fg-2")}>{delta}</span>{" "}
                        <span className="text-fg-3">{t("token.holdersPeriod", { period })}</span>
                    </div>
                )}
            </div>
            {values.length >= 2 && (
                <Sparkline values={values} width={120} height={40} label={t("token.holdersSpark", { from: count(values[0], lang), to: count(values[values.length - 1], lang) })} />
            )}
        </div>
    );
}

/**
 * Who holds the supply, one strip of four numbers like the phone token strip. Creator + linked from
 * 10% — both cells amber with «!», and a line saying how much they hold together (never colour alone).
 */
function Concentration({ d }: { d: HoldersReady }) {
    const { t } = useTranslation();
    const risk = creatorRisk(d.dev_share, d.linked_share);
    const cells: { key: string; label: string; hint?: string; value: number | null; risky?: boolean }[] = [
        { key: "top10", label: t("token.concTop10"), hint: t("token.concTop10Hint"), value: d.top10_share },
        { key: "dev", label: t("token.concCreator"), value: d.creator ? d.dev_share : null, risky: risk !== null },
        { key: "linked", label: t("token.concLinked"), hint: t("token.concLinkedHint"), value: d.linked_share, risky: risk !== null },
        { key: "contracts", label: t("token.concContracts"), hint: t("token.concContractsHint"), value: d.contracts_share },
    ];
    return (
        <div className="border-y border-line-soft">
            <dl className="grid grid-cols-4" aria-label={t("token.concentration")}>
                {cells.map((c, i) => (
                    <div key={c.key} title={c.hint} className={cn("flex min-w-0 flex-col-reverse justify-end py-2", i === 0 ? "pr-1 pl-4" : "border-l border-line-soft px-2.5")}>
                        <dt className="mt-0.5 text-[11px] leading-tight text-fg-3">{c.label}</dt>
                        <dd className={cn("truncate text-[15px] leading-tight font-semibold tabular-nums", c.risky && "text-amber-fg")}>
                            {c.risky && <span aria-hidden="true">! </span>}
                            {sharePct(c.value)}
                        </dd>
                    </div>
                ))}
            </dl>
            {risk !== null && (
                <div className="border-t border-line-soft px-4 text-[12px]">
                    <Fact mark="!" tone="amber">
                        <span className="text-amber-fg">{t("token.creatorRisk", { pct: sharePct(risk) })}</span>
                    </Fact>
                </div>
            )}
        </div>
    );
}

const KIND_TAGS = new Set(["pool", "contract"]);

function HolderRow({
    r, n, decimals, smart,
}: { r: Holder; n: number; decimals: number | null; smart: Map<string, SmartWallet> }) {
    const { t } = useTranslation();
    const lang = useLang();
    const labelText = useLabelText(r.label);
    const share = sharePct(r.share);
    const amount = decimals !== null ? tokenAmount(r.bal, decimals, lang) : compact(r.amount, lang);
    const wallet = !r.tags.some((x) => KIND_TAGS.has(x));
    const tags = <HolderTags r={r} labelText={labelText} smart={smart} />;
    return (
        <Row
            to={accountPath(r.account)}
            label={t("token.holderRow", { account: shortAccount(r.account), share })}
            className="h-[54px] grid-cols-[20px_minmax(0,1fr)_auto] @min-[640px]:h-11 @min-[640px]:grid-cols-[28px_minmax(0,1fr)_96px_64px]"
            // Wallets only: watching a pool or a contract in the bot means nothing.
            actions={wallet ? <WalletActions account={r.account} /> : undefined}
        >
            <span className="text-[12px] font-semibold text-fg-3 tabular-nums">{n}</span>
            {/* narrow: account / tags on the left, share / amount on the right */}
            <div className="flex min-w-0 flex-col gap-1 @min-[640px]:hidden">
                <AccountCell id={r.account} label={r.label} link={false} />
                {r.tags.length > 0 && <span className="flex min-w-0 gap-1 overflow-hidden">{tags}</span>}
            </div>
            <div className="flex flex-col items-end gap-1 @min-[640px]:hidden">
                <Num strong>{share}</Num>
                <Num className="text-[11px] text-fg-3">{amount}</Num>
            </div>
            {/* wide: one line */}
            <span className="hidden min-w-0 items-center gap-1.5 @min-[640px]:flex">
                <AccountCell id={r.account} label={r.label} link={false} />
                {tags}
            </span>
            <Num className="hidden text-right text-fg-2 @min-[640px]:block">{amount}</Num>
            <Num strong className="hidden text-right @min-[640px]:block">{share}</Num>
        </Row>
    );
}

/** The API's tags as chips; one that repeats the account's own label («контракт») is dropped. */
function HolderTags({ r, labelText, smart }: { r: Holder; labelText: string | null; smart: Map<string, SmartWallet> }) {
    const { t } = useTranslation();
    return (
        <>
            {r.tags.map((tag) => {
                if (tag === "smart") return <SmartTag key={tag} rank={smart.get(r.account)?.rank ?? 0} />;
                if (tag === "creator") return <CreatorTag key={tag} />;
                if (tag === "linked") return <Tag key={tag} tone="amber">{t("tags.linked")}</Tag>;
                const known = ["pool", "contract", "exchange", "hub"].includes(tag);
                const text = known ? t(`tags.${tag}`) : clean(tag, 16);
                if (text === labelText) return null;
                return <Tag key={tag}>{text}</Tag>;
            })}
        </>
    );
}

/** «обновлено 2 мин назад»; a count from the top 100 only says it is a lower bound. */
function Caption({ d }: { d: HoldersReady }) {
    const { t } = useTranslation();
    if (!d.updated_at && !d.top_only) return null;
    return (
        <div className="flex flex-col gap-1 border-t border-line-soft px-4 py-2.5 text-[12px] text-fg-3">
            {d.updated_at && (
                <span>
                    {t("token.holdersUpdated")} <Age at={d.updated_at} /> {t("token.ago")}
                </span>
            )}
            {d.top_only && <span>{t("token.holdersLowerBound")}</span>}
        </div>
    );
}
