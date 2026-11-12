import { useTranslation } from "react-i18next";

/** Stand-in for pages built in later steps of the plan. */
export function Placeholder({ title, detail }: { title: string; detail?: string }) {
    const { t } = useTranslation();
    return (
        <div className="rounded-[var(--radius-widget)] border border-line bg-surface p-6">
            <h1 className="font-display text-[26px] font-bold break-all">{title}</h1>
            {detail && <p className="mt-1 font-mono text-[12px] break-all text-fg-2">{detail}</p>}
            <p className="mt-3 text-fg-3">{t("pages.comingStep")}</p>
        </div>
    );
}
