<script lang="ts">
  import type { DatabaseSummary, ThreadSummary } from "../lib/api.ts";
  import { databaseTitle } from "../lib/databases.ts";
  import { formatShortTime, formatTime } from "../lib/format.ts";

  let {
    threads,
    databases,
    error,
    current,
    onopen,
    onnew,
    onclose,
  }: {
    threads: ThreadSummary[] | null;
    databases: DatabaseSummary[] | null;
    error: string | null;
    current: string | null;
    onopen: (thread: ThreadSummary) => void;
    onnew: () => void;
    onclose: () => void;
  } = $props();
</script>

<nav class="nodestep-panel nodestep-app-start" id="threads-panel" aria-labelledby="threads-heading">
  <div class="nodestep-panel-header">
    <h2 class="nodestep-panel-title" id="threads-heading" tabindex="-1">Threads</h2>
    <div class="nodestep-cluster">
      <button type="button" class="nodestep-button nodestep-button-secondary nodestep-button-small" onclick={onnew}>New chat</button>
      <button type="button" class="nodestep-button nodestep-button-quiet nodestep-button-small nodestep-app-close" onclick={onclose}
        >Close</button
      >
    </div>
  </div>
  <div class="nodestep-panel-body">
    {#if error !== null}
      <p class="nodestep-message nodestep-message-error">Could not load threads. {error}</p>
    {:else if threads === null}
      <p class="nodestep-hint">Loading…</p>
    {:else if threads.length === 0}
      <p class="nodestep-hint">No threads yet.</p>
    {:else}
      <ul class="nodestep-panel-list">
        {#each threads as thread (thread.id)}
          <li>
            <button
              type="button"
              class="nodestep-panel-item"
              aria-current={thread.id === current ? "true" : undefined}
              onclick={() => onopen(thread)}
            >
              <span class="nodestep-panel-item-line">
                <span class="nodestep-panel-item-title">{thread.title || "Untitled"}</span>
                <time class="nodestep-panel-item-time" datetime={thread.updated_at} title={formatTime(thread.updated_at)}
                  >{formatShortTime(thread.updated_at)}</time
                >
              </span>
              <span class="nodestep-panel-item-meta">{databaseTitle(databases, thread.database)}</span>
            </button>
          </li>
        {/each}
      </ul>
    {/if}
  </div>
</nav>
