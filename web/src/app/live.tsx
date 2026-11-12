import * as React from "react";
import type { LiveSync, LiveState } from "@/api/live";
import type { DashSocket } from "@/api/ws";
import type { BaseChannel } from "@/api/types";

interface LiveCtx {
    socket: DashSocket;
    live: LiveSync;
}

const Ctx = React.createContext<LiveCtx | null>(null);

/** The socket and the sync live for the whole tab (created in providers.tsx). */
export function LiveProvider({ socket, live, children }: LiveCtx & { children: React.ReactNode }) {
    const value = React.useMemo(() => ({ socket, live }), [socket, live]);
    return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useLiveCtx(): LiveCtx {
    const v = React.useContext(Ctx);
    if (!v) throw new Error("LiveProvider missing");
    return v;
}

type Channel = BaseChannel | `${BaseChannel}:${string}`;

/**
 * The widget's WebSocket channels while it is on screen. Events land in the react-query caches;
 * the widget just reads its query.
 */
export function useChannels(channels: readonly Channel[]) {
    const { socket, live } = useLiveCtx();
    const key = channels.join("|");
    React.useEffect(() => {
        if (!key) return;
        const offs = key.split("|").map((ch) => socket.subscribe(ch as Channel, live.handle));
        return () => offs.forEach((off) => off());
    }, [key, socket, live]);
}

export function useLiveState(): LiveState {
    const { live } = useLiveCtx();
    return React.useSyncExternalStore(live.subscribeState, live.getState);
}

/**
 * REST polling interval for a widget WITH a live channel: off while the socket is live, the
 * endpoint's max-age while it is down or in 4429/4503 fallback.
 */
export function usePollWhenOffline(ms: number): number | false {
    const { status } = useLiveState();
    return status === "open" || status === "idle" || status === "connecting" || status === "hidden" ? false : ms;
}
