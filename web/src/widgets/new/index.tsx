import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useTokenMeta } from "@/api/batch";
import { api } from "@/api/client";
import { useOlderRows } from "@/api/paging";
import { newItemsQuery } from "@/api/queries";
import type { NewItem } from "@/api/types";
import { useChannels, usePollWhenOffline } from "@/app/live";
import { msOf } from "@/lib/clock";
import { account as short } from "@/lib/format";
import { clean, tickerOf } from "@/lib/text";
import { AccountCell, accountPath } from "@/ui/AccountCell";
import { Age } from "@/ui/Age";
import { Chip, VenueChip } from "@/ui/Chip";
import { Freshness } from "@/ui/Freshness";
import { LiveList } from "@/ui/LiveList";
import { MoreRows } from "@/ui/MoreRows";
import { INNER, Row, TokenActions } from "@/ui/Row";
import { Segmented } from "@/ui/Segmented";
import { tokenPath } from "@/ui/TokenCell";
import { Widget } from "@/ui/Widget";
import type { WidgetDef, WidgetProps } from "../types";

type Kind = "all" | "ft" | "pool" | "nft" | "contract";
interface Settings extends Record<string, unknown> {
    kind: Kind;
}

const WRAP = "wrap.near";

/** What a row is, for its chip: a contract becomes «Токен» / «NFT» once its first event says so. */
export function kindOf(item: NewItem): "ft" | "nft" | "pool" | "contract" | "other" {
    if (item.kind === "pool") return "pool";
    if (item.kind === "ft" || item.token_kind === "ft") return "ft";
    if (item.kind === "nft" || item.token_kind === "nft") return "nft";
    if (item.kind === "contract") return "contract";
    return "other";
}

/** Where a row leads: a token page, the pool's (non-wNEAR) token, or the account. */
export function targetOf(item: NewItem): string {
    const k = kindOf(item);
    if (k === "ft") return tokenPath(item.id);
    if (k === "pool") {
        const tok = item.tokens?.find((x) => x !== WRAP) ?? item.tokens?.[0];
        return tok ? tokenPath(tok) : accountPath(item.id.split("|")[0]);
    }
    return accountPath(item.id);
}

function Ticker({ token }: { token: string }) {
    const meta = useTokenMeta(token);
    if (meta.isPending) return <span className="skeleton inline-block h-2.5 w-12 align-middle" aria-hidden="true" />;
    return <>{tickerOf(meta.data?.symbol, token)}</>;
}

export function NameCell({ item }: { item: NewItem }) {
    const k = kindOf(item);
    const meta = useTokenMeta(k === "ft" ? item.id : null);
    if (k === "ft" && meta.isPending) return <span className="skeleton h-2.5 w-28" aria-hidden="true" />;
    if (k === "ft") {
        const ticker = tickerOf(meta.data?.symbol, item.id);
        const name = clean(meta.data?.name, 40);
        return (
            <span className="min-w-0 truncate font-semibold" title={item.id}>
                {`$${ticker}`}
                {name && name !== ticker && <span className="font-normal text-fg-2"> · {name}</span>}
            </span>
        );
    }
    if (k === "pool" && item.tokens?.length) {
        return (
            <span className="flex min-w-0 items-center gap-1.5">
                <span className="truncate font-semibold">
                    {item.tokens.map((tk, i) => (
                        <span key={tk}>{i > 0 && " / "}<Ticker token={tk} /></span>
                    ))}
                </span>
                <VenueChip venue={item.venue} />
            </span>
        );
    }
    return (
        <span className="min-w-0 truncate font-mono text-[12px]" title={item.id} translate="no">
            {short(clean(item.id, 80))}
        </span>
    );
}

function NewRow({ item }: { item: NewItem }) {
    const { t } = useTranslation();
    const k = kindOf(item);
    return (
        <Row
            to={targetOf(item)}
            label={`${t(`new.kind.${k}`)} ${item.id}`}
            className="h-[50px] grid-cols-[76px_minmax(0,1.4fr)_auto] @md:grid-cols-[76px_minmax(0,1.4fr)_minmax(0,1fr)_60px]"
            actions={k === "ft" ? <TokenActions token={item.id} /> : undefined}
        >
            <Chip className="justify-self-start">{t(`new.kind.${k}`)}</Chip>
            <NameCell item={item} />
            <span className="hidden min-w-0 @md:block">
                {item.by ? <AccountCell id={item.by} className={INNER} /> : <span className="text-fg-3">—</span>}
            </span>
            <Age at={item.first_seen} className="text-right text-fg-3" />
        </Row>
    );
}

const newKey = (r: NewItem) => r._id;
const newTs = (r: NewItem) => r.first_seen;

function NewWidget({ settings, setSettings, height, full = false }: WidgetProps<Settings>) {
    const { t } = useTranslation();
    useChannels(["new"]);
    const kind = settings.kind === "all" ? undefined : settings.kind;
    const limit = full ? 100 : 50;
    const q = useQuery({ ...newItemsQuery({ kind, limit }), refetchInterval: usePollWhenOffline(10_000) });
    const older = useOlderRows({
        id: ["new", kind ?? null],
        head: q.data,
        headLimit: limit,
        enabled: full,
        keyOf: newKey,
        tsOf: newTs,
        fetchPage: (before, n, signal) => api.newItems({ kind, limit: n, before }, signal),
    });
    const rows = older.rows;
    return (
        <Widget
            title={t("widgets.new.title")}
            freshness={<Freshness updatedAt={q.dataUpdatedAt} />}
            filters={
                <Segmented
                    label={t("new.filter")}
                    value={settings.kind}
                    onChange={(k) => setSettings({ kind: k })}
                    options={[
                        { value: "all", label: t("new.tab.all") },
                        { value: "ft", label: t("new.tab.ft") },
                        { value: "pool", label: t("new.tab.pool") },
                        { value: "nft", label: t("new.tab.nft") },
                        { value: "contract", label: t("new.tab.contract") },
                    ]}
                />
            }
            state={q.isPending ? "loading" : q.isError && !q.data ? "error" : rows.length ? "ready" : "empty"}
            error={q.error}
            onRetry={() => void q.refetch()}
            empty={{
                title: t("new.empty"),
                hint: kind ? t("new.emptyFiltered") : t("new.emptyHint"),
                action: kind ? { label: t("new.showAll"), onClick: () => setSettings({ kind: "all" }) } : undefined,
            }}
            footer={full ? undefined : { to: kind ? `/new?kind=${kind}` : "/new", label: t("new.all") }}
            expandTo={full ? undefined : "/new"}
            height={height}
            skeletonRows={7}
        >
            <LiveList
                items={rows}
                getKey={newKey}
                renderRow={(r) => <NewRow item={r} />}
                page={full}
                footer={full ? <MoreRows older={older} /> : undefined}
            />
        </Widget>
    );
}

function useNewPulse(s: Settings): number[] {
    useChannels(["new"]);
    const q = useQuery(newItemsQuery({ kind: s.kind === "all" ? undefined : s.kind }));
    return (q.data ?? []).map((r) => msOf(r.first_seen) ?? 0);
}

export const newWidget: WidgetDef<Settings> = {
    type: "new",
    title: "widgets.new.title",
    size: { default: "M", min: "S" },
    settings: [],
    defaults: { kind: "all" },
    channels: () => ["new"],
    Component: NewWidget,
    usePulse: useNewPulse,
    fullList: "/new",
    choices: { kind: ["all", "ft", "pool", "nft", "contract"] },
};
