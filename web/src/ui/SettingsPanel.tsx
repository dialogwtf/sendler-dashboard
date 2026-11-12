import * as React from "react";
import { useTranslation } from "react-i18next";
import { SlidersHorizontal } from "lucide-react";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { useMediaQuery } from "@/lib/useMediaQuery";
import { cn } from "@/lib/utils";
import type { SettingField, SettingsSchema } from "@/widgets/types";

/**
 * Widget settings (UI spec «Режим правки», item 4): a popover by the widget on desktop, a bottom
 * sheet on the phone, drawn from the widget's schema — switches, checkboxes, chips. No technical
 * knobs (poll interval, sources) ever go here.
 */
export function SettingsButton({
    title,
    schema,
    values,
    onChange,
}: {
    title: string;
    schema: SettingsSchema;
    values: Record<string, unknown>;
    onChange: (patch: Record<string, unknown>) => void;
}) {
    const { t } = useTranslation();
    const phone = !useMediaQuery("(min-width: 768px)");
    const [open, setOpen] = React.useState(false);
    const trigger = (
        <button
            type="button"
            className="grid size-9 shrink-0 place-items-center rounded-lg text-fg-2 hover:bg-surface-2 hover:text-fg max-md:size-11"
            aria-label={t("widget.settingsOf", { title })}
            onClick={phone ? () => setOpen(true) : undefined}
        >
            <SlidersHorizontal className="size-[18px]" aria-hidden="true" />
        </button>
    );
    const form = <SettingsForm schema={schema} values={values} onChange={onChange} />;

    if (phone) {
        return (
            <>
                {trigger}
                <Sheet open={open} onOpenChange={setOpen}>
                    <SheetContent side="bottom">
                        <SheetHeader>
                            <SheetTitle>{t("widget.settingsOf", { title })}</SheetTitle>
                            <SheetDescription className="sr-only">{t("widget.settingsHint")}</SheetDescription>
                        </SheetHeader>
                        <div className="overflow-y-auto px-4 pb-6">{form}</div>
                    </SheetContent>
                </Sheet>
            </>
        );
    }
    return (
        <Popover open={open} onOpenChange={setOpen}>
            <PopoverTrigger asChild>{trigger}</PopoverTrigger>
            <PopoverContent align="end" className="w-80">
                <div className="mb-3 text-[14px] font-semibold">{t("widget.settingsOf", { title })}</div>
                {form}
            </PopoverContent>
        </Popover>
    );
}

export function SettingsForm({
    schema,
    values,
    onChange,
}: {
    schema: SettingsSchema;
    values: Record<string, unknown>;
    onChange: (patch: Record<string, unknown>) => void;
}) {
    return (
        <div className="flex flex-col gap-4">
            {schema.map((f) => (
                <Field key={f.key} field={f} value={values[f.key]} onChange={(v) => onChange({ [f.key]: v })} />
            ))}
        </div>
    );
}

function Field({ field, value, onChange }: { field: SettingField; value: unknown; onChange: (v: unknown) => void }) {
    const id = React.useId();
    if (field.kind === "toggle") {
        return (
            <label htmlFor={id} className="flex min-h-11 cursor-pointer items-center gap-3">
                <span className="flex-1">{field.label}</span>
                <input
                    id={id}
                    type="checkbox"
                    role="switch"
                    checked={!!value}
                    onChange={(e) => onChange(e.target.checked)}
                    className="size-4 accent-[var(--brand)]"
                />
            </label>
        );
    }
    if (field.kind === "checks") {
        const set = new Set(Array.isArray(value) ? (value as string[]) : []);
        return (
            <fieldset>
                <legend className="mb-1.5 text-[11px] font-medium text-fg-3">{field.label}</legend>
                <div className="flex flex-col">
                    {field.options.map((o) => (
                        <label key={o.value} className="flex min-h-10 cursor-pointer items-center gap-3">
                            <input
                                type="checkbox"
                                checked={set.has(o.value)}
                                onChange={(e) => {
                                    const next = new Set(set);
                                    if (e.target.checked) next.add(o.value);
                                    else next.delete(o.value);
                                    onChange([...next]);
                                }}
                                className="size-4 accent-[var(--brand)]"
                            />
                            {o.label}
                        </label>
                    ))}
                </div>
            </fieldset>
        );
    }
    // segment / chips: one choice of a few
    return (
        <fieldset>
            <legend className="mb-1.5 text-[11px] font-medium text-fg-3">{field.label}</legend>
            <div className="flex flex-wrap gap-1.5" role="radiogroup">
                {field.options.map((o) => {
                    const on = String(value) === o.value;
                    return (
                        <button
                            key={o.value}
                            type="button"
                            role="radio"
                            aria-checked={on}
                            onClick={() => onChange(o.value)}
                            className={cn(
                                "h-8 rounded-full px-3 text-[12px] font-semibold max-md:h-11",
                                on ? "bg-fg text-bg" : "bg-surface-2 text-fg-2 hover:text-fg",
                            )}
                        >
                            {o.label}
                        </button>
                    );
                })}
            </div>
        </fieldset>
    );
}
