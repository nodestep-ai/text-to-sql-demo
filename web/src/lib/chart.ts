import { chartFormat, type NumberFormatter } from "./format.ts";
import type { ChartFrame, JsonValue } from "./sse.ts";

export interface ChartPoint {
  label: string;
  value: number;
}

export interface Scale {
  min: number;
  max: number;
  ticks: number[];
}

export interface Tick {
  value: number;
  position: number;
  text: string;
}

export type Anchor = "start" | "middle" | "end";

export interface Mark extends ChartPoint {
  anchor: Anchor;
  short: string;
  text: string;
  x: number;
  y: number;
  labelY: number;
}

export interface ChartLayout {
  kind: "bar" | "line";
  width: number;
  height: number;
  left: number;
  right: number;
  top: number;
  bottom: number;
  baseline: number;
  labelEnd: number;
  labelAnchor: Anchor;
  labelEvery: number;
  stacked: boolean;
  ticks: Tick[];
  marks: Mark[];
  paths: string[];
}

const TOP = 28;
const AXIS = 44;
const BAND = 28;
const STACKED_BAND = 44;
const STACKED_LABEL = 10;
const STACKED_BAR = 28;
const BAR = 16;
const PLOT_HEIGHT = 200;
const CHAR_WIDTH = 7;
const MIN_PLOT = 120;
const MIN_LABELS = 24;

export function chartPoints(chart: ChartFrame): ChartPoint[] {
  const points: ChartPoint[] = [];
  for (const row of chart.data) {
    const value = toNumber(row[chart.y.field]);
    if (value !== null) points.push({ label: toLabel(row[chart.x.field]), value });
  }
  return points;
}

export function niceScale(values: number[], includeZero: boolean, count = 5): Scale {
  let low = Math.min(...values);
  let high = Math.max(...values);
  if (includeZero || low === high) {
    low = Math.min(low, 0);
    high = Math.max(high, 0);
  }
  if (low === high) high = low + 1;
  const rough = (high - low) / count;
  const magnitude = 10 ** Math.floor(Math.log10(rough));
  const factor = rough / magnitude;
  const step = (factor <= 1 ? 1 : factor <= 2 ? 2 : factor <= 5 ? 5 : 10) * magnitude;
  const ticks: number[] = [];
  for (let index = Math.floor(low / step); index <= Math.ceil(high / step); index += 1) {
    ticks.push(Number((index * step).toPrecision(12)));
  }
  return { min: ticks[0], max: ticks[ticks.length - 1], ticks };
}

export function truncate(text: string, length: number): string {
  return text.length > length ? `${text.slice(0, length - 1)}…` : text;
}

export function barPath(baseline: number, end: number, top: number, height: number, radius = 4): string {
  const direction = end >= baseline ? 1 : -1;
  const r = Math.min(radius, Math.abs(end - baseline), height / 2);
  const sweep = direction > 0 ? 1 : 0;
  const bottom = top + height;
  const inner = round(end - direction * r);
  return `M${baseline} ${top}H${inner}A${r} ${r} 0 0 ${sweep} ${end} ${round(top + r)}V${round(bottom - r)}A${r} ${r} 0 0 ${sweep} ${inner} ${bottom}H${baseline}Z`;
}

export function layoutChart(chart: ChartFrame, width: number, format: NumberFormatter | null = null): ChartLayout | null {
  const points = chartPoints(chart);
  if (points.length === 0 || width <= 0) return null;
  const numbers =
    format ??
    chartFormat(
      chart.y.field,
      points.map((point) => point.value),
      null,
    );
  return chart.kind === "bar" ? barLayout(points, width, numbers) : lineLayout(points, width, numbers);
}

export function nearestMark(layout: ChartLayout, x: number, y: number): number {
  const distance = (mark: Mark): number => Math.abs(layout.kind === "bar" ? mark.y - y : mark.x - x);
  let best = 0;
  for (const [index, mark] of layout.marks.entries()) {
    if (distance(mark) < distance(layout.marks[best])) best = index;
  }
  return best;
}

function barLayout(points: ChartPoint[], width: number, format: NumberFormatter): ChartLayout {
  const texts = points.map((point) => format.format(point.value));
  const tip = Math.max(...texts.map((text) => text.length)) * CHAR_WIDTH + 8;
  const negative = points.some((point) => point.value < 0) ? tip : 0;
  const positive = points.some((point) => point.value >= 0) ? tip : 16;
  const longest = Math.max(...points.map((point) => point.label.length)) * CHAR_WIDTH;
  const room = width - 12 - negative - positive - MIN_PLOT;
  const beside = Math.round(Math.max(MIN_LABELS, Math.min(Math.max(longest, 48), width * 0.4, room)));
  const stacked = longest > beside;
  const labels = stacked ? 0 : beside;
  const scale = niceScale(
    points.map((point) => point.value),
    true,
  );
  const edge = Math.ceil((format.format(scale.min).length * CHAR_WIDTH) / 2) + 2;
  const left = stacked ? Math.max(negative, edge) : labels + 12 + negative;
  const right = width - positive;
  const every = tickLabelEvery(scale, right - left, format);
  const band = stacked ? STACKED_BAND : BAND;
  const bottom = TOP + points.length * band;
  const chars = Math.floor((stacked ? width - left : labels) / CHAR_WIDTH);
  const x = (value: number): number => round(left + ((value - scale.min) / (scale.max - scale.min)) * (right - left));
  const baseline = x(0);
  const marks = points.map((point, index) => {
    const start = TOP + index * band;
    const y = stacked ? start + STACKED_BAR : start + BAND / 2;
    return {
      ...point,
      anchor: "end" as const,
      short: truncate(point.label, chars),
      text: texts[index],
      x: x(point.value),
      y,
      labelY: stacked ? start + STACKED_LABEL : y,
    };
  });
  return {
    kind: "bar",
    width,
    height: bottom + AXIS,
    left,
    right,
    top: TOP,
    bottom,
    baseline,
    labelEnd: stacked ? left : labels + 4,
    labelAnchor: stacked ? "start" : "end",
    labelEvery: 1,
    stacked,
    ticks: scale.ticks.map((value, index) => ({
      value,
      position: x(value),
      text: index % every === 0 ? format.format(value) : "",
    })),
    marks,
    paths: marks.map((mark) => barPath(baseline, mark.x, mark.y - BAR / 2, BAR)),
  };
}

function tickLabelEvery(scale: Scale, plot: number, format: NumberFormatter): number {
  const gap = plot / Math.max(1, scale.ticks.length - 1);
  const widest = Math.max(...scale.ticks.map((value) => format.format(value).length)) * CHAR_WIDTH + 8;
  return Math.max(1, Math.ceil(widest / gap));
}

function lineLayout(points: ChartPoint[], width: number, format: NumberFormatter): ChartLayout {
  const scale = niceScale(
    points.map((point) => point.value),
    true,
  );
  const left = Math.max(40, Math.max(...scale.ticks.map((value) => format.format(value).length)) * CHAR_WIDTH + 12);
  const right = width - 32;
  const bottom = TOP + PLOT_HEIGHT;
  const gap = points.length > 1 ? (right - left) / (points.length - 1) : 0;
  const y = (value: number): number => round(bottom - ((value - scale.min) / (scale.max - scale.min)) * PLOT_HEIGHT);
  const marks = points.map((point, index) => ({
    ...point,
    anchor: lineAnchor(index, points.length),
    short: truncate(point.label, 12),
    text: format.format(point.value),
    x: round(points.length > 1 ? left + index * gap : (left + right) / 2),
    y: y(point.value),
    labelY: bottom + 16,
  }));
  return {
    kind: "line",
    width,
    height: bottom + AXIS,
    left,
    right,
    top: TOP,
    bottom,
    baseline: bottom,
    labelEnd: left,
    labelAnchor: "end",
    labelEvery: Math.max(1, Math.ceil(points.length / Math.max(2, Math.floor((right - left) / 80)))),
    stacked: false,
    ticks: scale.ticks.map((value) => ({ value, position: y(value), text: format.format(value) })),
    marks,
    paths: [marks.map((mark, index) => `${index === 0 ? "M" : "L"}${mark.x} ${mark.y}`).join(" ")],
  };
}

function lineAnchor(index: number, count: number): Anchor {
  if (count === 1) return "middle";
  if (index === 0) return "start";
  return index === count - 1 ? "end" : "middle";
}

function toNumber(value: JsonValue | undefined): number | null {
  const number =
    typeof value === "number" ? value : typeof value === "string" && value.trim() !== "" ? Number(value) : Number.NaN;
  return Number.isFinite(number) ? number : null;
}

function toLabel(value: JsonValue | undefined): string {
  if (value === null || value === undefined) return "(none)";
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}

function round(value: number): number {
  return Math.round(value * 10) / 10;
}
