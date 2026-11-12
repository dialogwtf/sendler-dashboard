import * as React from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { api } from "@/api/client";
import { TokenIcon } from "@/components/common/TokenIcon";
import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from "@/components/ui/command";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { useTokenMeta } from "@/api/batch";
import { near } from "@/lib/format";
import { useLang } from "@/lib/useLang";
import { clean, tickerOf } from "@/lib/text";
import { accountPath, useLabelText } from "@/ui/AccountCell";
import { SmartTag } from "@/ui/Chip";
import { tokenPath } from "@/ui/TokenCell";
import type { SearchAccount, SearchToken } from "@/api/types";

/**
 * The search dialog — a lazy chunk (cmdk, the dialog) loaded on the first Ctrl K / tap, so the
 * first screen does not pay for it (plan step 10).
 */

const MIN_CHARS = 2;
const DEBOUNCE_MS = 250;
/** Looks like a NEAR account / contract: named with a dot, implicit hex or eth-implicit. */
const ADDRESS = /^([a-z0-9_-]+\.)+[a-z0-9_-]+$|^[0-9a-f]{64}$|^0x[0-9a-f]{40}$/;

function useDebounced<T>(value: T, ms: number): T {
    const [v, set] = React.useState(value);
    React.useEffect(() => {
        const t = setTimeout(() => set(value), ms);
        return () => clearTimeout(t);
    }, [value, ms]);
    return v;
}

export default function SearchDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
    const { t } = useTranslation();
    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="top-[max(12vh,calc(var(--safe-top)+12px))] translate-y-0 overflow-hidden p-0 sm:max-w-xl" showCloseButton={false}>
                <DialogTitle className="sr-only">{t("search.open")}</DialogTitle>
                <DialogDescription className="sr-only">{t("search.placeholder")}</DialogDescription>
                {open && <SearchPanel onDone={() => onOpenChange(false)} />}
            </DialogContent>
        </Dialog>
    );
}

function SearchPanel({ onDone }: { onDone: () => void }) {
    const { t } = useTranslation();
    const navigate = useNavigate();
    const [q, setQ] = React.useState("");
    const query = useDebounced(q.trim(), DEBOUNCE_MS);
    const enabled = query.length >= MIN_CHARS;
    const res = useQuery({
        queryKey: ["search", query],
        queryFn: ({ signal }) => api.search(query, 10, signal),
        enabled,
        staleTime: 30_000,
        placeholderData: keepPreviousData,
    });
    const go = (path: string) => {
        onDone();
        navigate(path);
    };
    const exact = ADDRESS.test(q.trim().toLowerCase()) ? q.trim().toLowerCase() : null;
    const tokens = enabled ? res.data?.tokens ?? [] : [];
    const accounts = enabled ? res.data?.accounts ?? [] : [];
    // An exact address the API already returned needs no separate «open» entry.
    const exactToken = exact && !tokens.some((x) => x.token === exact) ? exact : null;
    const exactAccount = exact && !accounts.some((x) => x.account === exact) ? exact : null;

    return (
        <Command shouldFilter={false} className="[&_[cmdk-group-heading]]:px-3 [&_[cmdk-group-heading]]:py-1.5 [&_[cmdk-group-heading]]:text-[11px] [&_[cmdk-group-heading]]:font-medium [&_[cmdk-group-heading]]:text-fg-3">
            <CommandInput value={q} onValueChange={setQ} placeholder={t("search.placeholder")} className="h-12" />
            <CommandList className="max-h-[60vh]">
                {enabled && !res.isFetching && !tokens.length && !accounts.length && !exact && (
                    <CommandEmpty>{t("search.empty")}</CommandEmpty>
                )}
                {!enabled && !exact && <div className="px-4 py-6 text-center text-[12px] text-fg-3">{t("search.hint")}</div>}
                {(exactToken || exactAccount) && (
                    <CommandGroup heading={t("search.address")}>
                        {exactToken && (
                            <CommandItem value={`tok:${exactToken}`} onSelect={() => go(tokenPath(exactToken))}>
                                {t("search.openToken")} <span className="ml-1 truncate font-mono text-[12px]">{exactToken}</span>
                            </CommandItem>
                        )}
                        {exactAccount && (
                            <CommandItem value={`acc:${exactAccount}`} onSelect={() => go(accountPath(exactAccount))}>
                                {t("search.openAccount")} <span className="ml-1 truncate font-mono text-[12px]">{exactAccount}</span>
                            </CommandItem>
                        )}
                    </CommandGroup>
                )}
                {tokens.length > 0 && (
                    <CommandGroup heading={t("search.tokens")}>
                        {tokens.map((tk) => (
                            <TokenResult key={tk.token} tk={tk} onSelect={() => go(tokenPath(tk.token))} />
                        ))}
                    </CommandGroup>
                )}
                {accounts.length > 0 && (
                    <CommandGroup heading={t("search.accounts")}>
                        {accounts.map((a) => (
                            <AccountResult key={a.account} a={a} onSelect={() => go(accountPath(a.account))} />
                        ))}
                    </CommandGroup>
                )}
            </CommandList>
        </Command>
    );
}

function TokenResult({ tk, onSelect }: { tk: SearchToken; onSelect: () => void }) {
    const lang = useLang();
    const meta = useTokenMeta(tk.token);
    const ticker = tickerOf(tk.symbol, tk.token);
    return (
        <CommandItem value={`t:${tk.token}`} onSelect={onSelect} className="gap-2.5 py-2">
            <TokenIcon symbol={ticker} icon={meta.data?.icon} seed={tk.token} size={24} />
            <span className="min-w-0 flex-1">
                <b className="font-semibold">{`$${ticker}`}</b> <span className="text-fg-2">{clean(tk.name, 40)}</span>
                <span className="block truncate font-mono text-[11px] text-fg-3">{tk.token}</span>
            </span>
            {tk.volume_24h_near > 0 && <span className="text-[12px] text-fg-2 tabular-nums">{near(tk.volume_24h_near, lang)}</span>}
        </CommandItem>
    );
}

function AccountResult({ a, onSelect }: { a: SearchAccount; onSelect: () => void }) {
    const { t } = useTranslation();
    const label = useLabelText(a.label);
    return (
        <CommandItem value={`a:${a.account}`} onSelect={onSelect} className="gap-2.5 py-2">
            <span className="min-w-0 flex-1 truncate font-mono text-[12px]">{a.account}</span>
            {label && <span className="text-[11px] text-fg-2">{label}</span>}
            {a.smart && <SmartTag rank={a.smart.rank} />}
            {a.launches > 0 && <span className="text-[11px] text-fg-3">{t("search.launches", { count: a.launches })}</span>}
        </CommandItem>
    );
}
