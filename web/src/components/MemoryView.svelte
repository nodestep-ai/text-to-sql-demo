<script lang="ts">
  import { tick } from "svelte";
  import type { MemoryEntry, MemoryText } from "../lib/api.ts";
  import { plural } from "../lib/format.ts";
  import MemoryForm from "./MemoryForm.svelte";

  let {
    entries,
    skipped,
    error,
    saving,
    onadd,
    onedit,
    ondelete,
  }: {
    entries: MemoryEntry[] | null;
    skipped: string[];
    error: string | null;
    saving: boolean;
    onadd: (text: MemoryText) => Promise<boolean>;
    onedit: (key: string, text: MemoryText) => Promise<boolean>;
    ondelete: (key: string) => Promise<boolean>;
  } = $props();

  type Mode = "list" | "add" | "edit" | "delete";

  let mode = $state<Mode>("list");
  let selected = $state<string | null>(null);
  let view = $state<HTMLElement>();

  function show(next: Mode, key: string | null = null): void {
    mode = next;
    selected = key;
  }

  async function back(focus: string): Promise<void> {
    show("list");
    await tick();
    view?.querySelector<HTMLElement>(focus)?.focus();
  }

  async function confirm(key: string): Promise<void> {
    show("delete", key);
    await tick();
    view?.querySelector<HTMLElement>(button(key, "confirm"))?.focus();
  }

  function button(key: string, action: "edit" | "delete" | "confirm"): string {
    return `[data-key="${CSS.escape(key)}"] [data-action="${action}"]`;
  }

  async function add(text: MemoryText): Promise<void> {
    if (await onadd(text)) await back('[data-action="add"]');
  }

  async function edit(key: string, text: MemoryText): Promise<void> {
    if (await onedit(key, text)) await back(button(key, "edit"));
  }

  async function remove(key: string): Promise<void> {
    if (saving) return;
    await ondelete(key);
    await back('[data-action="add"]');
  }
</script>

<div class="nodestep-stack nodestep-stack-small" bind:this={view}>
  <p class="nodestep-hint">
    The agent saves a memory when you ask it to remember something, and says so when an answer uses one. It searches the title and the text, so a short, specific title helps.
  </p>
  {#if mode === "add"}
    <MemoryForm
      label="New memory"
      {saving}
      onsave={(text) => void add(text)}
      oncancel={() => void back('[data-action="add"]')}
    />
  {:else}
    <div>
      <button
        type="button"
        class="nodestep-button nodestep-button-secondary nodestep-button-small"
        data-action="add"
        onclick={() => show("add")}>Add memory</button
      >
    </div>
  {/if}
  {#if error !== null}
    <p class="nodestep-message nodestep-message-error" role="alert">{error}</p>
  {/if}
  {#if entries === null}
    {#if error === null}<p class="nodestep-hint">Loading…</p>{/if}
  {:else if entries.length === 0}
    <p class="nodestep-hint">No memories yet.</p>
  {:else}
    <ul class="memory-list" aria-label="Memories">
      {#each entries as entry (entry.key)}
        <li class="memory-item" data-key={entry.key}>
          {#if mode === "edit" && selected === entry.key}
            <MemoryForm
              label="Edit memory"
              initial={entry}
              {saving}
              onsave={(text) => void edit(entry.key, text)}
              oncancel={() => void back(button(entry.key, "edit"))}
            />
          {:else}
            <div class="memory-head">
              <p class="memory-title">{entry.title}</p>
              {#if !(mode === "delete" && selected === entry.key)}
                <div class="nodestep-cluster memory-actions">
                  <button
                    type="button"
                    class="nodestep-button nodestep-button-quiet nodestep-button-small"
                    data-action="edit"
                    aria-label="Edit {entry.title}"
                    onclick={() => show("edit", entry.key)}>Edit</button
                  >
                  <button
                    type="button"
                    class="nodestep-button nodestep-button-quiet nodestep-button-small"
                    data-action="delete"
                    aria-label="Delete {entry.title}"
                    onclick={() => void confirm(entry.key)}>Delete</button
                  >
                </div>
              {/if}
            </div>
            <p class="memory-content">{entry.content}</p>
            {#if mode === "delete" && selected === entry.key}
              <div class="nodestep-cluster" role="group" aria-label="Delete this memory?">
                <span class="memory-question">Delete this memory?</span>
                <button
                  type="button"
                  class="nodestep-button nodestep-button-danger nodestep-button-small"
                  data-action="confirm"
                  aria-disabled={saving}
                  onclick={() => void remove(entry.key)}>Delete</button
                >
                <button
                  type="button"
                  class="nodestep-button nodestep-button-quiet nodestep-button-small"
                  onclick={() => void back(button(entry.key, "delete"))}>Cancel</button
                >
              </div>
            {/if}
          {/if}
        </li>
      {/each}
    </ul>
  {/if}
  {#if skipped.length > 0}
    <p class="nodestep-hint">{plural(skipped.length, "memory file")} left out: {skipped.join(", ")}.</p>
  {/if}
</div>
