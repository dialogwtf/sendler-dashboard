import * as React from "react";
import { useTranslation } from "react-i18next";
import { useRouteError } from "react-router-dom";
import { AlertTriangle } from "lucide-react";
import { Button } from "@/components/ui/button";

function messageOf(error: unknown): string {
    if (error instanceof Error) return error.message;
    return typeof error === "string" ? error : String(error);
}

/** Page-level crash screen (adapted from near-nft-market ErrorBoundary). */
export function ErrorScreen({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
    const { t } = useTranslation();
    return (
        <div className="mx-auto my-16 grid max-w-md place-items-center gap-3 rounded-[var(--radius-widget)] border border-line bg-surface p-8 text-center">
            <AlertTriangle className="size-8 text-amber-fg" aria-hidden="true" />
            <h1 className="font-display text-lg font-semibold">{t("errors.crashTitle")}</h1>
            <p className="text-fg-2">{t("errors.crashBody")}</p>
            <div className="flex gap-2">
                {onRetry && <Button onClick={onRetry}>{t("widget.retry")}</Button>}
                <Button variant="secondary" onClick={() => window.location.reload()}>
                    {t("errors.reload")}
                </Button>
            </div>
            <details className="max-w-full">
                <summary className="cursor-pointer text-[12px] text-fg-3">{t("errors.details")}</summary>
                <pre className="mt-2 max-h-40 overflow-auto rounded-lg bg-bg p-3 text-left text-[11px] whitespace-pre-wrap text-fg-2">
                    {messageOf(error)}
                </pre>
            </details>
        </div>
    );
}

/** Router `errorElement`. */
export function PageError() {
    return <ErrorScreen error={useRouteError()} />;
}

export class PageBoundary extends React.Component<{ resetKey: unknown; children: React.ReactNode }, { error: unknown }> {
    state = { error: null as unknown };

    static getDerivedStateFromError(error: unknown) {
        return { error };
    }

    componentDidUpdate(prev: { resetKey: unknown }) {
        if (this.state.error !== null && prev.resetKey !== this.props.resetKey) this.setState({ error: null });
    }

    componentDidCatch(error: unknown, info: React.ErrorInfo) {
        console.error("page crashed:", error, info.componentStack);
    }

    render() {
        if (this.state.error !== null) return <ErrorScreen error={this.state.error} onRetry={() => this.setState({ error: null })} />;
        return this.props.children;
    }
}
