import assert from "node:assert/strict";
import { test } from "node:test";
import {
  cellTitle,
  chartFormat,
  columnFormat,
  columnMeaning,
  currencyOf,
  formatBytes,
  formatCell,
  formatDuration,
  formatShortTime,
  formatTime,
  plural,
  tableColumns,
} from "../src/lib/format.ts";

test("cells show NULL, raw numbers and JSON for nested values", () => {
  assert.equal(formatCell(null), "NULL");
  assert.equal(formatCell(1990), "1990");
  assert.equal(formatCell(true), "true");
  assert.equal(formatCell("Coffee"), "Coffee");
  assert.equal(formatCell({ a: [1] }), '{"a":[1]}');
});

test("an unreadable time is shown as sent", () => {
  assert.equal(formatTime("yesterday"), "yesterday");
  assert.notEqual(formatTime("2026-09-29T20:00:00Z"), "2026-09-29T20:00:00Z");
});

test("plural picks the word form", () => {
  assert.equal(plural(1, "row"), "1 row");
  assert.equal(plural(0, "row"), "0 rows");
  assert.equal(plural(1500, "row"), `${(1500).toLocaleString()} rows`);
});

test("sizes read as bytes, KB, MB and GB", () => {
  assert.equal(formatBytes(0), "0 bytes");
  assert.equal(formatBytes(1), "1 byte");
  assert.equal(formatBytes(1023), `${(1023).toLocaleString()} bytes`);
  assert.equal(formatBytes(8192), "8 KB");
  assert.equal(formatBytes(1067008), "1 MB");
  assert.equal(formatBytes(1536 * 1024), `${(1.5).toLocaleString()} MB`);
  assert.equal(formatBytes(3 * 1024 ** 3), "3 GB");
});

test("short times show the time today, the day this year and the full date otherwise", () => {
  const now = new Date(2026, 8, 30, 21, 0);
  const today = new Date(2026, 8, 30, 9, 36);
  const earlier = new Date(2026, 8, 29, 20, 0);
  const lastYear = new Date(2025, 11, 31, 8, 0);
  assert.equal(formatShortTime(today.toISOString(), now), today.toLocaleTimeString(undefined, { timeStyle: "short" }));
  assert.equal(formatShortTime(earlier.toISOString(), now), earlier.toLocaleDateString(undefined, { month: "short", day: "numeric" }));
  assert.equal(formatShortTime(lastYear.toISOString(), now), lastYear.toLocaleDateString(undefined, { dateStyle: "medium" }));
  assert.equal(formatShortTime("yesterday", now), "yesterday");
});

test("table columns are numeric when every value is a number or NULL, and wrap when a value is long", () => {
  const rows = [
    [1, "Coffee", "x".repeat(49), 2.5],
    [null, "Kitchen", "short", "3"],
  ];
  assert.deepEqual(tableColumns(["id", "category", "note", "mixed"], rows), [
    { numeric: true, wrap: false },
    { numeric: false, wrap: false },
    { numeric: false, wrap: true },
    { numeric: false, wrap: false },
  ]);
  assert.deepEqual(tableColumns(["empty"], [[null]]), [{ numeric: false, wrap: false }]);
});

test("a currency comes from the database description", () => {
  assert.equal(currencyOf("A made-up shop. Prices in euros."), "EUR");
  assert.equal(currencyOf("Totals in EUR"), "EUR");
  assert.equal(currencyOf("Prices in €"), "EUR");
  assert.equal(currencyOf("All amounts are US dollars."), "USD");
  assert.equal(currencyOf("Priced in pounds sterling"), "GBP");
  assert.equal(currencyOf("Orders and invoices"), null);
  assert.equal(currencyOf(null), null);
});

test("a column is a percentage or money only when its name says so", () => {
  assert.deepEqual(columnMeaning("share", "EUR"), { style: "percent", currency: null, grouping: true });
  assert.deepEqual(columnMeaning("return_pct", null), { style: "percent", currency: null, grouping: true });
  assert.deepEqual(columnMeaning("revenue", "EUR"), { style: "currency", currency: "EUR", grouping: true });
  assert.deepEqual(columnMeaning("unitPrice", "USD"), { style: "currency", currency: "USD", grouping: true });
  assert.deepEqual(columnMeaning("refund_amount", "EUR"), { style: "currency", currency: "EUR", grouping: true });
  assert.deepEqual(columnMeaning("price_usd", null), { style: "currency", currency: "USD", grouping: true });
  assert.deepEqual(columnMeaning("revenue", null), { style: "plain", currency: null, grouping: true });
  assert.deepEqual(columnMeaning("units", "EUR"), { style: "plain", currency: null, grouping: true });
  assert.deepEqual(columnMeaning("shares_sold", "EUR"), { style: "plain", currency: null, grouping: true });
  assert.deepEqual(columnMeaning("order_id", "EUR"), { style: "plain", currency: null, grouping: false });
  assert.deepEqual(columnMeaning("year", null), { style: "plain", currency: null, grouping: false });
});

test("table cells group thousands and round floats to two places", () => {
  const units = columnFormat("units", [5309, 12], null, "en-US");
  assert.equal(units.format(5309), "5,309");
  assert.equal(units.format(12), "12");
  const average = columnFormat("average", [1.5, 1234.5678], null, "en-US");
  assert.equal(average.format(1234.5678), "1,234.57");
  assert.equal(average.format(1.5), "1.50");
  assert.equal(average.format(2), "2.00");
  assert.equal(columnFormat("year", [2024], null, "en-US").format(2024), "2024");
  assert.equal(formatCell(1234.5678, average), "1,234.57");
  assert.equal(formatCell(null, average), "NULL");
});

test("money and percentage cells use their style", () => {
  const revenue = columnFormat("revenue", [60480.53, 5], "EUR", "en-US");
  assert.equal(revenue.format(60480.53), "€60,480.53");
  assert.equal(revenue.format(5), "€5.00");
  assert.equal(columnFormat("revenue", [60480.53], null, "en-US").format(60480.53), "60,480.53");
  const pct = columnFormat("pct", [25.4, 3], null, "en-US");
  assert.equal(pct.format(25.4), "25.4%");
});

test("a percentage column is never scaled, and without a value above 1 it shows plain numbers", () => {
  const units = columnFormat("share", [36.3, 20.7, 0.4], null, "en-US");
  assert.deepEqual([36.3, 20.7, 0.4].map((value) => units.format(value)), ["36.3%", "20.7%", "0.4%"]);
  assert.equal(columnFormat("share", [36.3, 0.04], null, "en-US").format(0.04), "0.04%");
  const small = columnFormat("share", [0.4, 0.8, 0.9], null, "en-US");
  assert.deepEqual([0.4, 0.8, 0.9].map((value) => small.format(value)), ["0.40", "0.80", "0.90"]);
  const fractions = columnFormat("share", [0.254, 0.1], null, "en-US");
  assert.equal(fractions.format(0.254), "0.25");
  assert.equal(chartFormat("share", [0.25], null, "en-US").format(0.25), "0.25");
  assert.equal(chartFormat("share", [36.3, 0.4], null, "en-US").format(36.3), "36.3%");
});

test("small float cells keep two significant digits of the smallest value", () => {
  const discount = columnFormat("avg_discount", [0.004, 0.0512, 0.2], null, "en-US");
  assert.deepEqual([0.004, 0.0512, 0.2].map((value) => discount.format(value)), ["0.004", "0.0512", "0.20"]);
  assert.equal(columnFormat("rate", [0.5, 0.25], null, "en-US").format(0.25), "0.25");
  assert.equal(columnFormat("rate", [0, 0.5], null, "en-US").format(0), "0.00");
});

test("a formatted number cell carries its raw value as a title when the text differs", () => {
  const discount = columnFormat("avg_discount", [0.123456, 1], null, "en-US");
  assert.equal(cellTitle(0.123456, discount), "0.123456");
  assert.equal(cellTitle(1, discount), "1");
  const units = columnFormat("units", [5309], null, "en-US");
  assert.equal(cellTitle(12, units), undefined);
  assert.equal(cellTitle(5309, units), "5309");
  assert.equal(cellTitle("Coffee", units), undefined);
  assert.equal(cellTitle(null, units), undefined);
  assert.equal(cellTitle(0.5, null), undefined);
});

test("a chart uses one format for every number it draws", () => {
  const small = chartFormat("units", [258, 12], null, "en-US");
  assert.equal(small.format(258), "258");
  assert.equal(small.format(1000), "1,000");
  const large = chartFormat("units", [2_500_000, 12_000], null, "en-US");
  assert.equal(large.format(2_500_000), "2.5M");
  assert.equal(large.format(12_000), "12K");
  const money = chartFormat("revenue", [60480.53, 4963.69], "EUR", "en-US");
  assert.equal(money.format(60480.53), "€60,481");
  assert.equal(money.format(0), "€0");
  const fractions = chartFormat("ratio", [0.37, 0.1], null, "en-US");
  assert.equal(fractions.format(0.37), "0.37");
});

test("query times under a millisecond read <1 ms", () => {
  assert.equal(formatDuration(0), "<1 ms");
  assert.equal(formatDuration(0.4), "<1 ms");
  assert.equal(formatDuration(1), "1 ms");
  assert.equal(formatDuration(1250), `${(1250).toLocaleString()} ms`);
});
