<script lang="ts">
  import type { Snippet } from "svelte";

  let {
    role,
    text,
    label,
    busy = false,
    actions,
    children,
  }: {
    role: "user" | "assistant";
    text?: string;
    label?: string;
    busy?: boolean;
    actions?: Snippet;
    children?: Snippet;
  } = $props();
</script>

<article
  class="nodestep-chat-message"
  class:nodestep-chat-message-user={role === "user"}
  aria-label={label ?? (role === "user" ? "Question" : "Answer")}
  aria-busy={busy || undefined}
>
  {#if text !== undefined}
    <p
      class={role === "user"
        ? "nodestep-chat-bubble nodestep-chat-text"
        : "nodestep-chat-text"}
    >
      {text}
    </p>
  {/if}
  {#if actions}
    <div class="nodestep-chat-actions">{@render actions()}</div>
  {/if}
  {@render children?.()}
</article>
