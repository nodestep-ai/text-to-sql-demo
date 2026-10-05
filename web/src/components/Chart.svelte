<script lang="ts">
  import { chartPoints, layoutChart, nearestMark } from "../lib/chart.ts";
  import { cellTitle, chartFormat, columnFormat } from "../lib/format.ts";
  import type { ChartFrame } from "../lib/sse.ts";

  let { chart, currency }: { chart: ChartFrame; currency: string | null } = $props();

  const KEYS: Record<string, number> = {
    ArrowRight: 1,
    ArrowDown: 1,
    ArrowLeft: -1,
    ArrowUp: -1,
  };

  let width = $state(0);
  let active = $state<number | null>(null);
  const points = $derived(chartPoints(chart));
  const values = $derived(points.map((point) => point.value));
  const drawn = $derived(chartFormat(chart.y.field, values, currency));
  const exact = $derived(columnFormat(chart.y.field, values, currency));
  const layout = $derived(layoutChart(chart, width, drawn));
  const mark = $derived(
    layout !== null && active !== null ? (layout.marks[active] ?? null) : null,
  );
  const valueText = $derived(
    mark === null
      ? "Use the arrow keys to read the values."
      : `${chart.x.label} ${mark.label}: ${chart.y.label} ${exact.format(mark.value)}`,
  );

  function pointer(event: PointerEvent): void {
    if (layout === null) return;
    const box = (event.currentTarget as HTMLElement).getBoundingClientRect();
    active = nearestMark(
      layout,
      event.clientX - box.left,
      event.clientY - box.top,
    );
  }

  function keydown(event: KeyboardEvent): void {
    if (layout === null) return;
    const last = layout.marks.length - 1;
    const step = KEYS[event.key];
    let next: number;
    if (event.key === "Home") next = 0;
    else if (event.key === "End") next = last;
    else if (step !== undefined) next = active === null ? 0 : active + step;
    else return;
    event.preventDefault();
    active = Math.min(Math.max(next, 0), last);
  }
</script>

<figure class="nodestep-chart">
  <figcaption class="nodestep-chart-title">{chart.title}</figcaption>
  <div class="chart-area" bind:clientWidth={width}>
    {#if layout !== null}
      <div
        class="nodestep-chart-canvas"
        role="slider"
        tabindex="0"
        aria-label={chart.title}
        aria-valuemin={1}
        aria-valuemax={layout.marks.length}
        aria-valuenow={(active ?? 0) + 1}
        aria-valuetext={valueText}
        onpointermove={pointer}
        onpointerleave={() => (active = null)}
        onkeydown={keydown}
        onblur={() => (active = null)}
      >
        <svg
          width={layout.width}
          height={layout.height}
          viewBox="0 0 {layout.width} {layout.height}"
          aria-hidden="true"
        >
          {#if layout.kind === "bar"}
            {#each layout.ticks as tick (tick.value)}
              <line
                class="nodestep-chart-grid"
                x1={tick.position}
                x2={tick.position}
                y1={layout.top}
                y2={layout.bottom}
              />
              {#if tick.text !== ""}
                <text
                  class="nodestep-chart-tick"
                  x={tick.position}
                  y={layout.bottom + 16}
                  text-anchor="middle"
                >
                  {tick.text}
                </text>
              {/if}
            {/each}
            <line
              class="nodestep-chart-axis"
              x1={layout.baseline}
              x2={layout.baseline}
              y1={layout.top}
              y2={layout.bottom}
            />
            <text class="nodestep-chart-axis-title" x={0} y={layout.top - 12}
              >{chart.x.label}</text
            >
            <text
              class="nodestep-chart-axis-title"
              x={(layout.left + layout.right) / 2}
              y={layout.height - 6}
              text-anchor="middle"
            >
              {chart.y.label}
            </text>
            {#each layout.marks as item, index (index)}
              <text
                class="nodestep-chart-category"
                x={layout.labelEnd}
                y={item.labelY}
                text-anchor={layout.labelAnchor}
                dominant-baseline="middle"
              >
                {#if item.short !== item.label}<title>{item.label}</title>{/if}{item.short}
              </text>
              <path
                class="nodestep-chart-bar"
                class:nodestep-chart-active={index === active}
                d={layout.paths[index]}
              />
              <text
                class="nodestep-chart-value"
                x={item.value < 0 ? item.x - 4 : item.x + 4}
                y={item.y}
                text-anchor={item.value < 0 ? "end" : "start"}
                dominant-baseline="middle"
              >
                {item.text}
              </text>
            {/each}
          {:else}
            {#each layout.ticks as tick (tick.value)}
              <line
                class="nodestep-chart-grid"
                x1={layout.left}
                x2={layout.right}
                y1={tick.position}
                y2={tick.position}
              />
              <text
                class="nodestep-chart-tick"
                x={layout.left - 8}
                y={tick.position}
                text-anchor="end"
                dominant-baseline="middle"
              >
                {tick.text}
              </text>
            {/each}
            <line
              class="nodestep-chart-axis"
              x1={layout.left}
              x2={layout.right}
              y1={layout.bottom}
              y2={layout.bottom}
            />
            <text class="nodestep-chart-axis-title" x={0} y={layout.top - 12}
              >{chart.y.label}</text
            >
            <text
              class="nodestep-chart-axis-title"
              x={(layout.left + layout.right) / 2}
              y={layout.height - 6}
              text-anchor="middle"
            >
              {chart.x.label}
            </text>
            {#each layout.marks as item, index (index)}
              {#if index % layout.labelEvery === 0}
                <text
                  class="nodestep-chart-tick"
                  x={item.x}
                  y={layout.bottom + 16}
                  text-anchor={item.anchor}
                >
                  {#if item.short !== item.label}<title>{item.label}</title>{/if}{item.short}
                </text>
              {/if}
            {/each}
            {#if mark !== null}
              <line
                class="nodestep-chart-crosshair"
                x1={mark.x}
                x2={mark.x}
                y1={layout.top}
                y2={layout.bottom}
              />
            {/if}
            <path class="nodestep-chart-line" d={layout.paths[0]} />
            {#each layout.marks as item, index (index)}
              {#if layout.marks.length <= 24 || index === active}
                <circle
                  class="nodestep-chart-dot"
                  class:nodestep-chart-active={index === active}
                  cx={item.x}
                  cy={item.y}
                  r="4"
                />
              {/if}
            {/each}
          {/if}
        </svg>
        {#if mark !== null}
          <div
            class="nodestep-tooltip chart-tooltip"
            class:flip={mark.x > layout.width * 0.6}
            style:left="{mark.x}px"
            style:top="{mark.y}px"
          >
            <strong>{exact.format(mark.value)}</strong>
            <span class="nodestep-hint">{mark.label}</span>
          </div>
        {/if}
      </div>
    {:else if width > 0}
      <p class="nodestep-hint">This chart has no numeric values to draw.</p>
    {/if}
  </div>
  {#if points.length > 0}
    <details class="nodestep-details chart-data">
      <summary>Chart data</summary>
      <div class="nodestep-table-wrap table-wrap" role="region" aria-label="{chart.title} data">
        <table class="nodestep-table">
          <thead>
            <tr>
              <th scope="col">{chart.x.label}</th>
              <th scope="col" class="nodestep-num">{chart.y.label}</th>
            </tr>
          </thead>
          <tbody>
            {#each points as point, index (index)}
              <tr>
                <td>{point.label}</td>
                <td class="nodestep-num" title={cellTitle(point.value, exact)}>{exact.format(point.value)}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    </details>
  {/if}
</figure>
