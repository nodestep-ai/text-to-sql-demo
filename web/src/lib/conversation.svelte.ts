import { type ApiClient, errorMessage, type ThreadSummary } from "./api.ts";
import type { ErrorFrame, Frame } from "./sse.ts";
import {
  type AssistantTurn,
  answerClarification,
  applyFrame,
  assistantTurn,
  editTurns,
  pendingClarification,
  playFrames,
  replayTurns,
  retractAnswer,
  syncTurns,
  type Turn,
  userTurn,
} from "./turns.ts";

type Outcome = "started" | "failed" | "cancelled";

function problemOf(turn: AssistantTurn): string {
  return turn.blocks.findLast((block): block is ErrorFrame => block.type === "error")?.message ?? "";
}

export class Conversation {
  turns = $state<Turn[]>([]);
  threadId = $state<string | null>(null);
  database = $state<string | null>(null);
  switched = $state(false);
  busy = $state(false);
  stopping = $state(false);
  error = $state<string | null>(null);
  pending = $derived.by(() => {
    const last = this.turns.at(-1);
    return last?.role === "assistant" && !last.streaming ? pendingClarification(last) : null;
  });
  streaming = $derived.by(() => {
    const last = this.turns.at(-1);
    return last?.role === "assistant" && last.streaming;
  });

  #api: ApiClient;
  #onfinish: () => void;
  #controller: AbortController | null = null;
  #stream: AbortController | null = null;
  #playing: Promise<Outcome> | null = null;
  #running = false;
  #stopRequested = false;

  constructor(api: ApiClient, onfinish: () => void) {
    this.#api = api;
    this.#onfinish = onfinish;
  }

  async send(message: string): Promise<boolean> {
    const database = this.database;
    if (this.busy || this.pending !== null || database === null) return false;
    const threadId = this.threadId;
    const previous = this.turns;
    this.switched = false;
    this.turns = [...previous, userTurn(message), assistantTurn()];
    const answer = this.turns.at(-1) as AssistantTurn;
    const outcome = await this.#play((signal) => this.#api.chat({ threadId, database, message }, signal));
    if (outcome !== "failed") return true;
    this.turns = previous;
    this.error = `Could not send the question. ${problemOf(answer)}`;
    return false;
  }

  async edit(index: number, message: string): Promise<void> {
    const turn = this.turns[index];
    const threadId = this.threadId;
    if (this.busy || threadId === null || turn?.role !== "user" || turn.id === null) return;
    const turnId = turn.id;
    const previous = this.turns;
    this.turns = editTurns(previous, index, message);
    const answer = this.turns.at(-1) as AssistantTurn;
    const outcome = await this.#play((signal) => this.#api.edit(threadId, turnId, message, signal));
    if (outcome !== "failed") return;
    this.turns = previous;
    this.error = `Could not edit the question. ${problemOf(answer)}`;
  }

  async showVersion(index: number, version: number): Promise<void> {
    const turn = this.turns[index];
    const threadId = this.threadId;
    if (this.busy || threadId === null || turn?.role !== "user" || turn.id === null) return;
    const turnId = turn.id;
    const controller = this.#start();
    try {
      const thread = await this.#api.version(threadId, turnId, version, controller.signal);
      if (!controller.signal.aborted) this.turns = replayTurns(thread.turns);
    } catch (error) {
      if (!controller.signal.aborted) this.error = `Could not show version ${version + 1}. ${errorMessage(error)}`;
    } finally {
      this.#stop(controller);
    }
  }

  stop(): void {
    const turn = this.turns.at(-1);
    if (this.#stream === null || turn?.role !== "assistant" || !turn.streaming) return;
    turn.stopped = true;
    this.stopping = true;
    if (this.#running) this.#stream.abort();
    else this.#stopRequested = true;
  }

  select(database: string): void {
    if (database === this.database) return;
    const open = this.threadId !== null || this.turns.length > 0;
    this.#clear();
    this.database = database;
    this.switched = open;
  }

  async answer(answer: string): Promise<void> {
    const turn = this.turns.at(-1);
    const threadId = this.threadId;
    if (this.busy || threadId === null || turn?.role !== "assistant") return;
    const block = answerClarification(turn, answer);
    if (block === null) return;
    const outcome = await this.#play((signal) => this.#api.resume(threadId, block.key, answer, signal));
    if (outcome !== "failed") return;
    retractAnswer(turn, block);
    const problem = turn.blocks.findLast((entry): entry is ErrorFrame => entry.type === "error");
    if (problem?.kind === "clarification_not_pending") turn.stopped = true;
  }

  async open(summary: ThreadSummary): Promise<void> {
    if (this.streaming && summary.id === this.threadId) return;
    await this.#leave();
    const controller = this.#start();
    this.threadId = summary.id;
    this.database = summary.database;
    this.switched = false;
    this.turns = [];
    try {
      const thread = await this.#api.thread(summary.id, controller.signal);
      if (!controller.signal.aborted) {
        this.database = thread.database;
        this.turns = replayTurns(thread.turns);
      }
    } catch (error) {
      if (!controller.signal.aborted) {
        this.threadId = null;
        this.error = `Could not open the thread. ${errorMessage(error)}`;
      }
    } finally {
      this.#stop(controller);
    }
  }

  async reset(): Promise<void> {
    await this.#leave();
    this.#clear();
  }

  async #leave(): Promise<void> {
    this.stop();
    await this.#playing;
  }

  #clear(): void {
    this.#controller?.abort();
    this.#controller = null;
    this.busy = false;
    this.stopping = false;
    this.error = null;
    this.threadId = null;
    this.switched = false;
    this.turns = [];
  }

  #play(frames: (signal: AbortSignal) => AsyncIterable<Frame>): Promise<Outcome> {
    const playing = this.#playAnswer(frames);
    this.#playing = playing;
    return playing;
  }

  async #playAnswer(frames: (signal: AbortSignal) => AsyncIterable<Frame>): Promise<Outcome> {
    const session = this.#start();
    const stream = new AbortController();
    this.#stream = stream;
    this.#running = false;
    this.#stopRequested = false;
    const signal = AbortSignal.any([session.signal, stream.signal]);
    const turn = this.turns.at(-1) as AssistantTurn;
    const started = await playFrames(turn, frames(signal), signal, (run) => {
      this.threadId = run.thread_id;
      this.database = run.database;
      this.#running = true;
      if (this.#stopRequested) stream.abort();
    });
    if (this.#stream === stream) this.#stream = null;
    if (session.signal.aborted) return "cancelled";
    if (stream.signal.aborted) await this.#halt(turn, session.signal);
    if (started) await this.#sync(session.signal);
    if (this.#stop(session)) {
      this.stopping = false;
      this.#onfinish();
    }
    return started ? "started" : "failed";
  }

  async #halt(turn: AssistantTurn, signal: AbortSignal): Promise<void> {
    const threadId = this.threadId;
    try {
      if (threadId !== null) await this.#api.stop(threadId, signal);
    } catch (error) {
      if (!signal.aborted) {
        const message = `The run may still be active on the server. ${errorMessage(error)}`;
        applyFrame(turn, { type: "error", kind: "stop", message });
      }
    } finally {
      if (!signal.aborted) this.stopping = false;
    }
  }

  async #sync(signal: AbortSignal): Promise<void> {
    const threadId = this.threadId;
    if (threadId === null) return;
    try {
      const thread = await this.#api.thread(threadId, signal);
      if (!signal.aborted) this.turns = syncTurns(this.turns, replayTurns(thread.turns));
    } catch {}
  }

  #start(): AbortController {
    this.#controller?.abort();
    const controller = new AbortController();
    this.#controller = controller;
    this.busy = true;
    this.error = null;
    return controller;
  }

  #stop(controller: AbortController): boolean {
    if (this.#controller !== controller) return false;
    this.#controller = null;
    this.busy = false;
    return true;
  }
}
