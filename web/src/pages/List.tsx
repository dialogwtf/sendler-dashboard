import * as React from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import { useUrlSettings } from "@/lib/urlSettings";
import { WidgetBoundary } from "@/ui/Widget";
import { REGISTRY } from "@/widgets/registry";

export type ListKind = "launches" | "smart" | "moves" | "new";

/**
 * /launches, /smart, /moves, /new — the widget at full width (UI spec «Структура»): its
 * settings in the address (`?pad=nearly&min_cap=50000`), the page scrolls, older rows load at
 * the end. Footer links «Все … →» and «Развернуть» of the home widgets lead here.
 */
export default function ListPage({ kind }: { kind: ListKind }) {
    return <FullList key={kind} kind={kind} />;
}

function FullList({ kind }: { kind: ListKind }) {
    const { t } = useTranslation();
    const def = REGISTRY[kind];
    const [settings, setSettings] = useUrlSettings(def.defaults, def.choices);
    const title = t(def.title);
    const C = def.Component;

    React.useEffect(() => {
        document.title = `${title} — Sendler Dash`;
        return () => { document.title = "Sendler Dash"; };
    }, [title]);

    return (
        <div className="mx-auto max-w-[1100px]">
            {/* phone: the bottom tabs lead home */}
            <div className="max-md:hidden"><BackLink /></div>
            <h1 className="sr-only">{title}</h1>
            <WidgetBoundary title={title}>
                <C settings={settings} setSettings={setSettings} size="L" full />
            </WidgetBoundary>
        </div>
    );
}

export function BackLink() {
    const { t } = useTranslation();
    return (
        <div className="mb-3 flex items-center">
            <Link
                to="/"
                className="inline-flex min-h-9 items-center gap-1.5 rounded-lg pr-2 text-[13px] text-fg-2 hover:text-fg max-md:min-h-11"
            >
                <ArrowLeft className="size-4" aria-hidden="true" />
                {t("list.back")}
            </Link>
        </div>
    );
}
