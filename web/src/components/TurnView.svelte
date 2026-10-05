<script lang="ts">
  import { plural } from "../lib/format.ts";
  import { type ClarificationBlock, clarificationClosed, toolLabel, toolState, type Turn } from "../lib/turns.ts";
  import Badge from "./Badge.svelte";
  import Chart from "./Chart.svelte";
  import Chips from "./Chips.svelte";
  import ClarificationCard from "./ClarificationCard.svelte";
  import Message from "./Message.svelte";
  import SqlResult from "./SqlResult.svelte";
  import UserTurn from "./UserTurn.svelte";

  let {
    turn,
    pending,
    busy,
    suggestions,
    currency,
    onanswer,
    onedit,
    onversion,
    onsuggest,
  }: {
    turn: Turn;
    pending: ClarificationBlock | null;
    busy: boolean;
    suggestions: string[];
    currency: string | null;
    onanswer: (answer: string) => void;
    onedit: (message: string) => void;
    onversion: (version: number) => void;
    onsuggest: (question: string) => void;
  } = $props();
</script>

{#if turn.role === "user"}
  <UserTurn {turn} {busy} {onedit} {onversion} />
{:else}
  <Message role="assistant" busy={turn.streaming}>
    {#each turn.blocks as block (block)}
      {#if block.type === "text"}
        <p class="nodestep-chat-text">{block.text}</p>
      {:else if block.type === "tool"}
        {@const label = toolLabel(block, turn)}
        <p class="tool tool-{block.status}">
          {#if label === null}
            <code class="nodestep-tag tool-name">{block.name}</code>
            <span class="tool-status">{toolState(block, turn)}</span>
          {:else}
            <span class="tool-label">{label}</span>
          {/if}
          {#if block.summary}<span class="tool-summary">{block.summary}</span>{/if}
        </p>
      {:else if block.type === "sql_result"}
        <SqlResult result={block} {currency} />
      {:else if block.type === "chart"}
        <Chart chart={block} {currency} />
      {:else if block.type === "clarification"}
        <ClarificationCard
          clarification={block}
          active={block === pending}
          closed={clarificationClosed(turn, block, pending)}
          {busy}
          {onanswer}
        />
      {:else if block.type === "error"}
        <p class="nodestep-message nodestep-message-error">Error ({block.kind}): {block.message}</p>
      {/if}
    {/each}
    {#if turn.streaming}
      <p class="nodestep-hint"><span class="nodestep-spinner" aria-hidden="true"></span> Working…</p>
    {/if}
    {#if turn.stopped}
      <p><Badge status="stopped" /></p>
    {/if}
    {#if turn.usage !== null}
      <footer class="usage">
        {turn.usage.models.join(", ")} · {plural(turn.usage.input_tokens, "input token")} · {plural(
          turn.usage.output_tokens,
          "output token",
        )}
      </footer>
    {/if}
    {#if suggestions.length > 0}
      <Chips label="Follow-up questions" items={suggestions} disabled={busy} onchoose={onsuggest} />
    {/if}
  </Message>
{/if}
