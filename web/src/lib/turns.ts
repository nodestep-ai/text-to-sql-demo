import { ApiError, errorMessage, type StoredTurn, type TurnVersion } from "./api.ts";
import type {
  ChartFrame,
  ClarificationFrame,
  ErrorFrame,
  Frame,
  Proposal,
  RunFrame,
  SqlResultFrame,
  ToolFrame,
  UsageFrame,
} from "./sse.ts";

export interface TextBlock {
  type: "text";
  text: string;
}

export interface ClarificationBlock extends ClarificationFrame {
  answer: string | null;
}

export type Block = TextBlock | ToolFrame | SqlResultFrame | ChartFrame | ClarificationBlock | ErrorFrame;

export interface Usage {
  models: string[];
  input_tokens: number;
  output_tokens: number;
}

export interface UserTurn {
  role: "user";
  id: string | null;
  text: string;
  version: TurnVersion | null;
}

export interface AssistantTurn {
  role: "assistant";
  blocks: Block[];
  usage: Usage | null;
  streaming: boolean;
  stopped: boolean;
  suggestions: string[];
  runStart: number;
}

export type Turn = UserTurn | AssistantTurn;

export function userTurn(text: string): UserTurn {
  return { role: "user", id: null, text, version: null };
}

export function assistantTurn(): AssistantTurn {
  return { role: "assistant", blocks: [], usage: null, streaming: false, stopped: false, suggestions: [], runStart: 0 };
}

export function applyFrame(turn: AssistantTurn, frame: Frame): void {
  switch (frame.type) {
    case "token":
      appendText(turn, frame.text);
      break;
    case "final":
      finishText(turn, frame.text);
      break;
    case "tool":
      updateTool(turn, frame);
      break;
    case "clarification":
      turn.blocks.push({ ...frame, answer: null });
      break;
    case "usage":
      addUsage(turn, frame);
      break;
    case "stopped":
      turn.stopped = true;
      break;
    case "suggestions":
      turn.suggestions = [...frame.questions];
      break;
    case "sql_result":
    case "chart":
    case "error":
      turn.blocks.push(frame);
      break;
    case "run":
      turn.runStart = turn.blocks.length;
      break;
    case "done":
      break;
  }
}

export function pendingClarification(turn: AssistantTurn): ClarificationBlock | null {
  if (turn.stopped) return null;
  const last = turn.blocks.findLast((block) => block.type !== "text");
  return last?.type === "clarification" && last.answer === null ? last : null;
}

export function clarificationClosed(turn: AssistantTurn, block: ClarificationBlock, pending: ClarificationBlock | null): boolean {
  return block.answer === null && block !== pending && !turn.streaming;
}

export function answerClarification(turn: AssistantTurn, answer: string): ClarificationBlock | null {
  const pending = pendingClarification(turn);
  if (pending === null) return null;
  pending.answer = answer;
  turn.blocks.splice(turn.blocks.indexOf(pending), 1);
  turn.blocks.push(pending);
  return pending;
}

export function retractAnswer(turn: AssistantTurn, block: ClarificationBlock): void {
  block.answer = null;
  turn.blocks.splice(turn.blocks.indexOf(block), 1);
  turn.blocks.push(block);
}

export function answerChoices(clarification: ClarificationFrame): { proposals: Proposal[]; freeText: boolean } {
  const proposals = clarification.proposals;
  return { proposals, freeText: clarification.allow_free_text || proposals.length === 0 };
}

export function chosenProposal(block: ClarificationBlock): Proposal | null {
  if (block.answer === null) return null;
  return block.proposals.find((proposal) => proposal.id === block.answer) ?? null;
}

export interface ShownAnswer {
  text: string;
  detail: string | null;
}

export function shownAnswer(block: ClarificationBlock): ShownAnswer | null {
  if (block.answer === null) return null;
  const chosen = chosenProposal(block);
  return chosen === null ? { text: block.answer, detail: null } : { text: chosen.label, detail: chosen.description };
}

export function replayTurns(turns: StoredTurn[]): Turn[] {
  const replayed: Turn[] = [];
  let resumed: AssistantTurn | null = null;
  for (const stored of turns) {
    const previous = replayed.at(-1);
    if (stored.role === "user") {
      resumed = previous?.role === "assistant" && answers(previous, stored) ? previous : null;
      if (resumed === null) replayed.push({ role: "user", id: stored.id, text: stored.text, version: stored.version });
      else answerClarification(resumed, stored.text);
      continue;
    }
    const turn = resumed ?? assistantTurn();
    turn.runStart = turn.blocks.length;
    for (const frame of stored.frames) applyFrame(turn, frame);
    if (!stored.frames.some((frame) => frame.type === "final")) applyFrame(turn, { type: "final", text: stored.text });
    turn.stopped ||= stored.status === "stopped";
    if (turn !== resumed) replayed.push(turn);
    resumed = null;
  }
  return replayed;
}

export function editTurns(turns: Turn[], index: number, message: string): Turn[] {
  const turn = turns[index] as UserTurn;
  const count = (turn.version?.count ?? 1) + 1;
  return [...turns.slice(0, index), { ...turn, text: message, version: { index: count - 1, count } }, assistantTurn()];
}

export function syncTurns(live: Turn[], stored: Turn[]): Turn[] {
  if (live.length !== stored.length || live.some((turn, index) => turn.role !== stored[index].role)) return stored;
  return live.map((turn, index) => {
    const source = stored[index];
    if (turn.role === "user" && source.role === "user") return { ...turn, id: source.id, version: source.version };
    if (turn.role === "assistant" && source.role === "assistant") {
      return { ...turn, stopped: turn.stopped || source.stopped };
    }
    return turn;
  });
}

export function shownSuggestions(turns: Turn[], index: number): string[] {
  const turn = turns[index];
  if (index !== turns.length - 1 || turn?.role !== "assistant" || turn.streaming) return [];
  return turn.suggestions;
}

export type ToolState = "running" | "done" | "failed" | "stopped" | "not finished";

export function toolState(tool: ToolFrame, turn: AssistantTurn): ToolState {
  if (tool.status === "finished") return "done";
  if (tool.status === "error") return "failed";
  if (turn.streaming) return "running";
  return turn.stopped ? "stopped" : "not finished";
}

interface ToolPhrases {
  running: string;
  done: string;
  failed: string;
}

function toolPhrases(tool: ToolFrame): ToolPhrases | null {
  switch (tool.name) {
    case "save_memory":
      return { running: "Saving to memory", done: "Saved to memory", failed: "Could not save to memory" };
    case "search_memory":
      return { running: "Searching memory", done: "Searched memory", failed: "Could not search memory" };
    case "list_memories":
      return { running: "Listing memories", done: "Listed memories", failed: "Could not list memories" };
    case "delete_memory":
      return { running: "Deleting from memory", done: "Deleted from memory", failed: "Could not delete from memory" };
    case "load_skill": {
      const name = tool.arguments?.name;
      const skill = typeof name === "string" && name !== "" ? `skill ${name}` : "a skill";
      return { running: `Loading ${skill}`, done: `Loaded ${skill}`, failed: `Could not load ${skill}` };
    }
    case "analyze_topics": {
      const topics = tool.arguments?.topics;
      const agents = Array.isArray(topics) ? `${topics.length} sub-agents` : "sub-agents";
      return { running: `Running ${agents}`, done: `Ran ${agents}`, failed: "Could not run the sub-agents" };
    }
    default:
      return null;
  }
}

export function toolLabel(tool: ToolFrame, turn: AssistantTurn): string | null {
  const phrases = toolPhrases(tool);
  if (phrases === null) return null;
  const state = toolState(tool, turn);
  if (state === "done") return phrases.done;
  if (state === "failed") return phrases.failed;
  if (state === "running") return phrases.running;
  return `${phrases.running} (${state})`;
}

export function versionLabel(version: TurnVersion): string {
  return `${version.index + 1}/${version.count}`;
}

export function versionStep(version: TurnVersion, delta: number): number | null {
  const index = version.index + delta;
  return index >= 0 && index < version.count ? index : null;
}

export async function playFrames(
  turn: AssistantTurn,
  frames: AsyncIterable<Frame>,
  signal: AbortSignal,
  onrun: (run: RunFrame) => void,
): Promise<boolean> {
  turn.streaming = true;
  let started = false;
  let done = false;
  try {
    for await (const frame of frames) {
      if (signal.aborted) return started;
      if (frame.type === "run") {
        started = true;
        onrun(frame);
      }
      done ||= frame.type === "done";
      applyFrame(turn, frame);
    }
    if (!done && !signal.aborted) {
      applyFrame(turn, { type: "error", kind: "stream", message: "The stream ended before the run finished." });
    }
  } catch (error) {
    if (!signal.aborted) {
      const kind = error instanceof ApiError ? (error.code ?? "http") : "network";
      applyFrame(turn, { type: "error", kind, message: errorMessage(error) });
    }
  } finally {
    turn.streaming = false;
  }
  return started;
}

function answers(turn: AssistantTurn, stored: StoredTurn): boolean {
  return stored.answers !== null && pendingClarification(turn)?.key === stored.answers;
}

function appendText(turn: AssistantTurn, text: string): void {
  const last = turn.blocks.at(-1);
  if (last?.type === "text") last.text += text;
  else turn.blocks.push({ type: "text", text });
}

function finishText(turn: AssistantTurn, text: string): void {
  if (text === "") return;
  const last = turn.blocks.at(-1);
  if (last?.type === "text") last.text = text;
  else turn.blocks.push({ type: "text", text });
}

function updateTool(turn: AssistantTurn, frame: ToolFrame): void {
  const row = turn.blocks
    .slice(turn.runStart)
    .find((block): block is ToolFrame => block.type === "tool" && block.call_id === frame.call_id);
  if (row === undefined) {
    turn.blocks.push(frame);
    return;
  }
  row.status = frame.status;
  row.arguments = frame.arguments ?? row.arguments;
  row.summary = frame.summary ?? row.summary;
}

function addUsage(turn: AssistantTurn, frame: UsageFrame): void {
  if (turn.usage === null) turn.usage = { models: [], input_tokens: 0, output_tokens: 0 };
  const usage = turn.usage;
  if (!usage.models.includes(frame.model)) usage.models.push(frame.model);
  usage.input_tokens += frame.input_tokens;
  usage.output_tokens += frame.output_tokens;
}
