<script lang="ts">
  import type { DatabaseSummary } from "../lib/api.ts";
  import { pickerOptions } from "../lib/databases.ts";

  let {
    id,
    databases,
    error,
    selected,
    disabled,
    onselect,
  }: {
    id: string;
    databases: DatabaseSummary[] | null;
    error: string | null;
    selected: string | null;
    disabled: boolean;
    onselect: (database: string) => void;
  } = $props();

  const options = $derived(pickerOptions(databases ?? [], selected));
  const listed = $derived(databases?.some((database) => database.id === selected) ?? false);
  const problem = $derived.by(() => {
    if (error !== null) return `Could not load the databases. ${error}`;
    if (databases === null) return null;
    if (databases.length === 0) return "No databases found. Add a .sqlite file to the databases folder on the server.";
    if (selected !== null && !listed) return "This database is no longer on the server.";
    return null;
  });
</script>

<div class="nodestep-field picker">
  <label for={id} class="nodestep-label">Database</label>
  <select
    {id}
    class="nodestep-select"
    aria-describedby={problem === null ? undefined : `${id}-problem`}
    disabled={disabled || options.length === 0}
    bind:value={() => selected ?? "", (database: string) => onselect(database)}
  >
    {#if options.length === 0}
      <option value="">{databases === null && error === null ? "Loading…" : "None"}</option>
    {/if}
    {#each options as option (option.id)}
      <option value={option.id}>{option.label}</option>
    {/each}
  </select>
  {#if problem !== null}
    <p id="{id}-problem" class="nodestep-field-error">{problem}</p>
  {/if}
</div>
