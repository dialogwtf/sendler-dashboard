import * as React from "react";
import { useTranslation } from "react-i18next";
import { Loader2 } from "lucide-react";
import type { Older } from "@/api/paging";
import { Button } from "@/components/ui/button";

/**
 * The end of a full list: older rows load by themselves when the end comes into view (400 px
 * ahead), and by the button — for the keyboard, and after an error.
 */
export function MoreRows({ older, hint }: { older: Pick<Older<unknown>, "hasMore" | "loading" | "error" | "loadMore">; hint?: React.ReactNode }) {
    const { t } = useTranslation();
    const sentinel = React.useRef<HTMLDivElement>(null);
    const { hasMore, loading, error, loadMore } = older;
    const auto = hasMore && !loading && !error;

    React.useEffect(() => {
        const el = sentinel.current;
        if (!el || !auto || typeof IntersectionObserver === "undefined") return;
        const io = new IntersectionObserver((entries) => {
            if (entries.some((e) => e.isIntersecting)) loadMore();
        }, { rootMargin: "400px 0px" });
        io.observe(el);
        return () => io.disconnect();
    }, [auto, loadMore]);

    return (
        <div ref={sentinel} className="flex min-h-11 flex-wrap items-center justify-center gap-x-3 gap-y-1 px-4 py-3 text-[12px] text-fg-3">
            {loading ? (
                <span className="inline-flex items-center gap-2" role="status">
                    <Loader2 className="size-4 animate-spin" aria-hidden="true" />
                    {t("list.loading")}
                </span>
            ) : error ? (
                <>
                    <span>{t("list.olderError")}</span>
                    <Button variant="secondary" size="sm" className="pointer-coarse:h-11 pointer-coarse:px-4" onClick={loadMore}>{t("widget.retry")}</Button>
                </>
            ) : hasMore ? (
                <Button variant="secondary" size="sm" className="pointer-coarse:h-11 pointer-coarse:px-4" onClick={loadMore}>{t("list.more")}</Button>
            ) : (
                <span>{hint ?? t("list.end")}</span>
            )}
        </div>
    );
}
