import assert from "node:assert/strict";
import { test } from "node:test";
import {
  databaseFacts,
  databaseTitle,
  exampleQuestions,
  initialDatabase,
  listingKey,
  pickerOptions,
  StoredChoice,
} from "../src/lib/databases.ts";

const sales = { id: "sales", title: "Sales", description: "Orders and invoices", table_count: 11, size_bytes: 1067008 };
const shop = { id: "demo_shop", title: "Demo shop", description: "Retail", table_count: 8, size_bytes: 524288 };
const bare = { id: "other_db", title: "other_db", description: "", table_count: 1, size_bytes: 8192 };

function memoryStorage() {
  const items = new Map();
  return { getItem: (key) => items.get(key) ?? null, setItem: (key, value) => items.set(key, String(value)) };
}

test("a stored choice is written and read back under its key", () => {
  const storage = memoryStorage();
  const choice = new StoredChoice("text-to-sql-demo.database", () => storage);
  assert.equal(choice.read(), null);
  choice.write("demo_shop");
  assert.equal(choice.read(), "demo_shop");
  assert.equal(storage.getItem("text-to-sql-demo.database"), "demo_shop");
});

test("a stored choice never throws when storage is blocked or full", () => {
  const blocked = new StoredChoice("k", () => {
    throw new DOMException("denied", "SecurityError");
  });
  assert.equal(blocked.read(), null);
  assert.doesNotThrow(() => blocked.write("x"));
  const failing = new StoredChoice("k", () => ({
    getItem() {
      throw new Error("broken");
    },
    setItem() {
      throw new DOMException("full", "QuotaExceededError");
    },
  }));
  assert.equal(failing.read(), null);
  assert.doesNotThrow(() => failing.write("x"));
  assert.doesNotThrow(() => new StoredChoice("k").read());
});

test("the initial database is the stored one while the server lists it, otherwise demo_shop, otherwise the first", () => {
  assert.equal(initialDatabase([sales, shop], "sales"), "sales");
  assert.equal(initialDatabase([sales, shop], "gone"), "demo_shop");
  assert.equal(initialDatabase([sales, shop], null), "demo_shop");
  assert.equal(initialDatabase([sales, bare], null), "sales");
  assert.equal(initialDatabase([], "demo_shop"), null);
});

test("picker options use titles and keep a selected database the server no longer lists", () => {
  assert.deepEqual(pickerOptions([sales, { ...bare, title: "" }], "sales"), [
    { id: "sales", label: "Sales" },
    { id: "other_db", label: "other_db" },
  ]);
  assert.deepEqual(pickerOptions([sales], "gone"), [
    { id: "sales", label: "Sales" },
    { id: "gone", label: "gone (not available)" },
  ]);
  assert.deepEqual(pickerOptions([sales], null), [{ id: "sales", label: "Sales" }]);
});

test("the listing key changes only when the database changes on the server or leaves the list", () => {
  assert.equal(listingKey([sales, shop], "demo_shop"), listingKey([{ ...shop, title: "Shop" }], "demo_shop"));
  assert.notEqual(listingKey([shop], "demo_shop"), listingKey([{ ...shop, size_bytes: 1 }], "demo_shop"));
  assert.notEqual(listingKey([shop], "demo_shop"), listingKey([{ ...shop, table_count: 9 }], "demo_shop"));
  assert.notEqual(listingKey([shop], "demo_shop"), listingKey([sales], "demo_shop"));
  assert.equal(listingKey(null, "demo_shop"), listingKey([sales], "demo_shop"));
  assert.notEqual(listingKey([shop], "demo_shop"), listingKey([shop], "sales"));
});

test("a database title falls back to its id", () => {
  assert.equal(databaseTitle([sales, shop], "demo_shop"), "Demo shop");
  assert.equal(databaseTitle([sales], "gone"), "gone");
  assert.equal(databaseTitle(null, "sales"), "sales");
  assert.equal(databaseTitle([{ ...sales, title: "" }], "sales"), "sales");
});

test("database facts give the table count and the file size", () => {
  assert.equal(databaseFacts(sales), "11 tables · 1 MB");
  assert.equal(databaseFacts(bare), "1 table · 8 KB");
});

test("example questions come from the database listing, with general ones when it has none", () => {
  const examples = ["Which ten products sold the most units?", "Write a report on sales by store in 2025."];
  assert.deepEqual(exampleQuestions({ ...shop, examples }), examples);
  const generic = exampleQuestions({ ...bare, examples: [] });
  assert.ok(generic.length >= 3);
  assert.deepEqual(exampleQuestions(bare), generic);
  assert.deepEqual(exampleQuestions(null), generic);
});
