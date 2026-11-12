import * as React from "react";
import { useTranslation } from "react-i18next";
import { Check, Copy } from "lucide-react";

/** Copies an address; a check mark for 1.2 s. 44 px on the phone. */
export function CopyButton({ value, label }: { value: string; label: string }) {
    const { t } = useTranslation();
    const [done, setDone] = React.useState(false);
    return (
        <button
            type="button"
            onClick={() => {
                void navigator.clipboard?.writeText(value).then(() => {
                    setDone(true);
                    setTimeout(() => setDone(false), 1200);
                });
            }}
            className="grid size-8 place-items-center rounded-lg text-fg-2 hover:bg-surface-2 hover:text-fg max-md:size-11"
            aria-label={done ? t("actions.copied") : label}
        >
            {done ? <Check className="size-4" aria-hidden="true" /> : <Copy className="size-4" aria-hidden="true" />}
        </button>
    );
}
