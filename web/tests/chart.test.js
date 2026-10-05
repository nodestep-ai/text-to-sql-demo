import assert from "node:assert/strict";
import { test } from "node:test";
import { barPath, chartPoints, layoutChart, nearestMark, niceScale, truncate } from "../src/lib/chart.ts";
import { chartFormat } from "../src/lib/format.ts";

function chartOf(kind, rows) {
  return {
    type: "chart",
    call_id: "c1",
    kind,
    title: "Chart",
    x: { field: "x", label: "X" },
    y: { field: "y", label: "Y" },
    data: rows.map(([x, y]) => ({ x, y })),
  };
}

function finite(layout) {
  const numbers = [layout.height, layout.left, layout.right, layout.top, layout.bottom, layout.baseline];
  for (const tick of layout.ticks) numbers.push(tick.position);
  for (const mark of layout.marks) numbers.push(mark.x, mark.y);
  return numbers.every(Number.isFinite) && layout.paths.every((path) => !path.includes("NaN"));
}

test("nice scales use round steps", () => {
  assert.deepEqual(niceScale([1290, 400, 800], true), { min: 0, max: 1500, ticks: [0, 500, 1000, 1500] });
  assert.deepEqual(niceScale([400.5, 481.6], false), { min: 400, max: 500, ticks: [400, 420, 440, 460, 480, 500] });
  assert.deepEqual(niceScale([-50, 200], true), { min: -50, max: 200, ticks: [-50, 0, 50, 100, 150, 200] });
  assert.deepEqual(niceScale([0.1, 0.37], true), { min: 0, max: 0.4, ticks: [0, 0.1, 0.2, 0.3, 0.4] });
  assert.deepEqual(niceScale([5, 5], false), { min: 0, max: 5, ticks: [0, 1, 2, 3, 4, 5] });
  assert.deepEqual(niceScale([0, 0], true), { min: 0, max: 1, ticks: [0, 0.2, 0.4, 0.6, 0.8, 1] });
});

test("points skip rows without a numeric value", () => {
  const chart = chartOf("bar", [["Coffee", 10], ["Tea", "2.5"], ["Cups", null], ["Kettles", "n/a"], [null, 3], ["Filters", ""]]);
  assert.deepEqual(chartPoints(chart), [
    { label: "Coffee", value: 10 },
    { label: "Tea", value: 2.5 },
    { label: "(none)", value: 3 },
  ]);
});

test("truncate keeps short labels and shortens long ones", () => {
  assert.equal(truncate("Coffee", 6), "Coffee");
  assert.equal(truncate("Grinders & scales", 6), "Grind…");
});

test("bar paths are rounded at the data end only", () => {
  assert.equal(barPath(10, 50, 0, 16), "M10 0H46A4 4 0 0 1 50 4V12A4 4 0 0 1 46 16H10Z");
  assert.equal(barPath(50, 10, 0, 16), "M50 0H14A4 4 0 0 0 10 4V12A4 4 0 0 0 14 16H50Z");
});

test("bar layout puts one bar per row and grows from zero", () => {
  const layout = layoutChart(chartOf("bar", [["Coffee", 1290], ["Tea", 130], ["Refunds", -40]]), 600);
  assert.equal(layout.kind, "bar");
  assert.equal(layout.marks.length, 3);
  assert.equal(layout.paths.length, 3);
  assert.ok(layout.marks[0].x > layout.marks[1].x);
  assert.ok(layout.marks[2].x < layout.baseline);
  assert.ok(layout.marks[1].y > layout.marks[0].y);
  assert.ok(layout.marks.every((mark) => mark.x >= layout.left && mark.x <= layout.right));
  assert.ok(finite(layout));
});

test("line layout spreads points evenly and puts larger values higher", () => {
  const layout = layoutChart(chartOf("line", [["2024", 10], ["2025", 30], ["2026", 20]]), 600);
  assert.equal(layout.kind, "line");
  assert.equal(layout.paths.length, 1);
  assert.equal(layout.marks[0].x, layout.left);
  assert.equal(layout.marks[2].x, layout.right);
  assert.ok(layout.marks[1].y < layout.marks[2].y);
  assert.ok(finite(layout));
});

test("edge cases render finite coordinates", () => {
  for (const kind of ["bar", "line"]) {
    for (const rows of [[["only", 42]], [["a", 0], ["b", 0]], [["a", -5], ["b", -1]]]) {
      assert.ok(finite(layoutChart(chartOf(kind, rows), 320)), `${kind} ${JSON.stringify(rows)}`);
    }
  }
  assert.equal(layoutChart(chartOf("bar", [["a", null]]), 600), null);
  assert.equal(layoutChart(chartOf("bar", [["a", 1]]), 0), null);
});

test("the nearest mark follows the category axis", () => {
  const bars = layoutChart(chartOf("bar", [["a", 1], ["b", 2], ["c", 3]]), 600);
  assert.equal(nearestMark(bars, 0, bars.marks[2].y + 3), 2);
  const line = layoutChart(chartOf("line", [["a", 1], ["b", 2], ["c", 3]]), 600);
  assert.equal(nearestMark(line, line.marks[1].x - 5, 0), 1);
});

test("bar values sit at the bar tips, inside the chart and clear of the category labels", () => {
  const layout = layoutChart(chartOf("bar", [["Grinders & scales", 1290], ["Refunds", -40], ["Tea", 0], ["Kettles", 25000]]), 400);
  const format = chartFormat("y", [1290, -40, 0, 25000], null);
  assert.deepEqual(
    layout.marks.map((mark) => mark.text),
    [format.format(1290), format.format(-40), format.format(0), format.format(25000)],
  );
  for (const mark of layout.marks) {
    const room = 4 + mark.text.length * 7;
    if (mark.value >= 0) assert.ok(mark.x + room <= layout.width, mark.label);
    else assert.ok(mark.x - room >= layout.labelEnd, mark.label);
  }
  assert.ok(layout.labelEnd <= layout.left);
});

test("ticks and values in one chart share one format", () => {
  const format = chartFormat("revenue", [2_500_000, 900], "EUR", "en-US");
  for (const kind of ["bar", "line"]) {
    const layout = layoutChart(chartOf(kind, [["a", 2_500_000], ["b", 900]]), 600, format);
    const labelled = layout.ticks.filter((tick) => tick.text !== "");
    assert.ok(labelled.length >= 2);
    assert.deepEqual(
      labelled.map((tick) => tick.text),
      labelled.map((tick) => format.format(tick.value)),
    );
    assert.deepEqual(layout.marks.map((mark) => mark.text), ["€2.5M", "€900"]);
  }
});

test("at phone width long bar labels go above their bars in full, and the plot spans the chart", () => {
  const rows = [
    ["Brazil Cerrado 250 g whole beans", 60480.53],
    ["Costa Rica Tarrazú 250 g", 34511.68],
    ["Tea", 4963.69],
  ];
  const format = chartFormat("revenue", rows.map(([, value]) => value), "EUR", "en-US");
  const layout = layoutChart(chartOf("bar", rows), 332, format);
  assert.equal(layout.stacked, true);
  assert.equal(layout.labelAnchor, "start");
  assert.equal(layout.labelEnd, layout.left);
  assert.deepEqual(layout.marks.map((mark) => mark.short), rows.map(([label]) => label));
  assert.ok(layout.right - layout.left >= 332 - 100, `${layout.right - layout.left}`);
  for (const mark of layout.marks) {
    assert.ok(mark.short.length * 7 <= layout.width - layout.left, mark.label);
    assert.ok(mark.labelY + 6 <= mark.y - 8, `${mark.labelY} ${mark.y}`);
  }
  for (let index = 1; index < layout.marks.length; index += 1) {
    assert.ok(layout.marks[index].labelY - 6 >= layout.marks[index - 1].y + 8);
  }
  assert.ok(layout.marks[0].labelY - 6 >= layout.top);
  assert.ok(layout.marks.at(-1).y + 8 <= layout.bottom);
  for (const tick of layout.ticks.filter((item) => item.text !== "")) {
    assert.ok(tick.position - tick.text.length * 3.5 >= 0, tick.text);
    assert.ok(tick.position + tick.text.length * 3.5 <= layout.width, tick.text);
  }
  assert.ok(finite(layout));
});

test("a label too long even for the full width is shortened, with the full text kept", () => {
  const label = "Brazil Cerrado single estate natural process 250 g whole beans";
  const layout = layoutChart(chartOf("bar", [[label, 10], ["Tea", 4]]), 332);
  assert.equal(layout.stacked, true);
  assert.ok(layout.marks[0].short.endsWith("…"));
  assert.ok(layout.marks[0].short.length * 7 <= layout.width - layout.left);
  assert.equal(layout.marks[0].label, label);
});

test("short bar labels stay beside their bars, also at phone width", () => {
  for (const width of [332, 800]) {
    const layout = layoutChart(chartOf("bar", [["Coffee", 10], ["Tea", 4], ["Gifts", 7]]), width);
    assert.equal(layout.stacked, false);
    assert.equal(layout.labelAnchor, "end");
    assert.ok(layout.labelEnd <= layout.left);
    assert.ok(layout.marks.every((mark) => mark.labelY === mark.y && mark.short === mark.label));
  }
});

test("bar chart ticks never overlap at phone width", () => {
  const rows = [["Coffee", 60480.53], ["Brewing equipment", 34511.68], ["Snacks", 4619.06]];
  const format = chartFormat("revenue", rows.map(([, value]) => value), "EUR", "en-US");
  const layout = layoutChart(chartOf("bar", rows), 322, format);
  const labelled = layout.ticks.filter((tick) => tick.text !== "");
  assert.ok(labelled.length >= 2);
  assert.equal(labelled[0].value, 0);
  assert.ok(layout.ticks.at(-1).value < 100_000);
  for (let index = 1; index < labelled.length; index += 1) {
    const gap = labelled[index].position - labelled[index - 1].position;
    const room = (labelled[index].text.length + labelled[index - 1].text.length) * 3.5 + 8;
    assert.ok(gap >= room, `${labelled[index - 1].text} ${labelled[index].text} ${gap}`);
  }
});

test("the line chart value axis starts at zero, and a scale that crosses zero keeps its negative part", () => {
  const rows = [["2024-01", 5876.12], ["2024-02", 4156.05], ["2024-03", 13025.19]];
  const layout = layoutChart(chartOf("line", rows), 600);
  assert.equal(layout.ticks[0].value, 0);
  assert.equal(layout.ticks[0].position, layout.bottom);
  const mixed = layoutChart(chartOf("line", [["a", -20], ["b", 40]]), 600);
  assert.ok(mixed.ticks[0].value < 0);
  assert.ok(mixed.ticks.some((tick) => tick.value === 0));
});

test("the first month label starts at the value axis so it clears the zero label, and the last one ends at the plot edge", () => {
  const rows = Array.from({ length: 24 }, (_, index) => [`2024-${String(index + 1).padStart(2, "0")}`, 1000 + index]);
  for (const width of [322, 800]) {
    const layout = layoutChart(chartOf("line", rows), width);
    assert.equal(layout.marks[0].anchor, "start");
    assert.equal(layout.marks[0].x, layout.left);
    assert.equal(layout.marks.at(-1).anchor, "end");
    assert.ok(layout.marks.slice(1, -1).every((mark) => mark.anchor === "middle"));
  }
  assert.equal(layoutChart(chartOf("line", [["only", 3]]), 600).marks[0].anchor, "middle");
});

test("line chart month labels that are drawn never overlap", () => {
  const rows = Array.from({ length: 24 }, (_, index) => [`2024-${String(index + 1).padStart(2, "0")}`, 1000 + index]);
  for (const width of [322, 600, 800]) {
    const layout = layoutChart(chartOf("line", rows), width);
    const drawn = layout.marks.filter((_, index) => index % layout.labelEvery === 0);
    const spans = drawn.map((mark) => {
      const size = mark.short.length * 7;
      const start = mark.anchor === "start" ? mark.x : mark.anchor === "end" ? mark.x - size : mark.x - size / 2;
      return [start, start + size];
    });
    for (let index = 1; index < spans.length; index += 1) {
      assert.ok(spans[index][0] >= spans[index - 1][1] + 4, `${width}: ${JSON.stringify(spans)}`);
    }
  }
});
