import type { JsonValue } from "./sse.ts";

const numbers = new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 });
const sizes = new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 });
const SIZE_UNITS = ["KB", "MB", "GB", "TB"];

export function formatNumber(value: number): string {
  return numbers.format(value);
}

export function formatDuration(milliseconds: number): string {
  return milliseconds < 1 ? "<1 ms" : `${numbers.format(milliseconds)} ms`;
}

export type NumberStyle = "plain" | "percent" | "currency";

export interface ColumnMeaning {
  style: NumberStyle;
  currency: string | null;
  grouping: boolean;
}

export class NumberFormatter {
  readonly #format: Intl.NumberFormat;
  readonly #scale: number;

  constructor(options: Intl.NumberFormatOptions, scale = 1, locale: string | undefined = undefined) {
    this.#format = new Intl.NumberFormat(locale, options);
    this.#scale = scale;
  }

  format(value: number): string {
    return this.#format.format(value * this.#scale);
  }
}

const CURRENCIES: [RegExp, string][] = [
  [/€|\beuros?\b|\bEUR\b/i, "EUR"],
  [/\$|\bdollars?\b|\bUSD\b/i, "USD"],
  [/£|\bpounds? sterling\b|\bGBP\b/i, "GBP"],
];
const CURRENCY_CODES = new Set(["eur", "usd", "gbp", "chf", "jpy", "czk", "pln"]);
const MONEY_WORDS = new Set(["revenue", "amount", "price", "cost", "refund", "refunds", "spend", "spent", "profit", "income"]);
const PERCENT_WORDS = new Set(["share", "pct", "percent", "percentage"]);
const UNGROUPED_WORDS = new Set(["id", "year"]);
const COMPACT_FROM = 1_000_000;
const MAX_DECIMALS = 8;

export function currencyOf(text: string | null): string | null {
  if (text === null) return null;
  return CURRENCIES.find(([pattern]) => pattern.test(text))?.[1] ?? null;
}

export function columnMeaning(name: string, currency: string | null): ColumnMeaning {
  const words = name
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .toLowerCase()
    .split(/[^a-z0-9]+/)
    .filter((word) => word !== "");
  const grouping = !UNGROUPED_WORDS.has(words.at(-1) ?? "");
  if (words.some((word) => PERCENT_WORDS.has(word))) return { style: "percent", currency: null, grouping };
  const code = words.find((word) => CURRENCY_CODES.has(word));
  if (code !== undefined) return { style: "currency", currency: code.toUpperCase(), grouping };
  if (currency !== null && words.some((word) => MONEY_WORDS.has(word))) return { style: "currency", currency, grouping };
  return { style: "plain", currency: null, grouping };
}

export function columnFormat(
  name: string,
  values: number[],
  currency: string | null,
  locale: string | undefined = undefined,
): NumberFormatter {
  const meaning = numberMeaning(name, values, currency);
  if (meaning.style === "percent") return percentFormat(values, locale);
  if (meaning.style === "currency" || values.every(Number.isInteger)) {
    const decimals = meaning.style === "currency" ? 2 : 0;
    return new NumberFormatter(
      { ...styleOptions(meaning), useGrouping: meaning.grouping, minimumFractionDigits: decimals, maximumFractionDigits: decimals },
      1,
      locale,
    );
  }
  return new NumberFormatter(
    { useGrouping: meaning.grouping, minimumFractionDigits: 2, maximumFractionDigits: significantDecimals(values, 2) },
    1,
    locale,
  );
}

export function chartFormat(
  name: string,
  values: number[],
  currency: string | null,
  locale: string | undefined = undefined,
): NumberFormatter {
  const meaning = numberMeaning(name, values, currency);
  if (meaning.style === "percent") return percentFormat(values, locale);
  const largest = Math.max(0, ...values.map(Math.abs));
  const compact = largest >= COMPACT_FROM;
  return new NumberFormatter(
    {
      ...styleOptions(meaning),
      useGrouping: meaning.grouping,
      notation: compact ? "compact" : "standard",
      minimumFractionDigits: 0,
      maximumFractionDigits: compact ? 1 : largest >= 100 ? 0 : 2,
    },
    1,
    locale,
  );
}

function styleOptions(meaning: ColumnMeaning): Intl.NumberFormatOptions {
  return meaning.style === "currency" && meaning.currency !== null ? { style: "currency", currency: meaning.currency } : {};
}

function numberMeaning(name: string, values: number[], currency: string | null): ColumnMeaning {
  const meaning = columnMeaning(name, currency);
  if (meaning.style === "percent" && !values.some((value) => Math.abs(value) > 1)) return { ...meaning, style: "plain" };
  return meaning;
}

function significantDecimals(values: number[], least: number): number {
  const smallest = Math.min(...values.filter((value) => value !== 0).map(Math.abs));
  if (!Number.isFinite(smallest) || smallest >= 1) return least;
  return Math.min(MAX_DECIMALS, Math.max(least, 1 - Math.floor(Math.log10(smallest))));
}

function percentFormat(values: number[], locale: string | undefined): NumberFormatter {
  return new NumberFormatter({ style: "percent", maximumFractionDigits: significantDecimals(values, 1) }, 0.01, locale);
}

export function formatCell(value: JsonValue, format: NumberFormatter | null = null): string {
  if (value === null) return "NULL";
  if (typeof value === "number" && format !== null) return format.format(value);
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

export function cellTitle(value: JsonValue, format: NumberFormatter | null): string | undefined {
  if (typeof value !== "number" || format === null) return undefined;
  const raw = String(value);
  return format.format(value) === raw ? undefined : raw;
}

export interface TableColumn {
  numeric: boolean;
  wrap: boolean;
}

const WRAP_LENGTH = 48;

export function tableColumns(columns: string[], rows: JsonValue[][]): TableColumn[] {
  return columns.map((_, index) => {
    const values = rows.map((row) => row[index]);
    const numeric =
      values.some((value) => typeof value === "number") &&
      values.every((value) => value === null || typeof value === "number");
    return { numeric, wrap: !numeric && values.some((value) => formatCell(value).length > WRAP_LENGTH) };
  });
}

export function formatShortTime(value: string, now: Date = new Date()): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  if (date.toDateString() === now.toDateString()) return date.toLocaleTimeString(undefined, { timeStyle: "short" });
  if (date.getFullYear() === now.getFullYear()) {
    return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  }
  return date.toLocaleDateString(undefined, { dateStyle: "medium" });
}

export function formatTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export function plural(count: number, word: string): string {
  return `${formatNumber(count)} ${count === 1 ? word : `${word}s`}`;
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return plural(bytes, "byte");
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < SIZE_UNITS.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${sizes.format(value)} ${SIZE_UNITS[unit]}`;
}
