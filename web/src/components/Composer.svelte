<script lang="ts">
  import { onMount } from "svelte";

  let {
    placeholder,
    blocked,
    streaming,
    label = "Question",
    onsend,
    onstop,
  }: {
    placeholder: string;
    blocked: boolean;
    streaming: boolean;
    label?: string;
    onsend: (message: string) => Promise<boolean> | boolean;
    onstop: () => void;
  } = $props();

  const uid = $props.id();
  let text = $state("");
  let field = $state<HTMLTextAreaElement>();

  onMount(() => field?.focus());

  async function submit(event?: SubmitEvent): Promise<void> {
    event?.preventDefault();
    const message = text.trim();
    if (blocked || message === "") return;
    text = "";
    if (!(await onsend(message)) && text === "") text = message;
  }

  function click(event: MouseEvent): void {
    if (!streaming) return;
    event.preventDefault();
    onstop();
  }

  function keydown(event: KeyboardEvent): void {
    if (event.key !== "Enter" || event.shiftKey || event.isComposing) return;
    event.preventDefault();
    void submit();
  }
</script>

<form class="nodestep-composer" onsubmit={submit}>
  <label for="{uid}-message" class="nodestep-visually-hidden">{label}</label>
  <textarea
    id="{uid}-message"
    class="nodestep-composer-field"
    rows="1"
    {placeholder}
    aria-describedby="{uid}-hint"
    bind:value={text}
    bind:this={field}
    onkeydown={keydown}></textarea>
  <p id="{uid}-hint" class="nodestep-visually-hidden">
    Enter sends. Shift+Enter adds a line.
  </p>
  <button
    type="submit"
    class="nodestep-composer-send"
    aria-label={streaming ? "Stop answer" : "Send"}
    aria-disabled={!streaming && (blocked || text.trim() === "")}
    onclick={click}
  >
    {#if streaming}
      <svg
        class="nodestep-composer-stop"
        viewBox="0 0 16 16"
        aria-hidden="true"
        focusable="false"
        ><rect x="3.5" y="3.5" width="9" height="9" rx="1.5" /></svg
      >
    {:else}
      <svg viewBox="0 0 16 16" aria-hidden="true" focusable="false"
        ><path d="M8 13V3.5M3.75 7.75 8 3.5l4.25 4.25" /></svg
      >
    {/if}
  </button>
</form>
