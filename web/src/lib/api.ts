import { type Frame, readFrames, toFrame } from "./sse.ts";

export interface DatabaseSummary {
  id: string;
  title: string;
  description: string;
  table_count: number;
  size_bytes: number;
  examples: string[];
}

export interface ThreadSummary {
  id: string;
  title: string;
  database: string;
  updated_at: string;
}

export type TurnStatus = "completed" | "stopped" | "error" | "waiting";

export interface TurnVersion {
  index: number;
  count: number;
}

export interface StoredTurn {
  id: string;
  role: "user" | "assistant";
  text: string;
  frames: Frame[];
  status: TurnStatus;
  version: TurnVersion | null;
  answers: string | null;
}

export interface ThreadDetail {
  id: string;
  title: string;
  database: string;
  turns: StoredTurn[];
}

export interface ColumnReference {
  table: string;
  column: string;
}

export interface SchemaColumn {
  name: string;
  type: string;
  primary_key: boolean;
  references: ColumnReference | null;
}

export interface SchemaTable {
  name: string;
  row_count: number;
  columns: SchemaColumn[];
}

export interface Schema {
  database: string;
  tables: SchemaTable[];
}

export interface MemoryText {
  title: string;
  content: string;
}

export interface MemoryEntry extends MemoryText {
  key: string;
  created_at: string | null;
  updated_at: string | null;
}

export interface MemoryList {
  memories: MemoryEntry[];
  skipped: string[];
}

export interface ChatRequest {
  threadId: string | null;
  database: string;
  message: string;
}

export interface Problem {
  status: number;
  code: string | null;
  detail: string;
}

export type Explain = (problem: Problem) => string | null;

const MESSAGES = new Map<string, string>([
  ["run_active", "An answer is still running in this thread. Wait for it to finish or stop it, then try again."],
  ["clarification_pending", "This thread is waiting for the answer to its question. Answer the question first."],
  ["clarification_not_pending", "This question is closed and can no longer be answered. Ask it again as a new message."],
  ["database_mismatch", "This thread belongs to another database. Start a new chat to ask about this one."],
  ["thread_not_found", "This thread is no longer on the server. Start a new chat."],
  ["turn_not_found", "This question is no longer in the thread. Open the thread again."],
  ["version_not_found", "This version of the question is no longer on the server. Open the thread again."],
  ["turn_not_editable", "Only questions can be edited."],
  ["invalid_answer", "Pick one of the proposals. This question takes no free text."],
  ["database_required", "Pick a database to ask a question."],
  ["memory_not_found", "This memory is no longer on the server."],
]);

export class ApiError extends Error {
  readonly status: number;
  readonly code: string | null;

  constructor(status: number, message: string, code: string | null = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }

  static async from(response: Response, explain?: Explain): Promise<ApiError> {
    const { detail, code } = readProblem(await response.text());
    const problem = { status: response.status, code, detail: detail || response.statusText };
    const message = explain?.(problem) ?? problemMessage(code) ?? `HTTP ${problem.status}: ${problem.detail}`;
    return new ApiError(problem.status, message, code);
  }
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function problemMessage(code: string | null): string | null {
  return code === null ? null : (MESSAGES.get(code) ?? null);
}

export function databaseProblem(problem: Problem, database: string): string | null {
  if (problem.status !== 404) return null;
  return withDetail(`The database "${database}" is not available on the server. Pick another database.`, problem);
}

export function threadProblem(problem: Problem): string | null {
  if (problem.code !== "database_not_found") return null;
  return "The database of this thread is no longer on the server. Start a new chat.";
}

export function chatProblem(problem: Problem, request: ChatRequest): string | null {
  if (problem.status === 404 && request.threadId === null) return databaseProblem(problem, request.database);
  if (problem.code !== null) return threadProblem(problem);
  if (problem.status === 404) {
    return withDetail("This thread or its database was not found on the server. Start a new chat.", problem);
  }
  if (problem.status === 422) {
    return withDetail("The server did not accept the question. Check that a database is selected and send it again.", problem);
  }
  return null;
}

export function editProblem(problem: Problem): string | null {
  if (problem.code !== null) return threadProblem(problem);
  if (problem.status === 422) return withDetail("The server did not accept the edited question.", problem);
  return null;
}

export function memoryProblem(problem: Problem): string | null {
  if (problem.status !== 422 || problem.code !== null) return null;
  return withDetail("The server did not accept the memory.", problem);
}

function withDetail(sentence: string, problem: Problem): string {
  return problem.code !== null || problem.detail === "" ? sentence : `${sentence} (${problem.detail})`;
}

function readProblem(body: string): { detail: string; code: string | null } {
  let parsed: unknown;
  try {
    parsed = JSON.parse(body);
  } catch {
    return { detail: body.trim(), code: null };
  }
  if (typeof parsed !== "object" || parsed === null || !("detail" in parsed)) return { detail: body.trim(), code: null };
  const code = "code" in parsed && typeof parsed.code === "string" ? parsed.code : null;
  return { detail: detailText(parsed.detail), code };
}

interface Issue {
  loc?: unknown;
  msg: string;
}

function detailText(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail) && detail.length > 0 && detail.every(isIssue)) return detail.map(issueText).join("; ");
  return JSON.stringify(detail);
}

function isIssue(value: unknown): value is Issue {
  return typeof value === "object" && value !== null && "msg" in value && typeof value.msg === "string";
}

function issueText(issue: Issue): string {
  return Array.isArray(issue.loc) ? `${issue.loc.join(".")}: ${issue.msg}` : issue.msg;
}

export class ApiClient {
  databases(signal?: AbortSignal): Promise<DatabaseSummary[]> {
    return this.#get("/api/databases", signal);
  }

  threads(signal?: AbortSignal): Promise<ThreadSummary[]> {
    return this.#get("/api/threads", signal);
  }

  async thread(id: string, signal?: AbortSignal): Promise<ThreadDetail> {
    return replayedThread(await this.#get<ThreadDetail>(threadPath(id), signal));
  }

  async version(threadId: string, turnId: string, index: number, signal?: AbortSignal): Promise<ThreadDetail> {
    const path = `${turnPath(threadId, turnId)}/versions/${index}`;
    const response = await this.#post(path, null, "application/json", signal, threadProblem);
    return replayedThread((await response.json()) as ThreadDetail);
  }

  async stop(threadId: string, signal?: AbortSignal): Promise<void> {
    await this.#post(`${threadPath(threadId)}/stop`, null, "application/json", signal);
  }

  schema(database: string, signal?: AbortSignal): Promise<Schema> {
    return this.#get(`/api/databases/${encodeURIComponent(database)}/schema`, signal, (problem) =>
      databaseProblem(problem, database),
    );
  }

  memories(signal?: AbortSignal): Promise<MemoryList> {
    return this.#get("/api/memories", signal);
  }

  async addMemory(text: MemoryText, signal?: AbortSignal): Promise<MemoryEntry> {
    const response = await this.#send("POST", "/api/memories", text, "application/json", signal, memoryProblem);
    return (await response.json()) as MemoryEntry;
  }

  async editMemory(key: string, text: MemoryText, signal?: AbortSignal): Promise<MemoryEntry> {
    const response = await this.#send("PUT", memoryPath(key), text, "application/json", signal, memoryProblem);
    return (await response.json()) as MemoryEntry;
  }

  async deleteMemory(key: string, signal?: AbortSignal): Promise<void> {
    await this.#send("DELETE", memoryPath(key), null, "application/json", signal);
  }

  chat(request: ChatRequest, signal: AbortSignal): AsyncGenerator<Frame> {
    const body = { thread_id: request.threadId, database: request.database, message: request.message };
    return this.#stream("/api/chat", body, signal, (problem) => chatProblem(problem, request));
  }

  resume(threadId: string, key: string, answer: string, signal: AbortSignal): AsyncGenerator<Frame> {
    return this.#stream(`${threadPath(threadId)}/resume`, { key, answer }, signal, threadProblem);
  }

  edit(threadId: string, turnId: string, message: string, signal: AbortSignal): AsyncGenerator<Frame> {
    return this.#stream(`${turnPath(threadId, turnId)}/edit`, { message }, signal, editProblem);
  }

  async #get<T>(path: string, signal?: AbortSignal, explain?: Explain): Promise<T> {
    const response = await fetch(path, { headers: { Accept: "application/json" }, signal });
    if (!response.ok) throw await ApiError.from(response, explain);
    return (await response.json()) as T;
  }

  #post(path: string, body: object | null, accept: string, signal?: AbortSignal, explain?: Explain): Promise<Response> {
    return this.#send("POST", path, body, accept, signal, explain);
  }

  async #send(
    method: "POST" | "PUT" | "DELETE",
    path: string,
    body: object | null,
    accept: string,
    signal?: AbortSignal,
    explain?: Explain,
  ): Promise<Response> {
    const response = await fetch(path, {
      method,
      headers: { "Content-Type": "application/json", Accept: accept },
      body: body === null ? undefined : JSON.stringify(body),
      signal,
    });
    if (!response.ok) throw await ApiError.from(response, explain);
    return response;
  }

  async *#stream(path: string, body: object, signal: AbortSignal, explain?: Explain): AsyncGenerator<Frame> {
    const response = await this.#post(path, body, "text/event-stream", signal, explain);
    if (response.body === null) throw new ApiError(response.status, "The server sent an empty response.");
    yield* readFrames(response.body);
  }
}

function threadPath(threadId: string): string {
  return `/api/threads/${encodeURIComponent(threadId)}`;
}

function memoryPath(key: string): string {
  return `/api/memories/${encodeURIComponent(key)}`;
}

function turnPath(threadId: string, turnId: string): string {
  return `${threadPath(threadId)}/turns/${encodeURIComponent(turnId)}`;
}

function replayedThread(thread: ThreadDetail): ThreadDetail {
  return { ...thread, turns: thread.turns.map((turn) => ({ ...turn, frames: turn.frames.map(replayedFrame) })) };
}

function replayedFrame(frame: Frame): Frame {
  return toFrame(String(frame.type), frame);
}
