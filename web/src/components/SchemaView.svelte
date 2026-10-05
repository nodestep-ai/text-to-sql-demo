<script lang="ts">
  import type { Snippet } from "svelte";
  import type { DatabaseSummary, Schema } from "../lib/api.ts";
  import { databaseFacts } from "../lib/databases.ts";
  import { plural } from "../lib/format.ts";

  let {
    database,
    summary,
    schema,
    error,
    children,
  }: {
    database: string | null;
    summary: DatabaseSummary | null;
    schema: Schema | null;
    error: string | null;
    children: Snippet;
  } = $props();
</script>

<div class="panel-about">
  {@render children()}
  {#if summary?.description}
    <p class="nodestep-hint panel-description">{summary.description}</p>
  {/if}
  {#if summary !== null}
    <p class="nodestep-hint panel-facts">{databaseFacts(summary)}</p>
  {/if}
</div>
{#if database === null}
  <p class="nodestep-hint">Pick a database to see its tables.</p>
{:else if error !== null}
  <p class="nodestep-message nodestep-message-error">Could not load the schema. {error}</p>
{:else if schema === null}
  <p class="nodestep-hint">Loading…</p>
{:else if schema.tables.length === 0}
  <p class="nodestep-hint">The database has no tables.</p>
{:else}
  {#each schema.tables as table (table.name)}
    <details class="schema-table">
      <summary>
        <span class="schema-name">{table.name}</span>
        <span class="schema-count">{plural(table.row_count, "row")}</span>
      </summary>
      <ul class="schema-columns">
        {#each table.columns as column (column.name)}
          <li>
            <span class="schema-column">{column.name}</span>
            <span class="schema-type">{column.type || "no type"}</span>
            {#if column.primary_key}<abbr class="schema-key" title="Primary key">PK</abbr>{/if}
            {#if column.references !== null}
              <span class="schema-reference">
                <span class="nodestep-visually-hidden">references</span>
                <span aria-hidden="true">→</span>
                {column.references.table}.{column.references.column}
              </span>
            {/if}
          </li>
        {/each}
      </ul>
    </details>
  {/each}
{/if}
