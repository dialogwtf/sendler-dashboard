import * as React from "react";
import { Link } from "react-router-dom";
import * as f from "@/lib/format";
import { useLang } from "@/lib/useLang";
import { cn } from "@/lib/utils";
import { AccountCell } from "@/ui/AccountCell";
import { Age } from "@/ui/Age";
import { BuyInBot } from "@/ui/BuyInBot";
import { Chip, CreatorTag, FactChip, PadChip, SmartTag, StagePill, Tag, VenueChip } from "@/ui/Chip";
import { Freshness } from "@/ui/Freshness";
import { LiveList } from "@/ui/LiveList";
import { BigNum, Num } from "@/ui/Num";
import { TokenCell } from "@/ui/TokenCell";
import { Widget } from "@/ui/Widget";

/**
 * /dev/ui — every building block in both themes, to compare with the «Основы» and «Состояния»
 * boards. Dev-only page: its strings are not translated on purpose (excluded from i18n lint).
 * The rows here use sample data; the real widgets come in step 5.
 */

const SWATCHES = [
    ["bg", "Фон"], ["surface", "Поверхность"], ["surface-2", "Поверхность 2"], ["line", "Линия"],
    ["fg", "Текст"], ["fg-2", "Текст 2"], ["fg-3", "Текст 3"], ["brand", "Акцент"],
    ["up", "Рост"], ["down", "Падение"], ["amber", "Янтарь"], ["neutral", "Нейтраль"],
] as const;

const IMPLICIT = "a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e9f0e";

interface SmartRowData {
    key: string;
    wallet: string;
    rank: number;
    roi: number;
    side: "buy" | "sell";
    token: string;
    symbol: string;
    near: number;
    cap: number;
    at: string;
}

const minutesAgo = (m: number) => new Date(Date.now() - m * 60_000).toISOString();

const SAMPLE: SmartRowData[] = [
    { key: "1", wallet: "quietwhale.near", rank: 3, roi: 4.12, side: "buy", token: "mossy.nearlytrade.near", symbol: "MOSSY", near: 42, cap: 41_200, at: minutesAgo(6) },
    { key: "2", wallet: "fastfox.tg", rank: 11, roi: 1.88, side: "buy", token: "zappy.v2-launchpad.justhoot.near", symbol: "ZAPPY", near: 15.5, cap: 22_000, at: minutesAgo(7) },
    { key: "3", wallet: "deepcuts.near", rank: 1, roi: 9.03, side: "sell", token: "koi.nearlytrade.near", symbol: "KOI", near: 120, cap: 19_800, at: minutesAgo(9) },
    { key: "4", wallet: IMPLICIT, rank: 27, roi: 0.97, side: "buy", token: "mossy.nearlytrade.near", symbol: "MOSSY", near: 8, cap: 38_500, at: minutesAgo(11) },
];

/** One row, three densities by the widget's own width (container queries, not the window). */
function SmartRow({ r }: { r: SmartRowData }) {
    const lang = useLang();
    const act = (
        <Num trend={r.side === "buy" ? "up" : "down"} strong>
            {r.side === "buy" ? "Купил" : "Продал"}
        </Num>
    );
    return (
        <div className="group relative grid h-12 grid-cols-[minmax(0,1fr)_auto] items-center gap-3 border-b border-row-line px-4 hover:bg-inset @sm:h-14 @min-[600px]:grid-cols-[minmax(130px,1.4fr)_64px_104px_76px_68px_72px]">
            {/* S: action, token, amount */}
            <div className="min-w-0 @min-[600px]:hidden">
                <div className="hidden min-w-0 items-center gap-1.5 @sm:flex">
                    <AccountCell id={r.wallet} />
                    <Chip>{`#${r.rank} · ROI ${f.signedPct(r.roi)}`}</Chip>
                </div>
                <div className="mt-0.5 flex items-center gap-1.5 text-[13px] @sm:text-[12px]">
                    {act} <TokenCell token={r.token} symbol={r.symbol} hideIcon />
                    <span className="hidden text-fg-3 @sm:inline">· <Age at={r.at} /></span>
                </div>
            </div>
            {/* L: table columns */}
            <span className="hidden min-w-0 overflow-hidden @min-[600px]:block"><AccountCell id={r.wallet} /></span>
            <span className="hidden @min-[600px]:block">{act}</span>
            <span className="hidden @min-[600px]:block"><TokenCell token={r.token} symbol={r.symbol} size={20} /></span>
            <Num strong className="text-right">{f.near(r.near, lang)}</Num>
            <span className="hidden text-right text-fg-2 @min-[600px]:block">{f.usd(r.cap, lang)}</span>
            <Num trend="up" className="hidden text-right @min-[600px]:block">{f.signedPct(r.roi)}</Num>
            <span className="absolute right-3 hidden group-hover:block group-focus-within:block @min-[600px]:hidden">
                <BuyInBot token={r.token} compact />
            </span>
        </div>
    );
}

function SmartWidget({ size, rows = SAMPLE }: { size: "S" | "M" | "L"; rows?: SmartRowData[] }) {
    return (
        <Widget
            title="Умные деньги"
            freshness={<Freshness updatedAt={Date.now()} polled compact={size === "S"} />}
            state="ready"
            live={false}
            footer={size === "S" ? undefined : { to: "/smart", label: "Все сделки умных денег →" }}
        >
            {size !== "S" && (
                <div className="hidden h-[34px] grid-cols-[minmax(130px,1.4fr)_64px_104px_76px_68px_72px] items-center gap-3 border-b border-row-line px-4 text-[11px] font-medium tracking-[0.05em] text-fg-3 uppercase @min-[600px]:grid">
                    <span>Кошелёк</span><span>Действие</span><span>Токен</span><span className="text-right">Сумма</span>
                    <span className="text-right">Капа</span><span className="text-right">ROI 30 д</span>
                </div>
            )}
            {rows.slice(0, size === "S" ? 3 : 4).map((r) => <SmartRow key={r.key} r={r} />)}
        </Widget>
    );
}

function Section({ title, children, className }: { title: string; children: React.ReactNode; className?: string }) {
    return (
        <section className={cn("rounded-[var(--radius-widget)] border border-line bg-surface p-5", className)}>
            <h2 className="mb-3.5 font-display text-[22px] font-bold">{title}</h2>
            {children}
        </section>
    );
}

function NumbersTable() {
    const lang = useLang();
    const rows: [string, string][] = [
        ["0.000000543", f.small(0.000000543)],
        ["54 012 $", f.usd(54_012, lang)],
        ["1 204 300", f.compact(1_204_300, lang)],
        ["6.21 раза", f.mult(6.21)],
        ["+512.4%", f.change(5.124, lang)],
        ["−70.1%", f.change(-0.701, lang)],
        ["сумма NEAR", f.near(42, lang)],
        ["implicit", f.account(IMPLICIT)],
        ["нет значения", f.mult(null)],
    ];
    return (
        <div className="grid grid-cols-2 gap-x-4 gap-y-2">
            {rows.map(([a, b]) => (
                <React.Fragment key={a}>
                    <span className="text-fg-3">{a}</span>
                    <Num value={a.startsWith("+") ? 1 : a.startsWith("−") ? -1 : undefined} className={a === "implicit" ? "font-mono text-[12px]" : ""}>
                        {b}
                    </Num>
                </React.Fragment>
            ))}
            <span className="text-fg-3">давность</span>
            <span className="flex gap-2 text-fg-2">
                <Age at={new Date(Date.now() - 12_000).toISOString()} /> · <Age at={minutesAgo(6)} /> ·{" "}
                <Age at={minutesAgo(300)} /> · <Age at={minutesAgo(3 * 1440)} />
            </span>
        </div>
    );
}

function NewEventDemo() {
    const [rows, setRows] = React.useState(SAMPLE);
    const n = React.useRef(100);
    const add = () => {
        n.current += 1;
        const src = SAMPLE[n.current % SAMPLE.length];
        setRows((r) => [{ ...src, key: String(n.current), at: new Date().toISOString() }, ...r].slice(0, 40));
    };
    return (
        <div className="grid gap-5 lg:grid-cols-[400px_minmax(0,1fr)]">
            <Widget title="Лента (демо)" freshness={<Freshness updatedAt={Date.now()} polled />} state="ready" live={false} height={280}>
                <LiveList items={rows} getKey={(r) => r.key} renderRow={(r) => <SmartRow r={r} />} />
            </Widget>
            <div className="space-y-2 text-fg-2">
                <button type="button" onClick={add} className="h-9 rounded-[9px] bg-brand px-4 font-semibold text-brand-fg">
                    + Новая строка
                </button>
                <p>Вверху списка — строка въезжает и 3 с подсвечена. Прокрутите список вниз и добавьте ещё — список не дёргается, в шапке появляется «↑ N новых».</p>
            </div>
        </div>
    );
}

function Board() {
    return (
        <div className="grid gap-5 lg:grid-cols-3">
            <Section title="Цвет" className="lg:col-span-2">
                <div className="grid grid-cols-2 gap-x-5 gap-y-3.5 sm:grid-cols-4">
                    {SWATCHES.map(([v, name]) => (
                        <div key={v} className="grid grid-cols-[44px_minmax(0,1fr)] items-center gap-2.5">
                            <span className="size-11 rounded-[10px] border border-line" style={{ background: `var(--${v})` }} />
                            <div className="min-w-0">
                                <b>{name}</b>
                                <div className="font-mono text-[12px] text-fg-3">--{v}</div>
                            </div>
                        </div>
                    ))}
                </div>
            </Section>

            <Section title="Шрифт">
                <div className="font-display text-[32px] leading-tight font-bold">$MOSSY 54.0k</div>
                <div className="mt-1 mb-3.5 text-[12px] text-fg-2">Space Grotesk — заголовки и крупные числа</div>
                <div className="text-[14px] font-semibold">Лента запусков · 1 204 холдера</div>
                <div className="mt-1 mb-3.5 text-[12px] text-fg-2">Inter — интерфейс, цифры моноширинные</div>
                <div className="font-mono text-[13px]">quietwhale.near · a1b2…9f0e</div>
                <div className="mt-1 text-[12px] text-fg-2">JetBrains Mono — аккаунты, адреса, хеши</div>
                <BigNum className="mt-3 block">{f.usd(96_200, "ru")}</BigNum>
            </Section>

            <Section title="Числа">
                <NumbersTable />
            </Section>

            <Section title="Метки">
                <div className="flex flex-col gap-3.5">
                    <div>
                        <div className="mb-1.5 text-[11px] text-fg-3">Лаунчпады и площадки</div>
                        <div className="flex flex-wrap gap-1">
                            <PadChip pad="nearly" /> <PadChip pad="justhoot" /> <PadChip pad="intear" /> <PadChip pad="newpad" />
                            <VenueChip venue="Rhea DCL" /> <VenueChip venue="Rhea" />
                        </div>
                    </div>
                    <div>
                        <div className="mb-1.5 text-[11px] text-fg-3">Статус запуска</div>
                        <div className="flex gap-1"><StagePill stage="started" /> <StagePill stage="ready" /> <StagePill stage="graduated" /></div>
                    </div>
                    <div>
                        <div className="mb-1.5 text-[11px] text-fg-3">Кто это</div>
                        <div className="flex flex-wrap gap-1"><SmartTag rank={3} /> <CreatorTag /> <Tag>пул DCL</Tag> <Tag>биржа</Tag></div>
                    </div>
                    <div>
                        <div className="mb-1.5 text-[11px] text-fg-3">Свежесть данных</div>
                        <div className="flex items-center gap-2">
                            <Freshness updatedAt={Date.now()} polled />
                            <Freshness updatedAt={Date.now() - 120_000} polled />
                        </div>
                    </div>
                    <div>
                        <div className="mb-1.5 text-[11px] text-fg-3">Факты о создателе</div>
                        <div className="flex flex-wrap gap-1">
                            <FactChip tone="down">▼ 3 слива из 4</FactChip> <FactChip tone="amber">связан с блок-листом</FactChip>
                            <FactChip>первый запуск</FactChip>
                        </div>
                    </div>
                </div>
            </Section>

            <Section title="Токен и аккаунт">
                <div className="flex flex-col gap-3">
                    <TokenCell token="mossy.nearlytrade.near" symbol="MOSSY" link />
                    <TokenCell token="sinu-8.smithii.near" symbol="SINU" />
                    <TokenCell token="long.near" symbol="SUPERLONGTICKERNAME" />
                    <AccountCell id="v2.ref-finance.near" label={{ label: "Rhea", kind: "dex" }} />
                    <AccountCell id={IMPLICIT} label={{ label: "Крупный узел", kind: "hub" }} />
                    <BuyInBot token="sinu-8.smithii.near" />
                </div>
            </Section>
        </div>
    );
}

function States() {
    return (
        <>
            <h2 className="mt-11 mb-1 font-display text-[24px] font-bold">Размеры: один виджет — три плотности</h2>
            <p className="mb-5 text-fg-2">Виджет решает по своей ширине: S — главное, M — по умолчанию, L — дополнительные столбцы.</p>
            <div className="grid items-start gap-5 xl:grid-cols-[300px_400px_minmax(0,1fr)]">
                <SmartWidget size="S" />
                <SmartWidget size="M" />
                <SmartWidget size="L" />
            </div>

            <h2 className="mt-11 mb-5 font-display text-[24px] font-bold">Состояния</h2>
            <div className="grid items-start gap-5 md:grid-cols-2 xl:grid-cols-3">
                <Widget title="Умные деньги" state="loading" live={false} height={252} />
                <Widget
                    title="Лента запусков"
                    freshness={<Freshness updatedAt={Date.now()} polled />}
                    state="empty"
                    live={false}
                    height={252}
                    empty={{
                        title: "За час запусков не было",
                        hint: "Фильтр: только Intear. Новый запуск появится здесь сам.",
                        action: { label: "Показать все лаунчпады", onClick: () => {} },
                    }}
                />
                <Widget
                    title="Умные деньги"
                    freshness={<Freshness updatedAt={Date.now() - 120_000} polled />}
                    state="ready"
                    forceOffline
                    height={252}
                >
                    {SAMPLE.slice(0, 3).map((r) => <SmartRow key={r.key} r={r} />)}
                </Widget>
                <Widget title="Топ токенов" state="error" error={{ code: "http_500" }} onRetry={() => {}} live={false} height={252} />
            </div>

            <h2 className="mt-11 mb-5 font-display text-[24px] font-bold">Новое событие</h2>
            <NewEventDemo />
        </>
    );
}

export default function DevUiPage() {
    return (
        <div className="space-y-6">
            <p className="text-fg-2">
                Витрина дизайн-системы (шаг 2). Сравнивать с досками «Основы» и «Состояния». <Link className="text-brand-text" to="/dev/live">Живые события →</Link>
            </p>
            {(["dark", "light"] as const).map((theme) => (
                <div key={theme} data-theme={theme} className="rounded-2xl bg-bg p-4 text-fg ring-1 ring-line sm:p-6">
                    <div className="mb-4 font-mono text-[12px] text-fg-3">data-theme="{theme}"</div>
                    <Board />
                    <States />
                </div>
            ))}
        </div>
    );
}
