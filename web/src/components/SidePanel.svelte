<script lang="ts" module>
  export type SideTab = "schema" | "memory";
</script>

<script lang="ts">
  import { type Snippet, tick } from "svelte";

  let {
    tab,
    ontab,
    onclose,
    schemaTab,
    memoryTab,
  }: {
    tab: SideTab;
    ontab: (tab: SideTab) => void;
    onclose: () => void;
    schemaTab: Snippet;
    memoryTab: Snippet;
  } = $props();

  const TABS: { id: SideTab; label: string }[] = [
    { id: "schema", label: "Schema" },
    { id: "memory", label: "Memory" },
  ];

  async function keydown(event: KeyboardEvent): Promise<void> {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    const next = tab === "schema" ? "memory" : "schema";
    ontab(next);
    await tick();
    document.getElementById(`${next}-tab`)?.focus();
  }
</script>

<section class="nodestep-panel nodestep-panel-end nodestep-app-end" id="side-panel" aria-label="Schema and memory">
  <div class="nodestep-panel-header side-header">
    <div class="nodestep-tabs side-tabs" role="tablist" aria-label="Schema and memory">
      {#each TABS as item (item.id)}
        <button
          type="button"
          role="tab"
          class="nodestep-tab"
          id="{item.id}-tab"
          aria-selected={tab === item.id}
          aria-controls="side-tabpanel"
          tabindex={tab === item.id ? 0 : -1}
          onclick={() => ontab(item.id)}
          onkeydown={keydown}>{item.label}</button
        >
      {/each}
    </div>
    <button type="button" class="nodestep-button nodestep-button-quiet nodestep-button-small nodestep-app-close" onclick={onclose}
      >Close</button
    >
  </div>
  <div class="nodestep-panel-body" id="side-tabpanel" role="tabpanel" aria-labelledby="{tab}-tab">
    {#if tab === "schema"}
      {@render schemaTab()}
    {:else}
      {@render memoryTab()}
    {/if}
  </div>
</section>
