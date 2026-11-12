import type * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { healthQuery, networkQuery } from "@/api/queries";
import { useChannels, useLiveState } from "@/app/live";
import { age, blockHeight, count, usd } from "@/lib/format";
import { useLang } from "@/lib/useLang";
import { useNow } from "@/lib/useNow";
import { cn } from "@/lib/utils";

/** API data older than this behind the chain → amber warning instead of the live dot. */
const BEHIND_WARN_S = 30;

/**
 * Second header row (Main board): NEAR price, block (live), load tx/s, transactions, new and
 * active accounts today; freshness on the right. Height and tx/s come from the `network`
 * channel, the day totals from /network (also advanced by the channel), the price from /health.
 */
export function PulseRow({ compact = false }: { compact?: boolean }) {
    const { t } = useTranslation();
    const lang = useLang();
    useChannels(["network"]);
    const live = useLiveState();
    const health = useQuery(healthQuery());
    const net = useQuery(networkQuery("1h"));
    const now = useNow();

    const nearUsd = health.data?.near_usd ?? net.data?.near_usd ?? null;
    const height = live.height ?? health.data?.height ?? null;
    const tps = live.tps ?? net.data?.tps ?? null;
    const today = net.data?.today;

    // Phone (plan step 12): NEAR price and the live dot in the logo row — block height and load
    // live in the «Сеть» tab, and a second header row costs a row of the widget.
    if (compact) {
        return (
            <div className="flex min-w-0 items-center gap-3 text-[12px]">
                <Item label="NEAR">
                    <b className="font-semibold">{nearUsd === null ? "—" : usd(nearUsd, lang)}</b>
                </Item>
                <Fresh
                    status={live.status}
                    sinceBlock={null}
                    lostFor={live.lostAt ? (Date.now() - live.lostAt) / 1000 : null}
                    behind={health.data?.behind_s ?? null}
                    now={now}
                />
            </div>
        );
    }

    return (
        <div className="mx-auto flex max-w-[1440px] flex-wrap items-center gap-x-5 gap-y-1.5 px-4 pb-2.5 text-[12px] sm:px-6">
            <Item label="NEAR">
                <b className="font-semibold">{nearUsd === null ? "—" : usd(nearUsd, lang)}</b>
            </Item>
            <Item label={t("pulse.block")}>
                <span className="font-mono" translate="no">
                    {blockHeight(height)}
                </span>
            </Item>
            <Item label={t("pulse.load")}>{tps === null ? "—" : t("pulse.tps", { n: tps.toFixed(tps < 10 ? 1 : 0) })}</Item>
            <Item label={t("pulse.txsToday")} className="hidden sm:inline">{count(today?.txs, lang)}</Item>
            <Item label={t("pulse.newAccounts")} className="hidden md:inline">{count(today?.new_accounts, lang)}</Item>
            <Item label={t("pulse.activeAccounts")} className="hidden lg:inline">{count(today?.signers, lang)}</Item>
            <span className="flex-1" />
            <Fresh
                status={live.status}
                sinceBlock={live.heightAt ? (Date.now() - live.heightAt) / 1000 : null}
                lostFor={live.lostAt ? (Date.now() - live.lostAt) / 1000 : null}
                behind={health.data?.behind_s ?? null}
                now={now}
            />
        </div>
    );
}

function Item({ label, children, className }: { label: string; children: React.ReactNode; className?: string }) {
    return (
        <span className={cn("whitespace-nowrap", className)}>
            <span className="text-fg-3">{label}</span> {children}
        </span>
    );
}

function Fresh({
    status,
    sinceBlock,
    lostFor,
    behind,
}: {
    status: string;
    sinceBlock: number | null;
    lostFor: number | null;
    behind: number | null;
    now: number;
}) {
    const { t } = useTranslation();
    const lang = useLang();
    if (status !== "open" && lostFor !== null) {
        return (
            <span className="rounded-md bg-surface-2 px-[7px] py-[3px] text-[11px] font-semibold text-fg-2">
                {t("fresh.ago", { age: age(lostFor, lang) })}
            </span>
        );
    }
    if (behind !== null && behind > BEHIND_WARN_S) {
        return (
            <span className="rounded-md bg-amber-soft px-[7px] py-[3px] text-[11px] font-semibold text-amber-fg">
                {t("pulse.behind", { age: age(behind, lang) })}
            </span>
        );
    }
    if (status !== "open") return null;
    return (
        <span className="inline-flex items-center gap-1.5 text-[11px] font-semibold tracking-[0.04em] text-up-fg">
            <span
                aria-hidden="true"
                className="size-[7px] rounded-full bg-up shadow-[0_0_0_3px_color-mix(in_srgb,var(--up)_18%,transparent)]"
            />
            {sinceBlock === null ? "LIVE" : t("pulse.updated", { age: age(sinceBlock, lang) })}
        </span>
    );
}
