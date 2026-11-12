import * as React from "react";
import { createBrowserRouter, Outlet, RouterProvider, ScrollRestoration, useLocation, useNavigate } from "react-router-dom";
import { telegramBack } from "@/lib/telegram";
import { Providers } from "./providers";
import { Shell } from "./Shell";
import { PageError, PageBoundary } from "./PageError";
import { HomePage } from "@/pages/Home";
import { NotFoundPage } from "@/pages/NotFound";

// Everything beyond the home screen is a lazy chunk (the token page carries the chart library).
const TokenPage = React.lazy(() => import("@/pages/Token"));
const ListPage = React.lazy(() => import("@/pages/List"));
const AccountPage = React.lazy(() => import("@/pages/Account"));

/**
 * Internal showcases (/dev/ui, /dev/live) — only in `npm run dev`, or in a build made with
 * VITE_DEV_PAGES=1. A production build drops the branch, and their chunks with it.
 */
const devRoutes = import.meta.env.DEV || import.meta.env.VITE_DEV_PAGES === "1"
    ? (() => {
        const DevUiPage = React.lazy(() => import("@/pages/dev/DevUi"));
        const DevLivePage = React.lazy(() => import("@/pages/dev/DevLive"));
        return [
            { path: "/dev/ui", element: <DevUiPage /> },
            { path: "/dev/live", element: <DevLivePage /> },
        ];
    })()
    : [];

/**
 * Inside Telegram: its own «Назад» (the header / full-screen bar) on every page but the home —
 * back in the history, or home when the page was opened directly (as the token page's arrow).
 */
function TelegramBack() {
    const { pathname, key } = useLocation();
    const navigate = useNavigate();
    React.useEffect(() => {
        const back = telegramBack();
        if (!back) return;
        if (pathname === "/") {
            back.hide();
            return;
        }
        const onClick = () => (key !== "default" ? navigate(-1) : navigate("/"));
        back.onClick(onClick).show();
        return () => void back.offClick(onClick);
    }, [pathname, key, navigate]);
    return null;
}

function Root() {
    const { pathname } = useLocation();
    return (
        <Shell>
            <TelegramBack />
            <PageBoundary resetKey={pathname}>
                <React.Suspense fallback={<div className="min-h-[60vh]" />}>
                    <Outlet />
                </React.Suspense>
            </PageBoundary>
            <ScrollRestoration />
        </Shell>
    );
}

const router = createBrowserRouter([
    {
        element: <Root />,
        errorElement: <PageError />,
        children: [
            { path: "/", element: <HomePage /> },
            { path: "/token/:id", element: <TokenPage /> },
            { path: "/launches", element: <ListPage kind="launches" /> },
            { path: "/smart", element: <ListPage kind="smart" /> },
            { path: "/moves", element: <ListPage kind="moves" /> },
            { path: "/new", element: <ListPage kind="new" /> },
            { path: "/account/:id", element: <AccountPage /> },
            ...devRoutes,
            { path: "*", element: <NotFoundPage /> },
        ],
    },
]);

export function App() {
    return (
        <Providers>
            <RouterProvider router={router} />
        </Providers>
    );
}
