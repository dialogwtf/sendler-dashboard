import { useTranslation } from "react-i18next";
import { langOf } from "@/app/i18n";
import type { Lang } from "./format";

/** The UI language as format.ts takes it. */
export function useLang(): Lang {
    const { i18n } = useTranslation();
    return langOf(i18n.resolvedLanguage ?? i18n.language);
}
