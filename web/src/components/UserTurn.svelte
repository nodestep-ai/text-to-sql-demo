<script lang="ts">
  import { tick } from "svelte";
  import { type UserTurn, versionLabel, versionStep } from "../lib/turns.ts";
  import Message from "./Message.svelte";

  let {
    turn,
    busy,
    onedit,
    onversion,
  }: {
    turn: UserTurn;
    busy: boolean;
    onedit: (message: string) => void;
    onversion: (version: number) => void;
  } = $props();

  const uid = $props.id();
  let editing = $state(false);
  let draft = $state("");
  let field = $state<HTMLTextAreaElement>();
  let editButton = $state<HTMLButtonElement>();
  const previous = $derived(turn.version === null ? null : versionStep(turn.version, -1));
  const next = $derived(turn.version === null ? null : versionStep(turn.version, 1));

  async function start(): Promise<void> {
    if (busy) return;
    draft = turn.text;
    editing = true;
    await tick();
    field?.focus();
    field?.setSelectionRange(draft.length, draft.length);
  }

  async function finish(): Promise<void> {
    editing = false;
    await tick();
    editButton?.focus();
  }

  function save(event?: SubmitEvent): void {
    event?.preventDefault();
    const message = draft.trim();
    if (busy || message === "") return;
    onedit(message);
    void finish();
  }

  function keydown(event: KeyboardEvent): void {
    if (event.isComposing) return;
    if (event.key === "Escape") {
      event.preventDefault();
      void finish();
    } else if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      save();
    }
  }

  function show(version: number | null): void {
    if (!busy && version !== null) onversion(version);
  }
</script>

{#snippet actions()}
  {#if turn.version !== null}
    <div class="versions" role="group" aria-label="Versions of this question">
      <button
        type="button"
        class="nodestep-button nodestep-button-quiet nodestep-button-small nodestep-button-icon"
        aria-label="Previous version"
        aria-disabled={busy || previous === null}
        onclick={() => show(previous)}>‹</button
      >
      <span class="versions-label"><span class="nodestep-visually-hidden">Version </span>{versionLabel(turn.version)}</span>
      <button
        type="button"
        class="nodestep-button nodestep-button-quiet nodestep-button-small nodestep-button-icon"
        aria-label="Next version"
        aria-disabled={busy || next === null}
        onclick={() => show(next)}>›</button
      >
    </div>
  {/if}
  {#if turn.id !== null}
    <button
      type="button"
      class="nodestep-button nodestep-button-quiet nodestep-button-small"
      aria-label="Edit question"
      aria-disabled={busy}
      bind:this={editButton}
      onclick={() => void start()}>Edit</button
    >
  {/if}
{/snippet}

<Message
  role="user"
  text={editing ? undefined : turn.text}
  actions={editing || (turn.version === null && turn.id === null) ? undefined : actions}
>
  {#if editing}
    <form class="nodestep-composer nodestep-composer-stacked" onsubmit={save}>
      <label for="{uid}-edit" class="nodestep-visually-hidden">Edit the question</label>
      <textarea
        id="{uid}-edit"
        class="nodestep-composer-field"
        rows="3"
        aria-describedby="{uid}-hint"
        bind:value={draft}
        bind:this={field}
        onkeydown={keydown}
      ></textarea>
      <div class="nodestep-composer-bar">
        <p id="{uid}-hint" class="nodestep-hint">Enter saves. Shift+Enter adds a line. Escape cancels.</p>
        <div class="nodestep-cluster">
          <button type="button" class="nodestep-button nodestep-button-secondary nodestep-button-small" onclick={() => void finish()}
            >Cancel</button
          >
          <button
            type="submit"
            class="nodestep-button nodestep-button-primary nodestep-button-small"
            aria-disabled={busy || draft.trim() === ""}>Save</button
          >
        </div>
      </div>
    </form>
  {/if}
</Message>
