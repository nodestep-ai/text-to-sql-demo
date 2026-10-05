<script lang="ts">
  import { cellTitle, columnFormat, formatCell, formatDuration, plural, tableColumns } from "../lib/format.ts";
  import { sqlLines, tokenClass, tokenizeSql } from "../lib/sql.ts";
  import type { SqlResultFrame } from "../lib/sse.ts";

  let { result, currency }: { result: SqlResultFrame; currency: string | null } = $props();

  const COPIED_FOR_MS = 1500;

  const kinds = $derived(tableColumns(result.columns, result.rows));
  const formats = $derived(
    result.columns.map((column, index) =>
      kinds[index].numeric
        ? columnFormat(
            column,
            result.rows.map((row) => row[index]).filter((value): value is number => typeof value === "number"),
            currency,
          )
        : null,
    ),
  );
  const lines = $derived(sqlLines(tokenizeSql(result.sql)));
  let viewWidth = $state(0);
  let tableWidth = $state(0);
  let copyState = $state<"idle" | "copied" | "failed">("idle");
  let copyTimer: ReturnType<typeof setTimeout> | undefined;
  const clipped = $derived(tableWidth > viewWidth + 1);

  async function copy(): Promise<void> {
    clearTimeout(copyTimer);
    try {
      await navigator.clipboard.writeText(result.sql);
      copyState = "copied";
    } catch {
      copyState = "failed";
    }
    copyTimer = setTimeout(() => (copyState = "idle"), COPIED_FOR_MS);
  }

  $effect(() => () => clearTimeout(copyTimer));
</script>

<section class="nodestep-stack nodestep-stack-small">
  <details class="nodestep-code nodestep-code-wrap sql" open>
    <summary class="sql-summary"
      >SQL · {plural(result.row_count, "row")} · {formatDuration(result.elapsed_ms)}</summary
    >
    <div>
      <button
        type="button"
        class="nodestep-button nodestep-button-quiet nodestep-button-small sql-copy"
        class:is-copied={copyState === "copied"}
        onclick={copy}>{copyState === "copied" ? "Copied" : copyState === "failed" ? "Copy failed" : "Copy"}</button
      >
      <span class="nodestep-visually-hidden" role="status"
        >{copyState === "copied" ? "SQL copied" : copyState === "failed" ? "Could not copy the SQL" : ""}</span
      >
      <pre><code>{#each lines as line, index (index)}<span class="nodestep-code-line" style:--nodestep-indent={line.indent}
            >{line.lead}{#each line.tokens as token, position (position)}{@const name = tokenClass(token.kind)}{#if name === null}{token.text}{:else}<span
                  class={name}>{token.text}</span
                >{/if}{/each}</span
          >{/each}</code></pre>
    </div>
  </details>
  {#if result.rows.length === 0}
    <p class="nodestep-hint">No rows.</p>
  {:else}
    <div class="nodestep-table-wrap table-wrap" role="region" aria-label="Query result" bind:clientWidth={viewWidth}>
      <table class="nodestep-table" bind:clientWidth={tableWidth}>
        <thead>
          <tr>
            {#each result.columns as column, index (index)}
              <th scope="col" class:nodestep-num={kinds[index].numeric} class:wrap={kinds[index].wrap}>{column}</th>
            {/each}
          </tr>
        </thead>
        <tbody>
          {#each result.rows as row, rowIndex (rowIndex)}
            <tr>
              {#each row as cell, index (index)}
                <td
                  class:nodestep-num={kinds[index]?.numeric}
                  class:wrap={kinds[index]?.wrap}
                  class:null={cell === null}
                  title={cellTitle(cell, formats[index] ?? null)}>{formatCell(cell, formats[index] ?? null)}</td
                >
              {/each}
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
    {#if clipped}
      <p class="nodestep-hint">{plural(result.columns.length, "column")}. Scroll the table sideways to see them all.</p>
    {/if}
    {#if result.truncated}
      <p class="nodestep-hint">Showing the first {plural(result.rows.length, "row")}. The query returned more.</p>
    {/if}
  {/if}
</section>
