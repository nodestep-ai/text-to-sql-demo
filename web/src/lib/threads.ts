import type { ThreadSummary } from "./api.ts";
import type { Turn } from "./turns.ts";

const TITLE_LENGTH = 80;
const APP_NAME = "text-to-sql-demo";

export interface OpenThread {
  id: string;
  title: string | null;
  database: string;
}

export function threadTitle(turns: Turn[]): string | null {
  const question = turns.find((turn) => turn.role === "user");
  const title = question?.text.split(/\s+/).filter(Boolean).join(" ").slice(0, TITLE_LENGTH) ?? "";
  return title === "" ? null : title;
}

export function listedThreads(
  threads: ThreadSummary[] | null,
  current: OpenThread | null,
  now: Date = new Date(),
): ThreadSummary[] | null {
  if (threads === null || current === null || current.title === null) return threads;
  const title = current.title;
  if (threads.some((thread) => thread.id === current.id)) {
    return threads.map((thread) => (thread.id === current.id ? { ...thread, title } : thread));
  }
  return [{ id: current.id, title, database: current.database, updated_at: now.toISOString() }, ...threads];
}

export function documentTitle(threadTitle: string | null): string {
  return threadTitle ? `${threadTitle} · ${APP_NAME}` : APP_NAME;
}
