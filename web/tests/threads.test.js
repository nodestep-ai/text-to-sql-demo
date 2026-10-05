import assert from "node:assert/strict";
import { test } from "node:test";
import { documentTitle, listedThreads, threadTitle } from "../src/lib/threads.ts";
import { assistantTurn, userTurn } from "../src/lib/turns.ts";

const older = { id: "t1", title: "Which store has the most returns?", database: "demo_shop", updated_at: "2026-10-01T08:00:00Z" };
const edited = { id: "t2", title: "Which ten products sold the most units?", database: "demo_shop", updated_at: "2026-10-01T09:00:00Z" };

test("a thread's title is its first question as shown, on one line and at most 80 characters", () => {
  assert.equal(threadTitle([userTurn("  What is the\nrevenue   per month? "), assistantTurn()]), "What is the revenue per month?");
  assert.equal(threadTitle([userTurn("x".repeat(90))]).length, 80);
  assert.equal(threadTitle([]), null);
  assert.equal(threadTitle([userTurn("   ")]), null);
});

test("the open thread is listed with the title of the version on screen", () => {
  const current = { id: "t2", title: "What is the revenue per month?", database: "demo_shop" };
  assert.deepEqual(listedThreads([edited, older], current), [{ ...edited, title: "What is the revenue per month?" }, older]);
  assert.deepEqual(listedThreads([edited, older], { ...current, title: null }), [edited, older]);
  assert.deepEqual(listedThreads([edited, older], null), [edited, older]);
});

test("a thread the server does not list yet comes first while its first answer runs", () => {
  const current = { id: "t3", title: "Write a report on sales by store in 2025.", database: "demo_shop" };
  const now = new Date("2026-10-01T10:00:00Z");
  const expected = { id: "t3", title: current.title, database: "demo_shop", updated_at: now.toISOString() };
  assert.deepEqual(listedThreads([older], current, now), [expected, older]);
  assert.deepEqual(listedThreads([], current, now), [expected]);
  assert.equal(listedThreads(null, current, now), null);
});

test("the tab title is the app name for a new chat, and the thread title first for a thread", () => {
  assert.equal(documentTitle(null), "text-to-sql-demo");
  assert.equal(documentTitle("Which store has the most returns?"), "Which store has the most returns? · text-to-sql-demo");
  assert.equal(documentTitle(""), "text-to-sql-demo");
});
