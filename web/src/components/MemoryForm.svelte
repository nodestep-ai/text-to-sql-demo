<script lang="ts">
  import { onMount, untrack } from "svelte";
  import type { MemoryText } from "../lib/api.ts";

  let {
    label,
    initial = null,
    saving,
    onsave,
    oncancel,
  }: {
    label: string;
    initial?: MemoryText | null;
    saving: boolean;
    onsave: (text: MemoryText) => void;
    oncancel: () => void;
  } = $props();

  const TITLE_LENGTH = 120;
  const CONTENT_LENGTH = 2000;

  const uid = $props.id();
  let title = $state(untrack(() => initial?.title ?? ""));
  let content = $state(untrack(() => initial?.content ?? ""));
  let field = $state<HTMLInputElement>();
  const blank = $derived(title.trim() === "" || content.trim() === "");

  onMount(() => field?.focus());

  function submit(event: SubmitEvent): void {
    event.preventDefault();
    if (saving || blank) return;
    onsave({ title: title.trim(), content: content.trim() });
  }

  function keydown(event: KeyboardEvent): void {
    if (event.key !== "Escape" || event.isComposing) return;
    event.preventDefault();
    oncancel();
  }
</script>

<form class="nodestep-stack nodestep-stack-small memory-form" aria-label={label} onsubmit={submit}>
  <div class="nodestep-field">
    <label class="nodestep-label" for="{uid}-title">Title</label>
    <input
      id="{uid}-title"
      class="nodestep-input"
      maxlength={TITLE_LENGTH}
      autocomplete="off"
      onkeydown={keydown}
      bind:value={title}
      bind:this={field}
    />
  </div>
  <div class="nodestep-field">
    <label class="nodestep-label" for="{uid}-content">Text</label>
    <textarea
      id="{uid}-content"
      class="nodestep-textarea"
      rows="3"
      maxlength={CONTENT_LENGTH}
      onkeydown={keydown}
      bind:value={content}
    ></textarea>
  </div>
  <div class="nodestep-cluster memory-form-actions">
    <button type="button" class="nodestep-button nodestep-button-secondary nodestep-button-small" onclick={oncancel}>Cancel</button>
    <button type="submit" class="nodestep-button nodestep-button-primary nodestep-button-small" aria-disabled={saving || blank}
      >Save</button
    >
  </div>
</form>
