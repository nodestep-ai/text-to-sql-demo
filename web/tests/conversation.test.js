import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { registerHooks } from "node:module";
import { test } from "node:test";
import { compileModule } from "svelte/compiler";
import ts from "typescript";

registerHooks({
  load(url, context, nextLoad) {
    if (!url.endsWith(".svelte.ts")) return nextLoad(url, context);
    const options = { target: ts.ScriptTarget.ESNext, module: ts.ModuleKind.ESNext, verbatimModuleSyntax: true };
    const script = ts.transpileModule(readFileSync(new URL(url), "utf8"), { compilerOptions: options }).outputText;
    return { format: "module", source: compileModule(script, { filename: url }).js.code, shortCircuit: true };
  },
});

const { Conversation } = await import("../src/lib/conversation.svelte.ts");

const REPORT = "Write a report on sales by store in 2025.";
const RETURNS = "Which store has the most returns?";

function stored(id, role, text, extra = {}) {
  return { id, role, text, frames: [], status: "completed", version: null, answers: null, ...extra };
}

const THREADS = new Map([
  [
    "t1",
    {
      id: "t1",
      title: REPORT,
      database: "demo_shop",
      turns: [stored("u1", "user", REPORT), stored("a1", "assistant", "Here is", { status: "stopped" })],
    },
  ],
  [
    "t2",
    {
      id: "t2",
      title: RETURNS,
      database: "demo_shop",
      turns: [stored("u1", "user", RETURNS), stored("a1", "assistant", "Riverside has the most returns.")],
    },
  ],
]);

const OTHER = { id: "t2", title: RETURNS, database: "demo_shop", updated_at: "2026-10-01T08:00:00Z" };
const STREAMING = { id: "t1", title: REPORT, database: "demo_shop", updated_at: "2026-10-01T09:00:00Z" };

function pause() {
  return new Promise((resolve) => setTimeout(resolve, 1));
}

async function* slowAnswer(signal) {
  yield { type: "run", thread_id: "t1", run_id: "r1", database: "demo_shop" };
  yield { type: "token", text: "Here is" };
  if (!signal.aborted) await new Promise((resolve) => signal.addEventListener("abort", resolve, { once: true }));
  throw new DOMException("The operation was aborted.", "AbortError");
}

class FakeApi {
  log = [];

  chat(request, signal) {
    this.log.push(`chat ${request.message}`);
    return slowAnswer(signal);
  }

  async stop(threadId) {
    this.log.push(`stop ${threadId}`);
    await pause();
  }

  async thread(id) {
    this.log.push(`thread ${id}`);
    await pause();
    return THREADS.get(id);
  }
}

async function streamingConversation() {
  const api = new FakeApi();
  const conversation = new Conversation(api, () => api.log.push("finish"));
  conversation.select("demo_shop");
  const sent = conversation.send(REPORT);
  await pause();
  assert.equal(conversation.streaming, true);
  assert.equal(conversation.threadId, "t1");
  return { api, conversation, sent };
}

test("opening another thread while an answer streams stops the answer, waits for it, then opens the thread", async () => {
  const { api, conversation, sent } = await streamingConversation();
  const opened = conversation.open(OTHER);
  assert.equal(conversation.stopping, true);
  assert.equal(conversation.threadId, "t1");
  await opened;
  assert.deepEqual(api.log, [`chat ${REPORT}`, "stop t1", "thread t1", "finish", "thread t2"]);
  assert.equal(await sent, true);
  assert.equal(conversation.threadId, "t2");
  assert.deepEqual(
    conversation.turns.map((turn) => (turn.role === "user" ? turn.text : turn.blocks[0].text)),
    [RETURNS, "Riverside has the most returns."],
  );
  assert.equal(conversation.busy, false);
  assert.equal(conversation.stopping, false);
  assert.equal(conversation.error, null);
});

test("New chat while an answer streams stops the answer, waits for it, then starts an empty chat", async () => {
  const { api, conversation, sent } = await streamingConversation();
  const started = conversation.reset();
  assert.equal(conversation.stopping, true);
  assert.equal(conversation.turns.length, 2);
  await started;
  assert.deepEqual(api.log, [`chat ${REPORT}`, "stop t1", "thread t1", "finish"]);
  assert.equal(await sent, true);
  assert.equal(conversation.threadId, null);
  assert.deepEqual(conversation.turns, []);
  assert.equal(conversation.database, "demo_shop");
  assert.equal(conversation.busy, false);
  assert.equal(conversation.stopping, false);
});

test("the last choice wins when several are made while the answer stops", async () => {
  const { api, conversation } = await streamingConversation();
  await Promise.all([conversation.reset(), conversation.open(OTHER)]);
  assert.equal(api.log.filter((entry) => entry === "stop t1").length, 1);
  assert.equal(conversation.threadId, "t2");
  assert.equal(conversation.turns.length, 2);
});

test("opening the thread whose answer streams leaves the answer running", async () => {
  const { api, conversation, sent } = await streamingConversation();
  await conversation.open(STREAMING);
  assert.deepEqual(api.log, [`chat ${REPORT}`]);
  assert.equal(conversation.streaming, true);
  conversation.stop();
  assert.equal(await sent, true);
});

test("New chat without a running answer sends no stop request", async () => {
  const api = new FakeApi();
  const conversation = new Conversation(api, () => api.log.push("finish"));
  conversation.select("demo_shop");
  await conversation.open(OTHER);
  await conversation.reset();
  assert.deepEqual(api.log, ["thread t2"]);
  assert.equal(conversation.threadId, null);
  assert.deepEqual(conversation.turns, []);
});
