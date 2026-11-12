import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import type { Label } from "@/api/types";
import { account as short } from "@/lib/format";
import { clean } from "@/lib/text";
import { cn } from "@/lib/utils";
import { Tag } from "./Chip";

export function accountPath(id: string) {
    return `/account/${encodeURIComponent(id)}`;
}

/** Old rows (before the 2026-11-09 indexer deploy) carry Russian descriptions instead of names. */
const HAS_CYRILLIC = /[Ѐ-ӿ]/; // i18n-ok — matches old Russian labels, not shown text

/** Kinds whose proper name needs the kind spelled out: {label: "Nearly", kind: "token"} → «токен Nearly». */
const NAMED_KINDS = new Set(["token"]);

/**
 * Label text for the viewer: the proper name from the API, worded with the kind where the name
 * alone is ambiguous; no name → the kind from our dictionaries; wallets and implicit — nothing.
 */
export function useLabelText(label: Label | null | undefined): string | null {
    const { t } = useTranslation();
    if (!label) return null;
    const name = label.label && !HAS_CYRILLIC.test(label.label) ? clean(label.label, 32) : null;
    if (name) return NAMED_KINDS.has(label.kind) ? t(`labelNamed.${label.kind}`, { name }) : name;
    if (label.kind === "implicit" || label.kind === "wallet") return null;
    return t(`labelKind.${label.kind}`, { defaultValue: clean(label.kind, 20) });
}

/**
 * Account in a row: monospace, implicit ones shortened (a1b2…9f0e, full in the tooltip), and
 * its label as a tag. `link` → the account page.
 */
export function AccountCell({
    id,
    label,
    link = true,
    className,
}: {
    id: string | null | undefined;
    label?: Label | null;
    link?: boolean;
    className?: string;
}) {
    const text = useLabelText(label);
    if (!id) return <span className={className}>—</span>;
    const safe = clean(id, 80);
    const shown = short(safe);
    const body = (
        <span className="truncate font-mono text-[12px]" translate="no">
            {shown}
        </span>
    );
    return (
        <span className={cn("inline-flex min-w-0 items-center gap-1.5", className)} title={shown !== id ? id : undefined}>
            {link ? (
                <Link to={accountPath(id)} className="relative z-[1] min-w-0 truncate hover:text-brand-text">
                    {body}
                </Link>
            ) : (
                body
            )}
            {text && <Tag>{text}</Tag>}
        </span>
    );
}
