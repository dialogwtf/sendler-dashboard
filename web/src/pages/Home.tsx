import { useTranslation } from "react-i18next";
import { useLayoutChoice } from "@/layouts/choice";
import { PRESETS } from "@/layouts/presets";
import { useMediaQuery } from "@/lib/useMediaQuery";
import { WidgetGrid } from "@/ui/WidgetGrid";
import { PhoneHome } from "./home/PhoneHome";

/**
 * Desktop and tablet: the widget grid of the chosen preset layout («Мемы» / «Сеть»).
 * Phone: one widget per screen with tabs (plan step 12).
 */
export function HomePage() {
    const { t } = useTranslation();
    const layout = useLayoutChoice();
    const phone = useMediaQuery("(max-width: 767.98px)");
    if (phone) {
        return (
            <>
                <h1 className="sr-only">{t("app.name")}</h1>
                <PhoneHome />
            </>
        );
    }
    return (
        <>
            <h1 className="sr-only">{t(`nav.${layout}`)}</h1>
            <WidgetGrid key={layout} layoutId={layout} items={PRESETS[layout]} />
        </>
    );
}
