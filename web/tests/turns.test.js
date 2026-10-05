import assert from "node:assert/strict";
import { test } from "node:test";
import { ApiError } from "../src/lib/api.ts";
import {
  answerChoices,
  answerClarification,
  applyFrame,
  assistantTurn,
  chosenProposal,
  clarificationClosed,
  editTurns,
  pendingClarification,
  playFrames,
  replayTurns,
  retractAnswer,
  shownAnswer,
  shownSuggestions,
  syncTurns,
  toolLabel,
  toolState,
  userTurn,
  versionLabel,
  versionStep,
} from "../src/lib/turns.ts";

const sqlResult = {
  type: "sql_result",
  call_id: "c1",
  sql: "SELECT 1",
  columns: ["1"],
  rows: [[1]],
  row_count: 1,
  truncated: false,
  elapsed_ms: 2,
};

const chart = {
  type: "chart",
  call_id: "c2",
  kind: "bar",
  title: "Units per product",
  x: { field: "product", label: "Product" },
  y: { field: "units", label: "Units" },
  data: [{ product: "Espresso beans", units: 412 }],
};

const proposals = [
  { id: "gross", label: "Gross revenue", description: "Sum of quantity times price after discount." },
  { id: "net", label: "Net of refunds", description: "Gross revenue minus the refunds paid." },
];

const clarification = {
  type: "clarification",
  key: "revenue",
  question: "How should revenue be counted?",
  proposals,
  allow_free_text: false,
};

function user(id, text, extra = {}) {
  return { id, role: "user", text, frames: [], status: "completed", version: null, answers: null, ...extra };
}

function answer(id, text, frames = [], extra = {}) {
  return { id, role: "assistant", text, frames, status: "completed", version: null, answers: null, ...extra };
}

async function* framesOf(frames, failure) {
  for (const frame of frames) yield frame;
  if (failure) throw failure;
}

function applyAll(frames) {
  const turn = assistantTurn();
  for (const frame of frames) applyFrame(turn, frame);
  return turn;
}

test("tokens accumulate into one text block", () => {
  const turn = applyAll([
    { type: "token", text: "Hel" },
    { type: "token", text: "lo" },
  ]);
  assert.deepEqual(turn.blocks, [{ type: "text", text: "Hello" }]);
});

test("blocks keep arrival order and a tool row is updated in place", () => {
  const turn = applyAll([
    { type: "token", text: "Checking." },
    { type: "tool", call_id: "c1", name: "run_sql", status: "started", arguments: { sql: "SELECT 1" }, summary: null },
    sqlResult,
    { type: "tool", call_id: "c1", name: "run_sql", status: "finished", arguments: null, summary: "1 row" },
    { type: "token", text: "Done." },
  ]);
  assert.deepEqual(turn.blocks, [
    { type: "text", text: "Checking." },
    { type: "tool", call_id: "c1", name: "run_sql", status: "finished", arguments: { sql: "SELECT 1" }, summary: "1 row" },
    sqlResult,
    { type: "text", text: "Done." },
  ]);
});

test("a tool frame without a started frame still gets one row", () => {
  const turn = applyAll([
    { type: "tool", call_id: "c9", name: "list_tables", status: "error", arguments: null, summary: "failed" },
    { type: "tool", call_id: "c9", name: "list_tables", status: "error", arguments: null, summary: null },
  ]);
  assert.deepEqual(turn.blocks, [
    { type: "tool", call_id: "c9", name: "list_tables", status: "error", arguments: null, summary: "failed" },
  ]);
});

test("a resumed run that reuses a call id gets its own tool rows", () => {
  const turn = applyAll([
    { type: "run", thread_id: "t1", run_id: "r1", database: "demo_shop" },
    { type: "tool", call_id: "call_1", name: "list_tables", status: "started", arguments: {}, summary: null },
    { type: "tool", call_id: "call_1", name: "list_tables", status: "finished", arguments: null, summary: "8 rows" },
    clarification,
    { type: "done" },
  ]);
  answerClarification(turn, "gross");
  for (const frame of [
    { type: "run", thread_id: "t1", run_id: "r2", database: "demo_shop" },
    { type: "tool", call_id: "call_1", name: "run_sql", status: "started", arguments: { sql: "SELECT 1" }, summary: null },
    { ...sqlResult, call_id: "call_1" },
    { type: "tool", call_id: "call_1", name: "run_sql", status: "finished", arguments: null, summary: "8 rows" },
    { type: "tool", call_id: "call_2", name: "make_chart", status: "started", arguments: null, summary: null },
    { type: "tool", call_id: "call_2", name: "make_chart", status: "finished", arguments: null, summary: "bar chart" },
    { type: "final", text: "Coffee leads." },
    { type: "done" },
  ]) {
    applyFrame(turn, frame);
  }
  assert.deepEqual(
    turn.blocks.map((block) => (block.type === "tool" ? `${block.name} ${block.status}` : block.type)),
    ["list_tables finished", "clarification", "run_sql finished", "sql_result", "make_chart finished", "text"],
  );
  assert.deepEqual(turn.blocks[2].arguments, { sql: "SELECT 1" });
});

test("replay keeps the tool rows of a resumed answer apart from the question's rows", () => {
  const turns = replayTurns([
    user("u1", "Revenue per category?"),
    answer("a1", "", [
      { type: "tool", call_id: "call_1", name: "list_tables", status: "finished", arguments: null, summary: "8 rows" },
      clarification,
    ]),
    user("u2", "gross", { answers: "revenue" }),
    answer("a2", "Coffee leads.", [
      { type: "tool", call_id: "call_1", name: "run_sql", status: "started", arguments: null, summary: null },
      { type: "tool", call_id: "call_1", name: "run_sql", status: "finished", arguments: null, summary: "8 rows" },
    ]),
  ]);
  assert.deepEqual(
    turns[1].blocks.map((block) => (block.type === "tool" ? `${block.name} ${block.status}` : block.type)),
    ["list_tables finished", "clarification", "run_sql finished", "text"],
  );
});

test("final replaces the streamed text instead of repeating it", () => {
  const turn = applyAll([
    { type: "token", text: "The answer" },
    { type: "final", text: "The answer is 42." },
  ]);
  assert.deepEqual(turn.blocks, [{ type: "text", text: "The answer is 42." }]);
});

test("final without streamed text comes after the data, and an empty final is ignored", () => {
  const turn = applyAll([chart, { type: "final", text: "Espresso beans lead." }, { type: "final", text: "" }]);
  assert.deepEqual(turn.blocks, [chart, { type: "text", text: "Espresso beans lead." }]);
});

test("usage adds up per turn", () => {
  const turn = applyAll([
    { type: "usage", model: "gpt-a", input_tokens: 100, output_tokens: 10 },
    { type: "usage", model: "gpt-a", input_tokens: 50, output_tokens: 5 },
    { type: "usage", model: "gpt-b", input_tokens: 1, output_tokens: 1 },
  ]);
  assert.deepEqual(turn.usage, { models: ["gpt-a", "gpt-b"], input_tokens: 151, output_tokens: 16 });
});

test("errors are kept inline", () => {
  const turn = applyAll([{ type: "error", kind: "sql", message: "no such table: foo" }]);
  assert.deepEqual(turn.blocks, [{ type: "error", kind: "sql", message: "no such table: foo" }]);
});

test("a clarification is pending until it is answered or a non-text block follows it", () => {
  const turn = applyAll([clarification, { type: "usage", model: "m", input_tokens: 1, output_tokens: 1 }, { type: "done" }]);
  const pending = pendingClarification(turn);
  assert.equal(pending?.key, "revenue");
  assert.equal(pending?.answer, null);
  pending.answer = "net";
  assert.equal(pendingClarification(turn), null);
  assert.equal(pendingClarification(applyAll([clarification, { type: "error", kind: "model", message: "x" }])), null);
  assert.equal(pendingClarification(applyAll([clarification, { type: "final", text: "I need the basis." }]))?.key, "revenue");
  assert.equal(pendingClarification(applyAll([{ type: "token", text: "Hi" }])), null);
});

test("a stopped turn has no pending clarification", () => {
  const stopped = applyAll([clarification, { type: "stopped" }]);
  assert.equal(pendingClarification(stopped), null);
  const halted = applyAll([clarification]);
  halted.stopped = true;
  assert.equal(pendingClarification(halted), null);
  assert.equal(answerClarification(halted, "net"), null);
});

test("an unanswered clarification reads as closed once its turn has ended and it is not the pending one", () => {
  const open = applyAll([clarification]);
  const [block] = open.blocks;
  assert.equal(clarificationClosed(open, block, block), false);
  const streaming = applyAll([clarification]);
  streaming.streaming = true;
  assert.equal(clarificationClosed(streaming, streaming.blocks[0], null), false);
  const stopped = applyAll([clarification, { type: "stopped" }]);
  assert.equal(clarificationClosed(stopped, stopped.blocks[0], null), true);
  const answered = applyAll([clarification]);
  answerClarification(answered, "gross");
  assert.equal(clarificationClosed(answered, answered.blocks[0], null), false);
});

test("a clarification offers its proposals, and free text when allowed or when there are no proposals", () => {
  assert.deepEqual(answerChoices(clarification), { proposals, freeText: false });
  assert.deepEqual(answerChoices({ ...clarification, allow_free_text: true }), { proposals, freeText: true });
  assert.deepEqual(answerChoices({ ...clarification, proposals: [] }), { proposals: [], freeText: true });
});

test("an answered clarification finds the chosen proposal by id", () => {
  assert.equal(chosenProposal({ ...clarification, answer: "net" }), proposals[1]);
  assert.equal(chosenProposal({ ...clarification, answer: "Only online orders" }), null);
  assert.equal(chosenProposal({ ...clarification, answer: null }), null);
});

test("an answered clarification shows the chosen proposal with its description, or the typed text", () => {
  assert.deepEqual(shownAnswer({ ...clarification, answer: "net" }), {
    text: proposals[1].label,
    detail: proposals[1].description,
  });
  assert.deepEqual(shownAnswer({ ...clarification, answer: "Only online orders" }), {
    text: "Only online orders",
    detail: null,
  });
  assert.equal(shownAnswer({ ...clarification, answer: null }), null);
});

test("a stopped frame marks the turn, and the last suggestions frame wins", () => {
  const stopped = applyAll([{ type: "token", text: "Store 1 has" }, { type: "stopped" }, { type: "done" }]);
  assert.equal(stopped.stopped, true);
  assert.deepEqual(stopped.blocks, [{ type: "text", text: "Store 1 has" }]);
  const answered = applyAll([
    { type: "final", text: "Espresso beans." },
    { type: "suggestions", questions: ["Per year?"] },
    { type: "suggestions", questions: ["Per store?", "Per category?"] },
  ]);
  assert.equal(answered.stopped, false);
  assert.deepEqual(answered.suggestions, ["Per store?", "Per category?"]);
});

test("replay shows each assistant answer once", () => {
  const tool = { type: "tool", call_id: "c1", name: "run_sql", status: "finished", arguments: null, summary: "1 row" };
  const turns = replayTurns([
    user("u1", "Top product?"),
    answer("a1", "Espresso beans.", [tool, sqlResult]),
    answer("a2", "Filter coffee.", [{ type: "token", text: "Filter coffee." }, { type: "final", text: "Filter coffee." }]),
    answer("a3", "", [clarification], { status: "waiting" }),
  ]);
  assert.deepEqual(turns[0], { role: "user", id: "u1", text: "Top product?", version: null });
  assert.deepEqual(turns[1], {
    role: "assistant",
    blocks: [tool, sqlResult, { type: "text", text: "Espresso beans." }],
    usage: null,
    streaming: false,
    stopped: false,
    suggestions: [],
    runStart: 0,
  });
  assert.deepEqual(turns[2].blocks, [{ type: "text", text: "Filter coffee." }]);
  assert.equal(pendingClarification(turns[3])?.key, "revenue");
});

test("an answer is stored on the clarification, which moves after any text so new text starts a new block", () => {
  const turn = applyAll([clarification, { type: "final", text: "I need the basis." }]);
  assert.equal(answerClarification(turn, "net")?.answer, "net");
  assert.equal(answerClarification(turn, "gross"), null);
  applyFrame(turn, { type: "token", text: "Coffee" });
  applyFrame(turn, { type: "final", text: "Coffee leads." });
  assert.deepEqual(turn.blocks, [
    { type: "text", text: "I need the basis." },
    { ...clarification, answer: "net" },
    { type: "text", text: "Coffee leads." },
  ]);
});

test("replay puts a stored answer into its clarification and continues the same turn", () => {
  const usage = { type: "usage", model: "m", input_tokens: 10, output_tokens: 2 };
  const turns = replayTurns([
    user("u1", "Revenue per category?"),
    answer("a1", "", [clarification, usage]),
    user("u2", "net", { answers: "revenue" }),
    answer("a2", "Coffee leads.", [chart, usage, { type: "suggestions", questions: ["Per store?"] }]),
    user("u3", "Thanks"),
    answer("a3", "Which basis?", [clarification]),
    user("u4", "gross", { answers: "revenue" }),
    answer("a4", "Coffee sold 380 units.", [], { status: "stopped" }),
  ]);
  assert.equal(turns.length, 4);
  assert.deepEqual(turns[1].blocks, [{ ...clarification, answer: "net" }, chart, { type: "text", text: "Coffee leads." }]);
  assert.deepEqual(turns[1].usage, { models: ["m"], input_tokens: 20, output_tokens: 4 });
  assert.deepEqual(turns[1].suggestions, ["Per store?"]);
  assert.equal(turns[1].stopped, false);
  assert.deepEqual(turns[2], { role: "user", id: "u3", text: "Thanks", version: null });
  assert.equal(turns[3].stopped, true);
  assert.deepEqual(turns[3].blocks, [
    { type: "text", text: "Which basis?" },
    { ...clarification, answer: "gross" },
    { type: "text", text: "Coffee sold 380 units." },
  ]);
});

test("replay keeps a stopped clarification closed and a later message as its own question", () => {
  const turns = replayTurns([
    user("u1", "Revenue per category?"),
    answer("a1", "", [clarification], { status: "stopped" }),
    user("u2", "How many stores are there?"),
    answer("a2", "There are four stores."),
  ]);
  assert.equal(turns.length, 4);
  assert.equal(turns[1].stopped, true);
  assert.equal(pendingClarification(turns[1]), null);
  assert.deepEqual(turns[1].blocks, [{ ...clarification, answer: null }]);
  assert.deepEqual(turns[2], { role: "user", id: "u2", text: "How many stores are there?", version: null });
  assert.deepEqual(turns[3].blocks, [{ type: "text", text: "There are four stores." }]);
});

test("replay never reads a plain message as the answer to a clarification", () => {
  const turns = replayTurns([
    user("u1", "Revenue per category?"),
    answer("a1", "", [clarification]),
    user("u2", "How many stores are there?"),
    answer("a2", "There are four stores."),
  ]);
  assert.equal(turns.length, 4);
  assert.equal(turns[1].blocks[0].answer, null);
  assert.equal(turns[2].id, "u2");
});

test("replay keeps a clarification that still waits on the server open", () => {
  const turns = replayTurns([user("u1", "Revenue per category?"), answer("a1", "", [clarification], { status: "waiting" })]);
  assert.equal(pendingClarification(turns[1])?.key, "revenue");
  assert.equal(clarificationClosed(turns[1], turns[1].blocks[0], pendingClarification(turns[1])), false);
});

test("playFrames applies frames, reports the run with its thread and database, and ends streaming", async () => {
  const turn = assistantTurn();
  const runs = [];
  const run = { type: "run", thread_id: "t1", run_id: "r1", database: "demo_shop" };
  const frames = framesOf([run, { type: "token", text: "Hi" }, { type: "done" }]);
  await playFrames(turn, frames, new AbortController().signal, (frame) => runs.push(frame));
  assert.deepEqual(runs, [run]);
  assert.deepEqual(turn.blocks, [{ type: "text", text: "Hi" }]);
  assert.equal(turn.streaming, false);
});

test("playFrames reports a stream that ends without done", async () => {
  const turn = assistantTurn();
  await playFrames(turn, framesOf([{ type: "token", text: "Hi" }]), new AbortController().signal, () => {});
  assert.deepEqual(turn.blocks.at(-1), {
    type: "error",
    kind: "stream",
    message: "The stream ended before the run finished.",
  });
});

test("playFrames shows failed requests inline", async () => {
  const http = assistantTurn();
  await playFrames(http, framesOf([], new ApiError(409, "HTTP 409: Thread is waiting for an answer.")), new AbortController().signal, () => {});
  assert.deepEqual(http.blocks, [{ type: "error", kind: "http", message: "HTTP 409: Thread is waiting for an answer." }]);
  const network = assistantTurn();
  await playFrames(network, framesOf([], new TypeError("Failed to fetch")), new AbortController().signal, () => {});
  assert.deepEqual(network.blocks, [{ type: "error", kind: "network", message: "Failed to fetch" }]);
  assert.equal(network.streaming, false);
});

test("playFrames names a refusal by its API code", async () => {
  const turn = assistantTurn();
  const closed = new ApiError(409, "This question is closed.", "clarification_not_pending");
  await playFrames(turn, framesOf([], closed), new AbortController().signal, () => {});
  assert.deepEqual(turn.blocks, [{ type: "error", kind: "clarification_not_pending", message: "This question is closed." }]);
});

test("playFrames stops quietly after an abort", async () => {
  const turn = assistantTurn();
  const controller = new AbortController();
  async function* frames() {
    yield { type: "token", text: "a" };
    controller.abort();
    yield { type: "run", thread_id: "other", run_id: "r2", database: "demo_shop" };
    throw new DOMException("The operation was aborted.", "AbortError");
  }
  const threads = [];
  await playFrames(turn, frames(), controller.signal, (id) => threads.push(id));
  assert.deepEqual(turn.blocks, [{ type: "text", text: "a" }]);
  assert.deepEqual(threads, []);
  assert.equal(turn.streaming, false);
});

test("playFrames tells whether the run started", async () => {
  const signal = new AbortController().signal;
  const run = { type: "run", thread_id: "t1", run_id: "r1", database: "demo_shop" };
  assert.equal(await playFrames(assistantTurn(), framesOf([run, { type: "done" }]), signal, () => {}), true);
  assert.equal(await playFrames(assistantTurn(), framesOf([], new ApiError(409, "HTTP 409: No open question.")), signal, () => {}), false);
  assert.equal(await playFrames(assistantTurn(), framesOf([], new TypeError("Failed to fetch")), signal, () => {}), false);
  assert.equal(await playFrames(assistantTurn(), framesOf([run], new TypeError("network error")), signal, () => {}), true);
});

test("a resume that fails before the run starts takes the answer back and asks again below the error", async () => {
  const turn = applyAll([clarification, { type: "final", text: "I need the basis." }]);
  const block = answerClarification(turn, "net");
  const failure = new ApiError(409, "HTTP 409: No open question.");
  const started = await playFrames(turn, framesOf([], failure), new AbortController().signal, () => {});
  assert.equal(started, false);
  retractAnswer(turn, block);
  assert.equal(pendingClarification(turn), block);
  assert.deepEqual(turn.blocks, [
    { type: "text", text: "I need the basis." },
    { type: "error", kind: "http", message: "HTTP 409: No open question." },
    { ...clarification, answer: null },
  ]);
  assert.equal(answerClarification(turn, "gross")?.answer, "gross");
});

test("replay keeps turn ids and versions on questions and the stopped status on answers", () => {
  const turns = replayTurns([
    user("u1", "Per store?", { version: { index: 2, count: 3 } }),
    answer("a1", "Store 1 has", [], { status: "stopped" }),
    user("u2", "And per city?"),
    answer("a2", "Brno.", [], { status: "error" }),
  ]);
  assert.deepEqual(turns[0], { role: "user", id: "u1", text: "Per store?", version: { index: 2, count: 3 } });
  assert.equal(turns[1].stopped, true);
  assert.deepEqual(turns[2], { role: "user", id: "u2", text: "And per city?", version: null });
  assert.equal(turns[3].stopped, false);
});

test("editing a question keeps the turns before it, drops the ones after it and adds a new version", () => {
  const turns = replayTurns([
    user("u1", "Top product?"),
    answer("a1", "Espresso beans."),
    user("u2", "Per year?", { version: { index: 1, count: 2 } }),
    answer("a2", "2025."),
    user("u3", "Thanks"),
    answer("a3", "You are welcome."),
  ]);
  const edited = editTurns(turns, 2, "Per month?");
  assert.equal(edited.length, 4);
  assert.equal(edited[0], turns[0]);
  assert.equal(edited[1], turns[1]);
  assert.deepEqual(edited[2], { role: "user", id: "u2", text: "Per month?", version: { index: 2, count: 3 } });
  assert.deepEqual(edited[3], assistantTurn());
  assert.equal(turns[2].text, "Per year?");
  assert.deepEqual(editTurns(turns, 0, "Top store?")[0].version, { index: 1, count: 2 });
});

test("sync copies ids, versions and the stopped status onto the streamed turns when the structure matches", () => {
  const streamed = assistantTurn();
  applyFrame(streamed, { type: "token", text: "Store 1" });
  applyFrame(streamed, sqlResult);
  const live = [{ role: "user", id: null, text: "Per store?", version: null }, streamed];
  const stored = replayTurns([
    user("u9", "Per store?", { version: { index: 1, count: 2 } }),
    answer("a9", "Store 1", [sqlResult], { status: "stopped" }),
  ]);
  const synced = syncTurns(live, stored);
  assert.deepEqual(synced[0], { role: "user", id: "u9", text: "Per store?", version: { index: 1, count: 2 } });
  assert.deepEqual(synced[1].blocks, [{ type: "text", text: "Store 1" }, sqlResult]);
  assert.equal(synced[1].stopped, true);
});

test("sync takes the stored turns when the screen does not match them", () => {
  const stored = replayTurns([user("u1", "A?"), answer("a1", "B."), user("u2", "C?"), answer("a2", "D.")]);
  const refused = [userTurn("A?"), assistantTurn(), userTurn("X?"), assistantTurn(), userTurn("C?"), assistantTurn()];
  assert.equal(syncTurns(refused, stored), stored);
  const missing = [userTurn("C?"), assistantTurn()];
  assert.equal(syncTurns(missing, stored), stored);
  const swapped = [assistantTurn(), userTurn("A?"), assistantTurn(), userTurn("C?")];
  assert.equal(syncTurns(swapped, stored), stored);
  const matching = [userTurn("A?"), assistantTurn(), userTurn("C?"), assistantTurn()];
  assert.deepEqual(
    syncTurns(matching, stored).map((turn) => (turn.role === "user" ? turn.id : "answer")),
    ["u1", "answer", "u2", "answer"],
  );
});

test("sync keeps a stop made on screen even when the server stored the turn as completed", () => {
  const streamed = assistantTurn();
  streamed.stopped = true;
  const stored = replayTurns([user("u1", "A?"), answer("a1", "B.")]);
  assert.equal(syncTurns([userTurn("A?"), streamed], stored)[1].stopped, true);
});

test("the version switcher reads n/m counting from 1 and steps inside the range the server counts from 0", () => {
  assert.equal(versionLabel({ index: 0, count: 2 }), "1/2");
  assert.equal(versionLabel({ index: 1, count: 3 }), "2/3");
  assert.equal(versionStep({ index: 1, count: 3 }, -1), 0);
  assert.equal(versionStep({ index: 1, count: 3 }, 1), 2);
  assert.equal(versionStep({ index: 0, count: 3 }, -1), null);
  assert.equal(versionStep({ index: 2, count: 3 }, 1), null);
});

test("an edited question replayed from the server can step back to the original", () => {
  const [edited] = replayTurns([user("u1", "Top store?", { version: { index: 1, count: 2 } }), answer("a1", "Brno.")]);
  assert.equal(versionLabel(edited.version), "2/2");
  assert.equal(versionStep(edited.version, -1), 0);
  assert.equal(versionStep(edited.version, 1), null);
  const [original] = replayTurns([user("u1", "Top product?", { version: { index: 0, count: 2 } }), answer("a1", "Beans.")]);
  assert.equal(versionLabel(original.version), "1/2");
  assert.equal(versionStep(original.version, -1), null);
  assert.equal(versionStep(original.version, 1), 1);
});

test("a tool that never finished reads as stopped or not finished once its turn has ended", () => {
  const started = { type: "tool", call_id: "c1", name: "run_sql", status: "started", arguments: null, summary: null };
  const turn = assistantTurn();
  turn.streaming = true;
  assert.equal(toolState(started, turn), "running");
  turn.streaming = false;
  assert.equal(toolState(started, turn), "not finished");
  turn.stopped = true;
  assert.equal(toolState(started, turn), "stopped");
  assert.equal(toolState({ ...started, status: "finished" }, turn), "done");
  assert.equal(toolState({ ...started, status: "error" }, turn), "failed");
});

test("memory, skill and sub-agent rows read as sentences, and other tools keep their name", () => {
  const row = (name, status, args = null) => ({ type: "tool", call_id: "c1", name, status, arguments: args, summary: null });
  const topics = { topics: [{ name: "revenue" }, { name: "returns" }, { name: "top products" }] };
  const turn = assistantTurn();
  turn.streaming = true;
  assert.equal(toolLabel(row("save_memory", "started"), turn), "Saving to memory");
  assert.equal(toolLabel(row("search_memory", "started"), turn), "Searching memory");
  assert.equal(toolLabel(row("load_skill", "started", { name: "report" }), turn), "Loading skill report");
  assert.equal(toolLabel(row("analyze_topics", "started", topics), turn), "Running 3 sub-agents");
  turn.streaming = false;
  for (const [name, label] of [
    ["save_memory", "Saved to memory"],
    ["search_memory", "Searched memory"],
    ["list_memories", "Listed memories"],
    ["delete_memory", "Deleted from memory"],
  ]) {
    assert.equal(toolLabel(row(name, "finished"), turn), label);
  }
  assert.equal(toolLabel(row("load_skill", "finished", { name: "report" }), turn), "Loaded skill report");
  assert.equal(toolLabel(row("analyze_topics", "finished", topics), turn), "Ran 3 sub-agents");
  assert.equal(toolLabel(row("delete_memory", "error"), turn), "Could not delete from memory");
  assert.equal(toolLabel(row("load_skill", "error", { name: "nope" }), turn), "Could not load skill nope");
  assert.equal(toolLabel(row("analyze_topics", "error", { topics: "bad" }), turn), "Could not run the sub-agents");
  assert.equal(toolLabel(row("save_memory", "started"), turn), "Saving to memory (not finished)");
  turn.stopped = true;
  assert.equal(toolLabel(row("analyze_topics", "started", topics), turn), "Running 3 sub-agents (stopped)");
  assert.equal(toolLabel(row("load_skill", "started", {}), turn), "Loading a skill (stopped)");
  for (const name of ["list_tables", "describe_table", "run_sql", "make_chart", "unknown"]) {
    assert.equal(toolLabel(row(name, "finished"), turn), null);
  }
});

test("live and replayed threads show the same follow-up questions: only below the last finished answer", () => {
  const first = [sqlResult, { type: "final", text: "Espresso beans." }, { type: "suggestions", questions: ["Per store?"] }];
  const second = [chart, { type: "final", text: "Coffee leads." }, { type: "suggestions", questions: ["Per year?", "Per month?"] }];
  const live = [userTurn("Top product?"), applyAll(first), userTurn("Revenue per category?"), applyAll(second)];
  const replayed = replayTurns([
    user("u1", "Top product?"),
    answer("a1", "Espresso beans.", first),
    user("u2", "Revenue per category?"),
    answer("a2", "Coffee leads.", second),
  ]);
  for (const turns of [live, replayed]) {
    assert.deepEqual(
      turns.map((_, index) => shownSuggestions(turns, index)),
      [[], [], [], ["Per year?", "Per month?"]],
    );
  }
  const streaming = [...live.slice(0, 3), { ...applyAll(second), streaming: true }];
  assert.deepEqual(shownSuggestions(streaming, 3), []);
  assert.deepEqual(shownSuggestions(streaming, 1), []);
});

test("text the model wrote before a tool call is replayed before the tool, as it streamed", () => {
  const started = { type: "tool", call_id: "c1", name: "list_tables", status: "started", arguments: {}, summary: null };
  const finished = { ...started, status: "finished", summary: "3 tables" };
  const live = applyAll([
    { type: "token", text: "Let me " },
    { type: "token", text: "check." },
    started,
    finished,
    { type: "token", text: "Three tables." },
    { type: "final", text: "Three tables." },
  ]);
  const [, replayed] = replayTurns([
    user("u1", "Tables?"),
    answer("a1", "Three tables.", [{ type: "token", text: "Let me check." }, started, finished]),
  ]);
  const shown = (turn) => turn.blocks.map((block) => (block.type === "text" ? block.text : `${block.name} ${block.status}`));
  assert.deepEqual(shown(replayed), ["Let me check.", "list_tables finished", "Three tables."]);
  assert.deepEqual(shown(replayed), shown(live));
});
