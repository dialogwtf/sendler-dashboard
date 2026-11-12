import * as React from "react";
import { cn } from "@/lib/utils";
import { Check, Copy } from "lucide-react";

type Props = {
    value: string;
    title?: string;
    className?: string;
};

export function CopyIconButton({ value, title = "Copy", className }: Props) {
    const [copied, setCopied] = React.useState(false);

    const handleCopy = async () => {
        try {
            await navigator.clipboard.writeText(value);
            setCopied(true);
            window.setTimeout(() => setCopied(false), 1200);
        } catch {
            // no-op
        }
    };

    return (
        <button
            type="button"
            onClick={handleCopy}
            title={title}
            className={cn(
                "inline-flex h-8 w-8 items-center justify-center rounded-lg border border-line bg-surface-2 hover:bg-surface-2 transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/40",
                className
            )}
            aria-live="polite"
        >
            {copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
            <span className="sr-only">{copied ? "Copied" : "Copy to clipboard"}</span>
        </button>
    );
}
