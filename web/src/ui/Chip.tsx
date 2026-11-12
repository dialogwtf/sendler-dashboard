import * as React from "react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import { clean } from "@/lib/text";

/** Launchpad / venue chip: «Nearly», «Rhea DCL». Unknown values are shown as they come. */
export function Chip({ className, children }: { className?: string; children: React.ReactNode }) {
    return (
        <span
            className={cn(
                "inline-flex items-center gap-1 whitespace-nowrap rounded-[5px] bg-surface-2 px-1.5 py-0.5 text-[11px] font-medium leading-tight text-fg-2",
                className,
            )}
        >
            {children}
        </span>
    );
}

const PAD_NAMES: Record<string, string> = { nearly: "Nearly", justhoot: "JustHoot", intear: "Intear" };

export function PadChip({ pad }: { pad: string }) {
    return <Chip>{PAD_NAMES[pad] ?? clean(pad, 20)}</Chip>;
}

/** Venue as shown: «Rhea» of the API is Rhea v1 (classic pools); unknown ones as they come. */
export function venueName(venue: string): string {
    return venue === "Rhea" ? "Rhea v1" : clean(venue, 20);
}

export function VenueChip({ venue }: { venue: string | null | undefined }) {
    if (!venue) return null;
    return <Chip>{venueName(venue)}</Chip>;
}

type TagTone = "brand" | "amber" | "neutral" | "down";

const TAG_TONES: Record<TagTone, string> = {
    brand: "bg-brand-soft text-brand-soft-fg",
    amber: "bg-amber-soft text-amber-fg",
    neutral: "bg-surface-2 text-fg-2",
    down: "bg-down-soft text-down-soft-fg",
};

/** Who-is-it tag: «умный #3», «создатель», «биржа», «пул DCL». */
export function Tag({ tone = "neutral", className, children }: { tone?: TagTone; className?: string; children: React.ReactNode }) {
    return (
        <span
            className={cn(
                "inline-flex items-center whitespace-nowrap rounded-[5px] px-1.5 py-px text-[10.5px] font-semibold leading-tight",
                TAG_TONES[tone],
                className,
            )}
        >
            {children}
        </span>
    );
}

/** «умный #3»; a rank the page does not know (0) — just «умный», never «#0». */
export function SmartTag({ rank }: { rank: number }) {
    const { t } = useTranslation();
    return <Tag tone="brand">{rank > 0 ? t("tags.smart", { rank }) : t("tags.smartPlain")}</Tag>;
}

export function CreatorTag() {
    const { t } = useTranslation();
    return <Tag tone="amber">{t("tags.creator")}</Tag>;
}

/** Launch stage pill: «Старт» highlighted, the rest neutral. Unknown stages as they come. */
export function StagePill({ stage }: { stage: string }) {
    const { t } = useTranslation();
    const known = ["started", "funding", "live", "ready"].includes(stage);
    return (
        <span
            className={cn(
                "inline-flex whitespace-nowrap rounded-full px-2.5 py-[3px] text-[11px] font-semibold leading-tight",
                stage === "started" ? "bg-brand-soft text-brand-soft-fg" : "bg-surface-2 text-fg-2",
            )}
        >
            {known ? t(`stage.${stage}`) : clean(stage, 16)}
        </span>
    );
}

/** A fact about a creator, never a verdict: «▼ 3 слива из 4», «связан с блок-листом». */
export function FactChip({ tone = "neutral", children }: { tone?: "neutral" | "down" | "amber"; children: React.ReactNode }) {
    return (
        <Chip
            className={cn(tone === "down" && "bg-down-soft text-down-soft-fg", tone === "amber" && "bg-amber-soft text-amber-fg")}
        >
            {children}
        </Chip>
    );
}
