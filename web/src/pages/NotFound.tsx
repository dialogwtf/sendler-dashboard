import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

export function NotFoundPage() {
    const { t } = useTranslation();
    return (
        <div className="mx-auto my-20 grid max-w-md place-items-center gap-3 text-center">
            <span className="font-display text-[56px] leading-none font-bold text-fg-3">404</span>
            <h1 className="font-display text-[22px] font-semibold">{t("pages.notFound")}</h1>
            <p className="text-fg-2">{t("pages.notFoundHint")}</p>
            <Link to="/" className="mt-2 inline-flex h-10 items-center rounded-[9px] bg-brand px-4 font-semibold text-brand-fg hover:brightness-110">
                {t("nav.home")}
            </Link>
        </div>
    );
}
