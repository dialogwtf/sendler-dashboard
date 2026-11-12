import * as React from "react";
import {
    AreaSeries, CandlestickSeries, ColorType, CrosshairMode, HistogramSeries, createChart, createSeriesMarkers,
    type AutoscaleInfo, type IChartApi, type ISeriesApi, type SeriesMarker, type Time, type UTCTimestamp,
} from "lightweight-charts";
import { continuous, sigFor } from "./chartMath";

/**
 * Price chart on lightweight-charts (TradingView, Apache 2.0 — its attribution logo stays on).
 * Candles or a line, volume underneath, markers for creator sells and smart-money buys.
 * Candles are filled, green up and red down, like the launchpads' own charts; the legend says the
 * direction in words (± %), so it never rests on colour alone.
 * Colours are read from the CSS roles and re-read when the theme changes.
 */
export interface Bar {
    /** Unix seconds, UTC (bucket start). */
    time: number;
    open: number;
    high: number;
    low: number;
    close: number;
    volume: number;
}

export interface Mark {
    time: number;
    kind: "creatorSell" | "smartBuy";
}

/** The library draws UTC; shifting by the local offset shows local time on the axis. */
const TZ_S = -new Date().getTimezoneOffset() * 60;
const at = (t: number) => (t + TZ_S) as UTCTimestamp;

/**
 * The price axis never spans less than this share of the price: one trade or a flat hour would
 * otherwise be stretched over the whole height, and a 0.1% wiggle drawn as a cliff.
 */
const MIN_SPAN = 0.04;

function alpha(hex: string, a: number): string {
    const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
    if (!m) return hex;
    const n = parseInt(m[1], 16);
    return `rgba(${n >> 16}, ${(n >> 8) & 255}, ${n & 255}, ${a})`;
}

function useThemeTick(): number {
    const [n, set] = React.useState(0);
    React.useEffect(() => {
        const on = () => set((x) => x + 1);
        window.addEventListener("dash:theme", on);
        return () => window.removeEventListener("dash:theme", on);
    }, []);
    return n;
}

function widen(info: AutoscaleInfo | null): AutoscaleInfo | null {
    if (!info?.priceRange) return info;
    const { minValue, maxValue } = info.priceRange;
    const mid = (minValue + maxValue) / 2;
    const min = Math.abs(mid) * MIN_SPAN;
    if (maxValue - minValue >= min) return info;
    return { ...info, priceRange: { minValue: mid - min / 2, maxValue: mid + min / 2 } };
}

export function PriceChart({
    bars: raw,
    marks,
    mode,
    intraday,
    formatPrice,
    formatVolume,
    formatChange,
    summary,
    height,
    visibleBars = 100,
}: {
    bars: Bar[];
    marks: Mark[];
    mode: "candles" | "line";
    /** Show the time of day on the axis (1m / 1h bars). */
    intraday: boolean;
    /** `sig` — fixed significant digits (the axis and the legend ask for as many as the range needs). */
    formatPrice: (v: number, sig?: number) => string;
    formatVolume: (v: number) => string;
    /** Change of the bar as a fraction (0.012 = +1.2%). */
    formatChange: (fraction: number) => string;
    /** Text alternative for screen readers. */
    summary: string;
    height: number;
    /** Bars in the first view; fewer bars keep their width and sit on the right. */
    visibleBars?: number;
}) {
    const bars = React.useMemo(() => continuous(raw), [raw]);
    const box = React.useRef<HTMLDivElement>(null);
    const chart = React.useRef<IChartApi | null>(null);
    const price = React.useRef<ISeriesApi<"Candlestick"> | ISeriesApi<"Area"> | null>(null);
    const volume = React.useRef<ISeriesApi<"Histogram"> | null>(null);
    const markers = React.useRef<ReturnType<typeof createSeriesMarkers<Time>> | null>(null);
    const colors = React.useRef<Record<string, string>>({});
    const fitted = React.useRef(false);
    const theme = useThemeTick();
    // Digits follow the visible range: a zoomed-in axis needs more of them to tell labels apart.
    const sig = (v: number) => {
        const r = chart.current?.priceScale("right").getVisibleRange();
        return sigFor(Math.abs(v), r ? r.to - r.from : 0);
    };
    // Axis labels: the scale's lower margin dips below zero — no label there, and a clean 0.
    const floor = React.useRef(0);
    const axisPrice = (v: number) => (v < 0 ? "" : v < floor.current ? "0" : formatPrice(v, sig(v)));
    const fmt = React.useRef({ formatPrice: axisPrice, formatVolume });
    fmt.current = { formatPrice: axisPrice, formatVolume };
    const [hover, setHover] = React.useState<Bar | null>(null);

    // Build the chart (again on a mode or theme change).
    React.useEffect(() => {
        const el = box.current;
        if (!el) return;
        const css = (v: string) => getComputedStyle(el).getPropertyValue(v).trim();
        const c = {
            fg2: css("--fg-2"), line: css("--line-soft"), up: css("--up"), down: css("--down"),
            brand: css("--brand"), surface: css("--surface"), amber: css("--amber"),
        };
        colors.current = c;
        const ch = createChart(el, {
            autoSize: true,
            layout: {
                background: { type: ColorType.Solid, color: "transparent" },
                textColor: c.fg2,
                fontFamily: "Inter, system-ui, sans-serif",
                fontSize: 11,
                attributionLogo: true,
            },
            grid: { vertLines: { visible: false }, horzLines: { color: c.line } },
            rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.12, bottom: 0.24 } },
            timeScale: { borderVisible: false, timeVisible: intraday, secondsVisible: false, rightOffset: 4, barSpacing: 8, minBarSpacing: 1 },
            crosshair: { mode: CrosshairMode.Normal },
            localization: { priceFormatter: (v: number) => fmt.current.formatPrice(v) },
            handleScale: { axisPressedMouseMove: false },
        });
        const p =
            mode === "candles"
                ? ch.addSeries(CandlestickSeries, {
                    upColor: c.up, borderUpColor: c.up, wickUpColor: c.up,
                    downColor: c.down, borderDownColor: c.down, wickDownColor: c.down,
                    priceLineVisible: true, autoscaleInfoProvider: (orig: () => AutoscaleInfo | null) => widen(orig()),
                })
                : ch.addSeries(AreaSeries, {
                    lineColor: c.brand, lineWidth: 2, topColor: alpha(c.brand, 0.22), bottomColor: alpha(c.brand, 0.02),
                    autoscaleInfoProvider: (orig: () => AutoscaleInfo | null) => widen(orig()),
                });
        const v = ch.addSeries(HistogramSeries, { priceScaleId: "", priceFormat: { type: "volume" }, lastValueVisible: false, priceLineVisible: false });
        v.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
        ch.subscribeCrosshairMove((param) => {
            const d = (param.time !== undefined ? param.seriesData.get(p) : undefined) as
                | { open?: number; high?: number; low?: number; close?: number; value?: number }
                | undefined;
            const vol = (param.time !== undefined ? param.seriesData.get(v) : undefined) as { value?: number } | undefined;
            if (!d) return setHover(null);
            const t = Number(param.time) - TZ_S;
            const volume = vol?.value ?? 0;
            if (d.open !== undefined && d.high !== undefined && d.low !== undefined && d.close !== undefined) {
                setHover({ time: t, open: d.open, high: d.high, low: d.low, close: d.close, volume });
            } else if (d.value !== undefined) {
                setHover({ time: t, open: d.value, high: d.value, low: d.value, close: d.value, volume });
            }
        });
        chart.current = ch;
        price.current = p;
        volume.current = v;
        markers.current = createSeriesMarkers(p as ISeriesApi<"Candlestick">, []);
        fitted.current = false;
        return () => {
            ch.remove();
            chart.current = price.current = volume.current = markers.current = null;
        };
    }, [mode, intraday, theme]);

    // Data.
    React.useEffect(() => {
        const p = price.current;
        const v = volume.current;
        if (!p || !v) return;
        const c = colors.current;
        const last = bars[bars.length - 1]?.close ?? 1;
        // Fine enough for 8 significant digits — the axis decides how many it shows.
        const minMove = 10 ** (Math.floor(Math.log10(Math.max(last, 1e-30))) - 8);
        floor.current = minMove;
        p.applyOptions({ priceFormat: { type: "custom", minMove, formatter: (x: number) => fmt.current.formatPrice(x) } });
        if (mode === "candles") {
            (p as ISeriesApi<"Candlestick">).setData(bars.map((b) => ({ time: at(b.time), open: b.open, high: b.high, low: b.low, close: b.close })));
        } else {
            (p as ISeriesApi<"Area">).setData(bars.map((b) => ({ time: at(b.time), value: b.close })));
        }
        v.setData(bars.map((b) => ({ time: at(b.time), value: b.volume, color: alpha(b.close >= b.open ? c.up : c.down, 0.5) })));
        const known = new Set(bars.map((b) => b.time));
        const seen = new Set<string>();
        const ms: SeriesMarker<Time>[] = [];
        for (const m of [...marks].sort((a, b) => a.time - b.time)) {
            const k = `${m.time}:${m.kind}`;
            if (!known.has(m.time) || seen.has(k)) continue;
            seen.add(k);
            ms.push(m.kind === "creatorSell"
                ? { time: at(m.time), position: "aboveBar", shape: "square", color: c.amber, size: 1 }
                : { time: at(m.time), position: "belowBar", shape: "circle", color: c.brand, size: 1 });
        }
        markers.current?.setMarkers(ms);
        if (!fitted.current && bars.length) {
            fitted.current = true;
            // The last `visibleBars` bars; with fewer, the empty room stays on the left and a single
            // candle keeps a candle's width instead of filling the chart.
            chart.current?.timeScale().setVisibleLogicalRange({ from: bars.length - visibleBars, to: bars.length - 1 + 4 });
        }
    }, [bars, marks, mode, intraday, theme, visibleBars]);

    const shown = hover ?? bars[bars.length - 1] ?? null;
    // The legend renders before the chart has a visible range: its digits come from the data's span.
    const span = React.useMemo(() => {
        if (!bars.length) return 0;
        const hi = Math.max(...bars.map((b) => b.high));
        const lo = Math.min(...bars.map((b) => b.low));
        return Math.max(hi - lo, ((hi + lo) / 2) * MIN_SPAN);
    }, [bars]);
    const d = shown ? sigFor(shown.close, span) : 3;
    const move = shown && shown.open > 0 ? (shown.close - shown.open) / shown.open : null;
    return (
        <div className="relative" style={{ height }}>
            {shown && (
                // Stops short of the price axis (its labels are ~56 px); narrow (phone): O/H/L go, the
                // close, the bar's change and the volume stay — the row never runs into the axis.
                <div className="pointer-events-none absolute top-1 right-16 left-2 z-[3] flex flex-wrap gap-x-3 overflow-hidden text-[11px] text-fg-2 tabular-nums" aria-hidden="true">
                    {mode === "candles" ? (
                        <>
                            <span className="@max-[560px]:hidden">O <b className="font-medium text-fg">{formatPrice(shown.open, d)}</b></span>
                            <span className="@max-[560px]:hidden">H <b className="font-medium text-fg">{formatPrice(shown.high, d)}</b></span>
                            <span className="@max-[560px]:hidden">L <b className="font-medium text-fg">{formatPrice(shown.low, d)}</b></span>
                            <span>C <b className="font-medium text-fg">{formatPrice(shown.close, d)}</b></span>
                        </>
                    ) : (
                        <b className="font-medium text-fg">{formatPrice(shown.close, d)}</b>
                    )}
                    {move !== null && (
                        <b className={move > 0 ? "font-medium text-up-fg" : move < 0 ? "font-medium text-down-fg" : "font-medium text-fg-2"}>
                            {formatChange(move)}
                        </b>
                    )}
                    <span>V <b className="font-medium text-fg">{formatVolume(shown.volume)}</b></span>
                </div>
            )}
            <div ref={box} className="absolute inset-0" role="img" aria-label={summary} />
        </div>
    );
}
