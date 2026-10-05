<script lang="ts">
  import { onMount, tick } from "svelte";
  import Composer from "./components/Composer.svelte";
  import DatabasePicker from "./components/DatabasePicker.svelte";
  import MemoryView from "./components/MemoryView.svelte";
  import SchemaView from "./components/SchemaView.svelte";
  import SidePanel, { type SideTab } from "./components/SidePanel.svelte";
  import ThemeButton from "./components/ThemeButton.svelte";
  import ThreadList from "./components/ThreadList.svelte";
  import TurnView from "./components/TurnView.svelte";
  import { ApiClient, ApiError, type DatabaseSummary, errorMessage, type Schema, type ThreadSummary } from "./lib/api.ts";
  import { Conversation } from "./lib/conversation.svelte.ts";
  import { databaseTitle, exampleQuestions, initialDatabase, listingKey, StoredChoice } from "./lib/databases.ts";
  import { currencyOf } from "./lib/format.ts";
  import { Memories } from "./lib/memories.svelte.ts";
  import { documentTitle, listedThreads, threadTitle } from "./lib/threads.ts";
  import { shownSuggestions } from "./lib/turns.ts";

  type Panel = "threads" | SideTab;

  const PICKER_ID = "database-picker";
  const SIDES: Record<Panel, "start" | "end"> = { threads: "start", schema: "end", memory: "end" };

  const api = new ApiClient();
  const choice = new StoredChoice("text-to-sql-demo.database");
  const memories = new Memories(api);
  const conversation = new Conversation(api, () => {
    void loadThreads();
    void loadDatabases();
    void memories.load();
  });

  let databases = $state<DatabaseSummary[] | null>(null);
  let databasesError = $state<string | null>(null);
  let threads = $state<ThreadSummary[] | null>(null);
  let threadsError = $state<string | null>(null);
  let schema = $state<Schema | null>(null);
  let schemaError = $state<string | null>(null);
  let panel = $state<Panel | null>(null);
  let side = $state<SideTab>("schema");
  let threadsToggle = $state<HTMLButtonElement>();
  let schemaToggle = $state<HTMLButtonElement>();
  let memoryToggle = $state<HTMLButtonElement>();
  let log = $state<HTMLElement>();

  const title = $derived(conversation.database === null ? null : databaseTitle(databases, conversation.database));
  const current = $derived(databases?.find((database) => database.id === conversation.database) ?? null);
  const currency = $derived(currencyOf(current?.description ?? null));
  const shownThreads = $derived(
    listedThreads(
      threads,
      conversation.threadId === null || conversation.database === null
        ? null
        : { id: conversation.threadId, title: threadTitle(conversation.turns), database: conversation.database },
    ),
  );
  const listing = $derived(conversation.database === null ? null : listingKey(databases, conversation.database));
  const pageTitle = $derived(
    documentTitle(
      conversation.threadId === null
        ? null
        : (shownThreads?.find((thread) => thread.id === conversation.threadId)?.title ?? null),
    ),
  );
  const blockedReason = $derived.by(() => {
    if (conversation.pending !== null) return "Answer the question above to continue.";
    if (conversation.database !== null || databases === null) return null;
    return databases.length === 0 ? "Add a database on the server to ask a question." : "Pick a database to ask a question.";
  });
  const status = $derived.by(() => {
    if (conversation.error !== null) return { text: conversation.error, error: true };
    if (conversation.stopping) return { text: "Stopping the run…", error: false };
    if (blockedReason !== null) return { text: blockedReason, error: false };
    return null;
  });

  async function loadDatabases(): Promise<void> {
    try {
      databases = await api.databases();
      databasesError = null;
      const initial = initialDatabase(databases, choice.read());
      if (conversation.database === null && initial !== null) conversation.select(initial);
    } catch (error) {
      databasesError = errorMessage(error);
    }
  }

  async function loadThreads(): Promise<void> {
    try {
      threads = await api.threads();
      threadsError = null;
    } catch (error) {
      threadsError = errorMessage(error);
    }
  }

  async function openPanel(name: Panel): Promise<void> {
    panel = name;
    if (name !== "threads") showTab(name);
    await tick();
    document.getElementById(name === "threads" ? "threads-heading" : `${name}-tab`)?.focus();
  }

  async function closePanel(): Promise<void> {
    const closed = panel;
    panel = null;
    await tick();
    ({ threads: threadsToggle, schema: schemaToggle, memory: memoryToggle })[closed ?? "threads"]?.focus();
  }

  function showTab(tab: SideTab): void {
    side = tab;
    if (panel === "schema" || panel === "memory") panel = tab;
    if (tab === "memory") void memories.load();
  }

  function togglePanel(name: Panel): void {
    void (panel === name ? closePanel() : openPanel(name));
  }

  function leaveThreads(): void {
    if (panel === "threads") void closePanel();
  }

  function ask(question: string): Promise<boolean> {
    const sent = conversation.send(question);
    void revealLatestQuestion();
    return sent;
  }

  async function revealLatestQuestion(): Promise<void> {
    await tick();
    const question = [...(log?.querySelectorAll(".nodestep-chat-message-user") ?? [])].at(-1);
    if (log === undefined || question === undefined) return;
    const offset = question.getBoundingClientRect().top - log.getBoundingClientRect().top;
    log.scrollTop += offset - parseFloat(getComputedStyle(log).scrollPaddingTop);
  }

  function keydown(event: KeyboardEvent): void {
    if (event.key === "Escape" && !event.defaultPrevented && panel !== null) void closePanel();
  }

  $effect(() => {
    if (conversation.database !== null) choice.write(conversation.database);
  });

  $effect(() => {
    document.title = pageTitle;
  });

  $effect(() => {
    const database = conversation.database;
    schema = null;
    schemaError = null;
    if (database === null || listing === null) return;
    const controller = new AbortController();
    api.schema(database, controller.signal).then(
      (loaded) => {
        if (!controller.signal.aborted) schema = loaded;
      },
      (error: unknown) => {
        if (controller.signal.aborted) return;
        schemaError = errorMessage(error);
        if (error instanceof ApiError && error.status === 404) void loadDatabases();
      },
    );
    return () => controller.abort();
  });

  onMount(() => {
    void loadDatabases();
    void loadThreads();
    void memories.load();
  });
</script>

<svelte:window onfocus={() => void loadDatabases()} onkeydown={keydown} />

<a class="nodestep-skip-link" href="#chat">Skip to the conversation</a>
<div class="nodestep-app nodestep-app-fixed" data-panel={panel === null ? undefined : SIDES[panel]}>
  <header class="nodestep-topbar">
    <button
      type="button"
      class="nodestep-button nodestep-button-quiet nodestep-button-small nodestep-app-toggle nodestep-app-toggle-start toggle"
      aria-label="Threads"
      aria-expanded={panel === "threads"}
      aria-controls="threads-panel"
      bind:this={threadsToggle}
      onclick={() => togglePanel("threads")}
      ><svg class="toggle-icon" viewBox="0 0 16 16" aria-hidden="true" focusable="false"
        ><path d="M2.5 4h11M2.5 8h11M2.5 12h11" /></svg
      ><span class="toggle-label">Threads</span></button
    >
    <h1 class="nodestep-brand">
      <svg class="nodestep-brand-mark" viewBox="0 0 64 64" aria-hidden="true" focusable="false"
        ><path
          d="M14 50V14L50 50V14"
          stroke="currentColor"
          stroke-width="6"
          stroke-linecap="round"
          stroke-linejoin="round"
        /><circle cx="14" cy="50" r="6" fill="currentColor" /><circle cx="14" cy="14" r="6" fill="currentColor" /><circle
          cx="50"
          cy="50"
          r="6"
          fill="currentColor"
        /><circle class="nodestep-mark-accent-fill" cx="50" cy="14" r="7" /></svg
      >text-to-sql-demo</h1
    >
    <div class="nodestep-topbar-end">
      <button
        type="button"
        class="nodestep-button nodestep-button-quiet nodestep-button-small nodestep-app-toggle nodestep-app-toggle-end toggle"
        aria-label="Schema"
        aria-expanded={panel === "schema"}
        aria-controls="side-panel"
        bind:this={schemaToggle}
        onclick={() => togglePanel("schema")}
        ><svg class="toggle-icon" viewBox="0 0 16 16" aria-hidden="true" focusable="false"
          ><ellipse cx="8" cy="3.75" rx="5.25" ry="2" /><path
            d="M2.75 3.75v8.5c0 1.1 2.35 2 5.25 2s5.25-.9 5.25-2v-8.5M2.75 8c0 1.1 2.35 2 5.25 2s5.25-.9 5.25-2"
          /></svg
        ><span class="toggle-label">Schema</span></button
      >
      <button
        type="button"
        class="nodestep-button nodestep-button-quiet nodestep-button-small nodestep-app-toggle nodestep-app-toggle-end toggle"
        aria-label="Memory"
        aria-expanded={panel === "memory"}
        aria-controls="side-panel"
        bind:this={memoryToggle}
        onclick={() => togglePanel("memory")}
        ><svg class="toggle-icon" viewBox="0 0 16 16" aria-hidden="true" focusable="false"
          ><path d="M4.25 2.25h7.5v11.5L8 11l-3.75 2.75z" /></svg
        ><span class="toggle-label">Memory</span></button
      >
      <ThemeButton />
    </div>
  </header>
  <ThreadList
    threads={shownThreads}
    {databases}
    error={threadsError}
    current={conversation.threadId}
    onopen={(thread) => {
      leaveThreads();
      void conversation.open(thread);
    }}
    onnew={() => {
      leaveThreads();
      void conversation.reset();
    }}
    onclose={() => void closePanel()}
  />
  <main class="nodestep-app-main nodestep-chat chat" id="chat" tabindex="-1">
    <div class="nodestep-chat-log" role="log" bind:this={log} aria-label="Conversation" aria-busy={conversation.busy}>
      <div class="nodestep-page nodestep-page-narrow nodestep-chat-turns" class:is-empty={conversation.turns.length === 0}>
        {#each conversation.turns as turn, index (index)}
          <TurnView
            {turn}
            pending={conversation.pending}
            busy={conversation.busy}
            suggestions={shownSuggestions(conversation.turns, index)}
            {currency}
            onanswer={(answer) => void conversation.answer(answer)}
            onedit={(message) => void conversation.edit(index, message)}
            onversion={(version) => void conversation.showVersion(index, version)}
            onsuggest={(question) => void ask(question)}
          />
        {:else}
          {#if conversation.busy}
            <p class="nodestep-hint">Loading the thread…</p>
          {:else if conversation.database !== null && title !== null}
            <section class="welcome" aria-labelledby="welcome-heading">
              {#if conversation.switched}
                <p class="nodestep-message nodestep-message-info">Database changed to {title}. This is a new chat.</p>
              {/if}
              <h2 id="welcome-heading">Ask about {title}</h2>
              <p class="nodestep-hint welcome-hint">
                Each answer shows the SQL that was run and its result. Try one of these questions:
              </p>
              <ul class="example-grid">
                {#each exampleQuestions(current) as question (question)}
                  <li>
                    <button type="button" class="nodestep-choice example" onclick={() => void ask(question)}
                      >{question}</button
                    >
                  </li>
                {/each}
              </ul>
            </section>
          {:else}
            <p class="nodestep-hint">No messages yet.</p>
          {/if}
        {/each}
      </div>
    </div>
    <div class="nodestep-chat-dock">
      <div class="nodestep-page nodestep-page-narrow nodestep-stack nodestep-stack-small">
        <div class="status" role="status">
          {#if status !== null}<p class:status-error={status.error}>{status.text}</p>{/if}
        </div>
        <Composer
          blocked={conversation.busy || conversation.pending !== null || conversation.database === null}
          streaming={conversation.streaming}
          placeholder={title === null ? "Ask a question about the database" : `Ask a question about ${title}`}
          onsend={ask}
          onstop={() => conversation.stop()}
        />
      </div>
    </div>
  </main>
  <SidePanel tab={side} ontab={showTab} onclose={() => void closePanel()}>
    {#snippet schemaTab()}
      <SchemaView database={conversation.database} summary={current} {schema} error={schemaError}>
        <DatabasePicker
          id={PICKER_ID}
          {databases}
          error={databasesError}
          selected={conversation.database}
          disabled={conversation.busy}
          onselect={(database) => conversation.select(database)}
        />
      </SchemaView>
    {/snippet}
    {#snippet memoryTab()}
      <MemoryView
        entries={memories.entries}
        skipped={memories.skipped}
        error={memories.error}
        saving={memories.saving}
        onadd={(text) => memories.add(text)}
        onedit={(key, text) => memories.edit(key, text)}
        ondelete={(key) => memories.remove(key)}
      />
    {/snippet}
  </SidePanel>
</div>
