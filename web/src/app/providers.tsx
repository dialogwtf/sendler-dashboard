import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import * as React from "react";
import { TooltipProvider } from "@/components/ui/tooltip";
import { ApiError } from "@/api/client";
import { LiveSync } from "@/api/live";
import { DashSocket } from "@/api/ws";
import { LiveProvider } from "./live";

const WS_URL = (import.meta.env.VITE_DASH_WS as string | undefined) || "wss://api.sendler.xyz/v2/dash/ws";

/**
 * One cache and one WebSocket per tab, created once at module level — not in React state:
 * StrictMode unmounts and remounts effects in dev, and a socket stopped by that cleanup never
 * came back.
 */
export const queryClient = new QueryClient({
    defaultOptions: {
        queries: {
            // 429 waits out its pause (client.ts); 4xx never retries; 5xx / network twice.
            retry: (n, err) => !(err instanceof ApiError && (err.status === 429 || err.status < 500)) && n < 2,
            refetchOnWindowFocus: false,
            gcTime: 5 * 60_000,
        },
    },
});

const socket = new DashSocket({
    url: WS_URL,
    doc: document,
    log: import.meta.env.DEV ? (m, x) => console.debug(m, x ?? "") : undefined,
});

const live = new LiveSync(queryClient, socket);

if (import.meta.hot) {
    import.meta.hot.dispose(() => {
        live.dispose();
        socket.stop();
    });
}

export function Providers({ children }: { children: React.ReactNode }) {
    return (
        <QueryClientProvider client={queryClient}>
            <LiveProvider socket={socket} live={live}>
                <TooltipProvider delayDuration={250}>{children}</TooltipProvider>
            </LiveProvider>
        </QueryClientProvider>
    );
}
