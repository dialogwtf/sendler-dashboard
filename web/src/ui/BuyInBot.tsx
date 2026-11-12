import { useTranslation } from "react-i18next";
import { Send } from "lucide-react";
import { buyInBotUrl } from "@/lib/botLinks";
import { openTelegramUrl } from "@/lib/telegram";
import { cn } from "@/lib/utils";

/**
 * «Купить в боте»: a real link (works with JS off, middle-click, long-press), opened through
 * Telegram's own API inside the Mini App. `compact` = icon button for row hover actions.
 */
export function BuyInBot({
    token,
    code,
    compact,
    className,
}: {
    token: string;
    /** bot_code from the API when the row has it (same value as computed locally). */
    code?: string | null;
    compact?: boolean;
    className?: string;
}) {
    const { t } = useTranslation();
    const href = buyInBotUrl(token, code);
    return (
        <a
            href={href}
            target="_blank"
            rel="noopener noreferrer"
            onClick={(e) => {
                e.stopPropagation();
                if (openTelegramUrl(href)) e.preventDefault();
            }}
            className={cn(
                "inline-flex shrink-0 items-center justify-center gap-2 rounded-[9px] bg-brand font-semibold text-brand-fg hover:brightness-110",
                compact ? "size-8" : "h-9 px-3.5 text-[13px]",
                className,
            )}
            title={compact ? t("actions.buyInBot") : undefined}
        >
            <Send className={compact ? "size-4" : "size-[18px]"} aria-hidden="true" />
            {compact ? <span className="sr-only">{t("actions.buyInBot")}</span> : t("actions.buyInBot")}
        </a>
    );
}
