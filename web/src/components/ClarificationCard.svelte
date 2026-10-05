<script lang="ts">
  import { tick } from "svelte";
  import { answerChoices, type ClarificationBlock, shownAnswer } from "../lib/turns.ts";

  let {
    clarification,
    active,
    closed,
    busy,
    onanswer,
  }: {
    clarification: ClarificationBlock;
    active: boolean;
    closed: boolean;
    busy: boolean;
    onanswer: (answer: string) => void;
  } = $props();

  const uid = $props.id();
  const choices = $derived(answerChoices(clarification));
  const answer = $derived(shownAnswer(clarification));
  let text = $state("");
  let card = $state<HTMLElement>();

  $effect(() => {
    if (active) card?.focus();
  });

  async function choose(answer: string): Promise<void> {
    const value = answer.trim();
    if (!active || busy || value === "") return;
    onanswer(value);
    await tick();
    card?.focus();
  }

  function submit(event: SubmitEvent): void {
    event.preventDefault();
    void choose(text);
  }
</script>

<section
  class="nodestep-card clarification"
  class:nodestep-card-waiting={active && clarification.answer === null}
  class:nodestep-card-answered={clarification.answer !== null || closed}
  aria-labelledby="{uid}-question"
  tabindex="-1"
  bind:this={card}
>
  <p class="nodestep-card-label">Question for you</p>
  <p id="{uid}-question" class="nodestep-card-title">{clarification.question}</p>
  {#if answer !== null}
    <div class="nodestep-answer">
      <p class="nodestep-answer-label">Your answer</p>
      <p class="nodestep-answer-text">{answer.text}</p>
      {#if answer.detail}<p class="nodestep-answer-detail">{answer.detail}</p>{/if}
    </div>
  {:else if closed}
    <p class="nodestep-card-body">Closed without an answer.</p>
  {:else if active}
    <div class="nodestep-stack">
      {#if choices.proposals.length > 0}
        <ul class="nodestep-choices" aria-labelledby="{uid}-question">
          {#each choices.proposals as proposal (proposal.id)}
            <li>
              <button
                type="button"
                class="nodestep-choice"
                aria-disabled={busy}
                onclick={() => choose(proposal.id)}
              >
                <span class="nodestep-choice-label">{proposal.label}</span>
                <span class="nodestep-choice-description">{proposal.description}</span>
              </button>
            </li>
          {/each}
        </ul>
      {/if}
      {#if choices.freeText}
        <form class="nodestep-field" onsubmit={submit}>
          <label class="nodestep-label" for="{uid}-answer"
            >{choices.proposals.length > 0 ? "Or describe what you mean" : "Your answer"}</label
          >
          <div class="nodestep-input-row">
            <input id="{uid}-answer" class="nodestep-input" type="text" autocomplete="off" bind:value={text} />
            <button type="submit" class="nodestep-button nodestep-button-primary" aria-disabled={busy || text.trim() === ""}
              >Send answer</button
            >
          </div>
        </form>
      {/if}
    </div>
  {/if}
</section>
