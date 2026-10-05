export type JsonValue = string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue };

export interface ChartAxis {
  field: string;
  label: string;
}

export interface RunFrame {
  type: "run";
  thread_id: string;
  run_id: string;
  database: string;
}

export interface TokenFrame {
  type: "token";
  text: string;
}

export interface ToolFrame {
  type: "tool";
  call_id: string;
  name: string;
  status: "started" | "finished" | "error";
  arguments: Record<string, JsonValue> | null;
  summary: string | null;
}

export interface SqlResultFrame {
  type: "sql_result";
  call_id: string;
  sql: string;
  columns: string[];
  rows: JsonValue[][];
  row_count: number;
  truncated: boolean;
  elapsed_ms: number;
}

export interface ChartFrame {
  type: "chart";
  call_id: string;
  kind: "bar" | "line";
  title: string;
  x: ChartAxis;
  y: ChartAxis;
  data: Record<string, JsonValue>[];
}

export interface Proposal {
  id: string;
  label: string;
  description: string;
}

export interface ClarificationFrame {
  type: "clarification";
  key: string;
  question: string;
  proposals: Proposal[];
  allow_free_text: boolean;
}

export interface FinalFrame {
  type: "final";
  text: string;
}

export interface UsageFrame {
  type: "usage";
  model: string;
  input_tokens: number;
  output_tokens: number;
}

export interface ErrorFrame {
  type: "error";
  kind: string;
  message: string;
}

export interface StoppedFrame {
  type: "stopped";
}

export interface SuggestionsFrame {
  type: "suggestions";
  questions: string[];
}

export interface DoneFrame {
  type: "done";
}

export type Frame =
  | RunFrame
  | TokenFrame
  | ToolFrame
  | SqlResultFrame
  | ChartFrame
  | ClarificationFrame
  | FinalFrame
  | UsageFrame
  | ErrorFrame
  | StoppedFrame
  | SuggestionsFrame
  | DoneFrame;

export type FrameType = Frame["type"];

export interface SseMessage {
  event: string;
  data: string;
}

const FRAME_TYPES: Record<FrameType, true> = {
  run: true,
  token: true,
  tool: true,
  sql_result: true,
  chart: true,
  clarification: true,
  final: true,
  usage: true,
  error: true,
  stopped: true,
  suggestions: true,
  done: true,
};

export class SseParser {
  #buffer = "";
  #skipNewline = false;
  #event = "";
  #data: string[] = [];

  push(chunk: string): SseMessage[] {
    if (chunk === "") return [];
    const fresh = this.#skipNewline && chunk.startsWith("\n") ? chunk.slice(1) : chunk;
    this.#skipNewline = chunk.endsWith("\r");
    const lines = (this.#buffer + fresh).replace(/\r\n?/g, "\n").split("\n");
    this.#buffer = lines.pop() ?? "";
    const messages: SseMessage[] = [];
    for (const line of lines) {
      const message = this.#line(line);
      if (message !== null) messages.push(message);
    }
    return messages;
  }

  #line(line: string): SseMessage | null {
    if (line === "") return this.#dispatch();
    if (line.startsWith(":")) return null;
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    const raw = colon === -1 ? "" : line.slice(colon + 1);
    const value = raw.startsWith(" ") ? raw.slice(1) : raw;
    if (field === "event") this.#event = value;
    if (field === "data") this.#data.push(value);
    return null;
  }

  #dispatch(): SseMessage | null {
    const message = this.#data.length > 0 ? { event: this.#event || "message", data: this.#data.join("\n") } : null;
    this.#event = "";
    this.#data = [];
    return message;
  }
}

export function decodeFrame(message: SseMessage): Frame {
  let parsed: unknown;
  try {
    parsed = message.data.trim() === "" ? {} : JSON.parse(message.data);
  } catch {
    parsed = null;
  }
  return toFrame(message.event, parsed);
}

export function toFrame(type: string, data: unknown): Frame {
  if (!Object.hasOwn(FRAME_TYPES, type)) return protocolError(`Unknown frame type '${type}'.`);
  if (typeof data !== "object" || data === null || Array.isArray(data)) {
    return protocolError(`Frame '${type}' does not carry a JSON object.`);
  }
  return { ...(data as Record<string, JsonValue>), type } as Frame;
}

export async function* readFrames(stream: ReadableStream<Uint8Array>): AsyncGenerator<Frame> {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  const parser = new SseParser();
  let finished = false;
  try {
    while (!finished) {
      const chunk = await reader.read();
      finished = chunk.done;
      const text = chunk.done ? decoder.decode() : decoder.decode(chunk.value, { stream: true });
      for (const message of parser.push(text)) yield decodeFrame(message);
    }
  } finally {
    if (!finished) await reader.cancel();
  }
}

function protocolError(message: string): ErrorFrame {
  return { type: "error", kind: "protocol", message };
}
