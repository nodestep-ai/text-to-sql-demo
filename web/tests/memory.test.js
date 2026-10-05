import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { registerHooks } from "node:module";
import { test } from "node:test";
import { compileModule } from "svelte/compiler";
import ts from "typescript";
import { ApiClient, ApiError, memoryProblem } from "../src/lib/api.ts";

registerHooks({
  load(url, context, nextLoad) {
    if (!url.endsWith(".svelte.ts")) return nextLoad(url, context);
    const options = { target: ts.ScriptTarget.ESNext, module: ts.ModuleKind.ESNext, verbatimModuleSyntax: true };
    const script = ts.transpileModule(readFileSync(new URL(url), "utf8"), { compilerOptions: options }).outputText;
    return { format: "module", source: compileModule(script, { filename: url }).js.code, shortCircuit: true };
  },
});

const { Memories } = await import("../src/lib/memories.svelte.ts");

const REVENUE = { title: "Revenue", content: "Revenue counts completed orders only." };

function entry(key, text, updated = "2026-10-04T10:00:00Z") {
  return { key, ...text, created_at: updated, updated_at: updated };
}

function stubFetch(t, response) {
  const calls = [];
  t.mock.method(globalThis, "fetch", async (url, init) => {
    calls.push({ url, init });
    return typeof response === "function" ? response() : response;
  });
  return calls;
}

class FakeApi {
  stored = [];
  log = [];
  failure = null;

  async memories() {
    this.log.push("list");
    if (this.failure !== null) throw this.failure;
    return { memories: [...this.stored], skipped: ["broken.json"] };
  }

  async addMemory(text) {
    this.log.push(`add ${text.title}`);
    if (this.failure !== null) throw this.failure;
    const added = entry(`k${this.stored.length + 1}`, text);
    this.stored.unshift(added);
    return added;
  }

  async editMemory(key, text) {
    this.log.push(`edit ${key}`);
    if (this.failure !== null) throw this.failure;
    this.stored = this.stored.map((item) => (item.key === key ? { ...item, ...text } : item));
    return this.stored.find((item) => item.key === key);
  }

  async deleteMemory(key) {
    this.log.push(`delete ${key}`);
    if (this.failure !== null) throw this.failure;
    this.stored = this.stored.filter((item) => item.key !== key);
  }
}

test("the memory routes: list, add, edit by key and delete", async (t) => {
  const calls = stubFetch(t, () => Response.json({ memories: [], skipped: [] }));
  const client = new ApiClient();
  assert.deepEqual(await client.memories(), { memories: [], skipped: [] });
  await client.addMemory(REVENUE);
  await client.editMemory("a b/c", REVENUE);
  assert.deepEqual(
    calls.map((call) => [call.init?.method ?? "GET", call.url]),
    [
      ["GET", "/api/memories"],
      ["POST", "/api/memories"],
      ["PUT", "/api/memories/a%20b%2Fc"],
    ],
  );
  assert.deepEqual(JSON.parse(calls[1].init.body), REVENUE);
  assert.deepEqual(JSON.parse(calls[2].init.body), REVENUE);
});

test("delete sends DELETE and accepts 204", async (t) => {
  const calls = stubFetch(t, () => new Response(null, { status: 204 }));
  await new ApiClient().deleteMemory("revenue");
  assert.equal(calls[0].url, "/api/memories/revenue");
  assert.equal(calls[0].init.method, "DELETE");
});

test("a memory that is gone or a refused memory says so in plain words", async (t) => {
  stubFetch(t, () => Response.json({ detail: "Memory 'x' not found.", code: "memory_not_found" }, { status: 404 }));
  await assert.rejects(new ApiClient().deleteMemory("x"), (error) => {
    assert.ok(error instanceof ApiError);
    assert.equal(error.code, "memory_not_found");
    assert.equal(error.message, "This memory is no longer on the server.");
    return true;
  });
  const refused = { status: 422, code: null, detail: "body.title: String should have at most 120 characters" };
  assert.equal(
    memoryProblem(refused),
    "The server did not accept the memory. (body.title: String should have at most 120 characters)",
  );
  assert.equal(memoryProblem({ status: 500, code: null, detail: "boom" }), null);
});

test("load lists the memories and the files left out", async () => {
  const api = new FakeApi();
  api.stored = [entry("k1", REVENUE)];
  const memories = new Memories(api);
  assert.equal(memories.entries, null);
  await memories.load();
  assert.deepEqual(memories.entries, api.stored);
  assert.deepEqual(memories.skipped, ["broken.json"]);
  assert.equal(memories.error, null);
});

test("add, edit and remove change the memory on the server, then load the list again", async () => {
  const api = new FakeApi();
  const memories = new Memories(api);
  assert.equal(await memories.add(REVENUE), true);
  assert.equal(await memories.edit("k1", { title: "Revenue rule", content: "Refunds count too." }), true);
  assert.equal(memories.entries?.[0].title, "Revenue rule");
  assert.equal(await memories.remove("k1"), true);
  assert.deepEqual(memories.entries, []);
  assert.deepEqual(api.log, ["add Revenue", "list", "edit k1", "list", "delete k1", "list"]);
  assert.equal(memories.saving, false);
});

test("a failed change keeps the list, says what failed and reports false", async () => {
  const api = new FakeApi();
  api.stored = [entry("k1", REVENUE)];
  const memories = new Memories(api);
  await memories.load();
  api.failure = new ApiError(500, "HTTP 500: disk full");
  assert.equal(await memories.add(REVENUE), false);
  assert.equal(memories.error, "Could not save the memory. HTTP 500: disk full");
  assert.equal(await memories.remove("k1"), false);
  assert.equal(memories.error, "Could not delete the memory. HTTP 500: disk full");
  assert.deepEqual(memories.entries, [entry("k1", REVENUE)]);
  assert.equal(memories.saving, false);
});

test("a memory deleted elsewhere is dropped from the list", async () => {
  const api = new FakeApi();
  api.stored = [entry("k1", REVENUE)];
  const memories = new Memories(api);
  await memories.load();
  api.stored = [];
  api.deleteMemory = async () => {
    throw new ApiError(404, "This memory is no longer on the server.", "memory_not_found");
  };
  assert.equal(await memories.remove("k1"), false);
  assert.equal(memories.error, "Could not delete the memory. This memory is no longer on the server.");
  assert.deepEqual(memories.entries, []);
});

test("a list that fails to load keeps the last one and says so; the next load clears the error", async () => {
  const api = new FakeApi();
  api.stored = [entry("k1", REVENUE)];
  const memories = new Memories(api);
  await memories.load();
  api.failure = new TypeError("Failed to fetch");
  await memories.load();
  assert.equal(memories.error, "Could not load the memory. Failed to fetch");
  assert.equal(memories.entries?.length, 1);
  api.failure = null;
  await memories.load();
  assert.equal(memories.error, null);
});

test("only the latest of two overlapping loads is shown", async () => {
  const api = new FakeApi();
  let release;
  const slow = new Promise((resolve) => (release = resolve));
  let first = true;
  api.memories = async () => {
    if (first) {
      first = false;
      await slow;
      return { memories: [entry("old", REVENUE)], skipped: [] };
    }
    return { memories: [entry("new", REVENUE)], skipped: [] };
  };
  const memories = new Memories(api);
  const older = memories.load();
  await memories.load();
  release();
  await older;
  assert.deepEqual(
    memories.entries?.map((item) => item.key),
    ["new"],
  );
});
