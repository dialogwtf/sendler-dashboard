import { secondsSince } from "@/lib/clock";
import { age } from "@/lib/format";
import { useLang } from "@/lib/useLang";
import { useNow } from "@/lib/useNow";
import { cn } from "@/lib/utils";

/**
 * «12 с · 6 мин · 5 ч · 3 дн», counted on the server's clock and updated every second;
 * the local date and time in the tooltip.
 */
export function Age({ at, className }: { at: string | number | null | undefined; className?: string }) {
    const lang = useLang();
    const now = useNow();
    const s = secondsSince(at, now);
    if (s === null) return <span className={className}>—</span>;
    const date = typeof at === "number" ? new Date(at * 1000) : new Date(at as string);
    return (
        <time dateTime={date.toISOString()} title={date.toLocaleString(lang)} className={cn("whitespace-nowrap tabular-nums", className)}>
            {age(s, lang)}
        </time>
    );
}
