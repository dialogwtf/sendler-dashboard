import { useTranslation } from "react-i18next";
import { useLiveState } from "@/app/live";
import { age } from "@/lib/format";
import { useLang } from "@/lib/useLang";
import { useNow } from "@/lib/useNow";

/**
 * Freshness in a widget header (anatomy item 2): LIVE while the socket is up, otherwise the age
 * of what is on screen — «данные 2 мин назад». Widgets without a channel pass `polled` and show
 * LIVE while their data is younger than 2.5 poll intervals.
 */
export function Freshness({
    updatedAt,
    polled,
    pollMs = 10_000,
    compact,
}: {
    /** dataUpdatedAt of the widget's query, ms (local clock). */
    updatedAt: number;
    polled?: boolean;
    pollMs?: number;
    /** S size: just the dot. */
    compact?: boolean;
}) {
    const { t } = useTranslation();
    const lang = useLang();
    useNow(); // re-render every second
    const { status } = useLiveState();
    const dataAge = updatedAt ? Math.max(0, (Date.now() - updatedAt) / 1000) : null;
    const live = polled ? dataAge !== null && dataAge * 1000 < pollMs * 2.5 : status === "open";

    if (live) {
        return (
            <span className="inline-flex items-center gap-1.5 text-[11px] font-semibold text-up-fg">
                <span
                    aria-hidden="true"
                    className="size-[7px] rounded-full bg-up shadow-[0_0_0_3px_color-mix(in_srgb,var(--up)_18%,transparent)]"
                />
                {compact ? <span className="sr-only">{t("fresh.live")}</span> : "LIVE"}
            </span>
        );
    }
    // Still connecting (first seconds of a visit): no badge rather than «data 3 s ago».
    if (dataAge === null || (!polled && (status === "idle" || status === "connecting"))) return null;
    return (
        <span className="inline-flex whitespace-nowrap rounded-md bg-surface-2 px-[7px] py-[3px] text-[11px] font-semibold text-fg-2">
            {t("fresh.ago", { age: age(dataAge, lang) })}
        </span>
    );
}
