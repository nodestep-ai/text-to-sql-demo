import assert from "node:assert/strict";
import { test } from "node:test";
import { ApiClient, ApiError, chatProblem, databaseProblem, editProblem, problemMessage } from "../src/lib/api.ts";

function stubFetch(t, response) {
  const calls = [];
  t.mock.method(globalThis, "fetch", async (url, init) => {
    calls.push({ url, init });
    return typeof response === "function" ? response() : response;
  });
  return calls;
}

function sse(text) {
  return new Response(text, { status: 200, headers: { "Content-Type": "text/event-stream" } });
}

async function collect(frames) {
  const items = [];
  for await (const frame of frames) items.push(frame);
  return items;
}

const newChat = { threadId: null, database: "demo_shop", message: "Top product?" };

function refusal(detail, code, status = 409) {
  return Response.json({ detail, code }, { status });
}

test("chat posts the question with its database and yields the streamed frames", async (t) => {
  const calls = stubFetch(t, () => sse('event: token\ndata: {"text":"Hi"}\n\nevent: done\ndata: {}\n\n'));
  const frames = await collect(new ApiClient().chat(newChat, new AbortController().signal));
  assert.deepEqual(frames, [{ type: "token", text: "Hi" }, { type: "done" }]);
  assert.equal(calls[0].url, "/api/chat");
  assert.equal(calls[0].init.method, "POST");
  assert.equal(calls[0].init.headers["Content-Type"], "application/json");
  assert.deepEqual(JSON.parse(calls[0].init.body), { thread_id: null, database: "demo_shop", message: "Top product?" });
});

test("a follow-up sends the thread id and the thread's database", async (t) => {
  const calls = stubFetch(t, () => sse("event: done\ndata: {}\n\n"));
  await collect(new ApiClient().chat({ threadId: "t1", database: "demo_shop", message: "And 2024?" }, new AbortController().signal));
  assert.deepEqual(JSON.parse(calls[0].init.body), { thread_id: "t1", database: "demo_shop", message: "And 2024?" });
});

test("resume posts the key and the answer to the thread", async (t) => {
  const calls = stubFetch(t, () => sse("event: done\ndata: {}\n\n"));
  await collect(new ApiClient().resume("t 1", "revenue", "net", new AbortController().signal));
  assert.equal(calls[0].url, "/api/threads/t%201/resume");
  assert.deepEqual(JSON.parse(calls[0].init.body), { key: "revenue", answer: "net" });
});

test("a thread is loaded and its stored frames are decoded", async (t) => {
  const body = {
    id: "t1",
    title: "Products",
    database: "demo_shop",
    turns: [
      { role: "user", text: "Top product?", frames: [] },
      { role: "assistant", text: "Espresso beans.", frames: [{ type: "final", text: "Espresso beans." }, { type: "bogus" }] },
    ],
  };
  const calls = stubFetch(t, () => Response.json(body));
  const thread = await new ApiClient().thread("t1");
  assert.equal(calls[0].url, "/api/threads/t1");
  assert.equal(thread.database, "demo_shop");
  assert.deepEqual(thread.turns[1].frames, [
    { type: "final", text: "Espresso beans." },
    { type: "error", kind: "protocol", message: "Unknown frame type 'bogus'." },
  ]);
});

test("databases, threads and a database schema are plain GET requests", async (t) => {
  const calls = stubFetch(t, () => Response.json([]));
  assert.deepEqual(await new ApiClient().databases(), []);
  assert.deepEqual(await new ApiClient().threads(), []);
  await new ApiClient().schema("demo_shop");
  await new ApiClient().schema("a b");
  assert.deepEqual(
    calls.map((call) => call.url),
    ["/api/databases", "/api/threads", "/api/databases/demo_shop/schema", "/api/databases/a%20b/schema"],
  );
});

test("an unknown database for the schema names the database", async (t) => {
  stubFetch(t, () => refusal("Database 'gone' not found.", "database_not_found", 404));
  await assert.rejects(new ApiClient().schema("gone"), {
    name: "ApiError",
    message: 'The database "gone" is not available on the server. Pick another database.',
  });
});

test("an error response becomes an ApiError with its code and a plain message", async (t) => {
  stubFetch(t, () => refusal("Thread 3f2a not found.", "thread_not_found", 404));
  await assert.rejects(new ApiClient().thread("3f2a"), (error) => {
    assert.ok(error instanceof ApiError);
    assert.equal(error.status, 404);
    assert.equal(error.code, "thread_not_found");
    assert.equal(error.message, "This thread is no longer on the server. Start a new chat.");
    return true;
  });
});

test("an error without a code keeps the server detail", async (t) => {
  stubFetch(t, () => Response.json({ detail: "Not Found" }, { status: 404 }));
  await assert.rejects(new ApiClient().thread("x"), (error) => {
    assert.equal(error.code, null);
    assert.equal(error.message, "HTTP 404: Not Found");
    return true;
  });
});

test("every 409 says what happened without routes or thread ids", () => {
  const messages = ["run_active", "clarification_pending", "clarification_not_pending", "database_mismatch"].map(problemMessage);
  assert.deepEqual(messages, [
    "An answer is still running in this thread. Wait for it to finish or stop it, then try again.",
    "This thread is waiting for the answer to its question. Answer the question first.",
    "This question is closed and can no longer be answered. Ask it again as a new message.",
    "This thread belongs to another database. Start a new chat to ask about this one.",
  ]);
  assert.equal(new Set(messages).size, messages.length);
  for (const message of messages) assert.doesNotMatch(message, /\/api|POST|[0-9a-f]{32}/);
  assert.equal(problemMessage("constructor"), null);
  assert.equal(problemMessage(null), null);
});

test("a refused chat, resume, edit or version switch shows the message for its code", async (t) => {
  const detail = "Thread 0123456789abcdef0123456789abcdef is still answering. Stop it with POST /api/threads/0123456789abcdef0123456789abcdef/stop first.";
  stubFetch(t, () => refusal(detail, "run_active"));
  const running = "An answer is still running in this thread. Wait for it to finish or stop it, then try again.";
  const signal = new AbortController().signal;
  await assert.rejects(collect(new ApiClient().chat({ threadId: "t1", database: "demo_shop", message: "Hi" }, signal)), { message: running, code: "run_active" });
  await assert.rejects(collect(new ApiClient().resume("t1", "revenue", "net", signal)), { message: running });
  await assert.rejects(collect(new ApiClient().edit("t1", "u1", "Hi", signal)), { message: running });
  await assert.rejects(new ApiClient().version("t1", "u1", 1), { message: running });
  t.mock.restoreAll();
  stubFetch(t, () => refusal("Thread t1 has no open question with key 'revenue'.", "clarification_not_pending"));
  await assert.rejects(collect(new ApiClient().resume("t1", "revenue", "net", signal)), {
    message: "This question is closed and can no longer be answered. Ask it again as a new message.",
    code: "clarification_not_pending",
  });
});

test("a streaming request that fails raises before any frame", async (t) => {
  const missing = [{ type: "missing", loc: ["body", "database"], msg: "Field required", input: null }];
  stubFetch(t, () => Response.json({ detail: missing }, { status: 422 }));
  await assert.rejects(collect(new ApiClient().chat(newChat, new AbortController().signal)), (error) => {
    assert.ok(error instanceof ApiError);
    assert.equal(error.status, 422);
    assert.equal(
      error.message,
      "The server did not accept the question. Check that a database is selected and send it again. (body.database: Field required)",
    );
    return true;
  });
});

test("chat failures name the database and the next step", () => {
  const followUp = { threadId: "t1", database: "demo_shop", message: "Hi" };
  assert.equal(
    chatProblem({ status: 404, code: "database_not_found", detail: "Database 'gone' not found." }, { threadId: null, database: "gone", message: "Hi" }),
    'The database "gone" is not available on the server. Pick another database.',
  );
  assert.equal(
    chatProblem({ status: 404, code: "database_not_found", detail: "Database 'demo_shop' not found." }, followUp),
    "The database of this thread is no longer on the server. Start a new chat.",
  );
  assert.equal(
    chatProblem({ status: 404, code: null, detail: "Not Found" }, followUp),
    "This thread or its database was not found on the server. Start a new chat. (Not Found)",
  );
  assert.equal(
    chatProblem({ status: 404, code: null, detail: "Not Found" }, { threadId: null, database: "gone", message: "Hi" }),
    'The database "gone" is not available on the server. Pick another database. (Not Found)',
  );
  assert.equal(chatProblem({ status: 409, code: "run_active", detail: "x" }, followUp), null);
  assert.equal(
    chatProblem({ status: 422, code: null, detail: "" }, newChat),
    "The server did not accept the question. Check that a database is selected and send it again.",
  );
  assert.equal(chatProblem({ status: 500, code: null, detail: "boom" }, newChat), null);
  assert.equal(databaseProblem({ status: 409, code: "run_active", detail: "x" }, "demo_shop"), null);
});

test("validation details read as location and message, other details stay JSON", async (t) => {
  stubFetch(t, () => Response.json({ detail: [{ loc: ["body", "key"], msg: "Field required" }, { msg: "Bad" }] }, { status: 422 }));
  await assert.rejects(collect(new ApiClient().resume("t1", "", "", new AbortController().signal)), {
    message: "HTTP 422: body.key: Field required; Bad",
  });
  t.mock.restoreAll();
  stubFetch(t, () => Response.json({ detail: { code: 7 } }, { status: 400 }));
  await assert.rejects(new ApiClient().threads(), { message: 'HTTP 400: {"code":7}' });
});

test("a plain text or empty error body stays readable", async (t) => {
  stubFetch(t, () => new Response("upstream down\n", { status: 502 }));
  await assert.rejects(new ApiClient().databases(), { message: "HTTP 502: upstream down" });
  t.mock.restoreAll();
  stubFetch(t, () => new Response("", { status: 503, statusText: "Service Unavailable" }));
  await assert.rejects(new ApiClient().schema("demo_shop"), { message: "HTTP 503: Service Unavailable" });
});

test("stop posts to the thread and accepts 204", async (t) => {
  const calls = stubFetch(t, () => new Response(null, { status: 204 }));
  assert.equal(await new ApiClient().stop("t 1"), undefined);
  assert.equal(calls[0].url, "/api/threads/t%201/stop");
  assert.equal(calls[0].init.method, "POST");
  t.mock.restoreAll();
  stubFetch(t, () => Response.json({ detail: "Thread not found" }, { status: 404 }));
  await assert.rejects(new ApiClient().stop("gone"), { name: "ApiError", message: "HTTP 404: Thread not found" });
});

test("edit posts the new message for the turn and yields the streamed frames", async (t) => {
  const calls = stubFetch(t, () => sse('event: token\ndata: {"text":"Re"}\n\nevent: done\ndata: {}\n\n'));
  const frames = await collect(new ApiClient().edit("t1", "u 2", "Per store?", new AbortController().signal));
  assert.deepEqual(frames, [{ type: "token", text: "Re" }, { type: "done" }]);
  assert.equal(calls[0].url, "/api/threads/t1/turns/u%202/edit");
  assert.equal(calls[0].init.method, "POST");
  assert.deepEqual(JSON.parse(calls[0].init.body), { message: "Per store?" });
});

test("a version switch posts the index and returns the thread with its turn ids, status and versions", async (t) => {
  const body = {
    id: "t1",
    title: "Returns",
    database: "demo_shop",
    turns: [
      { id: "u1", role: "user", text: "Per store?", frames: [], status: "completed", version: { index: 1, count: 2 }, answers: null },
      { id: "a1", role: "assistant", text: "Brno.", frames: [{ type: "stopped" }], status: "stopped", version: null, answers: null },
    ],
  };
  const calls = stubFetch(t, () => Response.json(body));
  const thread = await new ApiClient().version("t1", "u1", 1);
  assert.equal(calls[0].url, "/api/threads/t1/turns/u1/versions/1");
  assert.equal(calls[0].init.method, "POST");
  assert.deepEqual(thread, body);
});

test("edit, resume and version failures say what to do", async (t) => {
  assert.equal(problemMessage("turn_not_editable"), "Only questions can be edited.");
  assert.equal(
    editProblem({ status: 422, code: null, detail: "body.message: String should have at least 1 character" }),
    "The server did not accept the edited question. (body.message: String should have at least 1 character)",
  );
  assert.equal(
    editProblem({ status: 404, code: "database_not_found", detail: "x" }),
    "The database of this thread is no longer on the server. Start a new chat.",
  );
  assert.equal(editProblem({ status: 500, code: null, detail: "boom" }), null);
  stubFetch(t, () => refusal("Turn u1 has no version 2; it has 2 (0 to 1).", "version_not_found", 404));
  await assert.rejects(new ApiClient().version("t1", "u1", 2), {
    message: "This version of the question is no longer on the server. Open the thread again.",
  });
});
