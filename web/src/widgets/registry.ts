import type { WidgetDef, WidgetType } from "./types";
import { launchesWidget } from "./launches";
import { moversWidget } from "./movers";
import { movesWidget } from "./moves";
import { networkWidget } from "./network";
import { newWidget } from "./new";
import { nowWidget } from "./now";
import { smartWidget } from "./smart";
import { topWidget } from "./top";

/** Every widget, by type. The grid looks widgets up here and knows nothing else about them. */
export const REGISTRY: Record<WidgetType, WidgetDef> = {
    now: nowWidget,
    launches: launchesWidget,
    movers: moversWidget,
    smart: smartWidget,
    moves: movesWidget,
    network: networkWidget,
    new: newWidget,
    top: topWidget,
};
