import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { healthQuery } from "@/api/queries";
import type { BaseChannel, WsEvent } from "@/api/types";
import { useChannels, useLiveCtx, useLiveState } from "@/app/live";
import { age } from "@/lib/format";
import { useNow } from "@/lib/useNow";
import { cn } from "@/lib/utils";

/**
 * /dev/live — the data layer at work (step 3 «готово, когда»): socket status, block height,
 * event counts per channel and the last events straight from production.
 */
const CHANNELS: BaseChannel[] = ["launch", "trade", "move", "new", "network"];

export default function DevLivePage() {
    const { socket } = useLiveCtx();
    const live = useLiveState();
    const now = useNow();
    const health = useQuery(healthQuery());
    const [on, setOn] = React.useState<Record<BaseChannel, boolean>>({ launch: true, trade: true, move: true, new: true, network: true });
    const [events, setEvents] = React.useState<WsEvent[]>([]);
    useChannels(["network"]); // through the data layer too: the block height comes from LiveSync
    const [counts, setCounts] = React.useState<Record<string, number>>({});

    React.useEffect(() => {
        const offs = CHANNELS.filter((c) => on[c]).map((c) =>
            socket.subscribe(c, (e) => {
                setEvents((list) => [e, ...list].slice(0, 60));
                setCounts((m) => ({ ...m, [c]: (m[c] ?? 0) + 1 }));
            }),
        );
        return () => offs.forEach((o) => o());
    }, [socket, on]);

    return (
        <div className="space-y-4">
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                <Stat label="Сокет" value={live.status} tone={live.status === "open" ? "up" : "amber"} />
                <Stat label="Блок (ws)" value={live.height ? live.height.toLocaleString("ru") : "—"} />
                <Stat label="Отставание API" value={health.data ? `${health.data.behind_s.toFixed(1)} с` : "—"} />
                <Stat label="Последнее сообщение" value={socket.lastMessageAt ? age((Date.now() - socket.lastMessageAt) / 1000, "ru") : "—"} hint={String(now).slice(-1)} />
            </div>
            <div className="flex flex-wrap items-center gap-2">
                {CHANNELS.map((c) => (
                    <label key={c} className="inline-flex cursor-pointer items-center gap-2 rounded-lg bg-surface-2 px-3 py-1.5 text-[12px]">
                        <input type="checkbox" checked={on[c]} onChange={(e) => setOn((s) => ({ ...s, [c]: e.target.checked }))} />
                        <b>{c}</b> <span className="text-fg-3">{counts[c] ?? 0}</span>
                    </label>
                ))}
                <button type="button" onClick={() => socket.retryNow()} className="rounded-lg bg-surface-2 px-3 py-1.5 text-[12px]">
                    retryNow()
                </button>
                <span className="font-mono text-[11px] text-fg-3">subs: {socket.wantedChannels().join(", ") || "—"}</span>
            </div>
            <div className="overflow-hidden rounded-[var(--radius-widget)] border border-line bg-surface">
                {events.length === 0 && <div className="p-4 text-fg-3">Ждём события…</div>}
                {events.map((e, i) => (
                    <div key={i} className="grid grid-cols-[72px_90px_minmax(0,1fr)] gap-3 border-b border-row-line px-4 py-1.5 font-mono text-[11.5px]">
                        <span className="text-brand-text">{e.ch.split(":")[0]}</span>
                        <span className="text-fg-3">{e.ts.slice(11, 19)}</span>
                        <span className="truncate text-fg-2">{JSON.stringify(e.d)}</span>
                    </div>
                ))}
            </div>
        </div>
    );
}

function Stat({ label, value, tone, hint }: { label: string; value: React.ReactNode; tone?: "up" | "amber"; hint?: string }) {
    return (
        <div className="rounded-[10px] bg-surface p-3 ring-1 ring-line" data-tick={hint}>
            <div className="text-[11px] text-fg-3">{label}</div>
            <div className={cn("font-display text-[20px] font-semibold", tone === "up" && "text-up-fg", tone === "amber" && "text-amber-fg")}>{value}</div>
        </div>
    );
}
