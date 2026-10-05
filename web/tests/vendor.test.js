import assert from "node:assert/strict";
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { test } from "node:test";

const root = new URL("../", import.meta.url);
const vendored = new URL("src/nodestep-design/", root);
const sibling = new URL("../../nodestep-stylesheet/", root);
const read = (path) => readFileSync(new URL(path, root), "utf8");
const VERSION = "0.1.0a1";

test("the vendored nodestep-design copy is the stylesheet alone, at the nodestep version", () => {
  assert.deepEqual(readdirSync(vendored), ["nodestep.css"]);
  assert.ok(read("src/nodestep-design/nodestep.css").includes(`--nodestep-design-version: "${VERSION}";`));
});

test("the vendored stylesheet matches nodestep-stylesheet byte for byte when it is checked out next to text-to-sql-demo", (context) => {
  if (!existsSync(sibling)) {
    context.skip("nodestep-stylesheet is not checked out next to text-to-sql-demo");
    return;
  }
  assert.ok(
    readFileSync(new URL("nodestep.css", vendored)).equals(readFileSync(new URL("nodestep.css", sibling))),
    "nodestep.css differs from nodestep-stylesheet",
  );
});

test("the theme button, the question box, messages, chips and badges are text-to-sql-demo components", () => {
  for (const path of [
    "src/components/Badge.svelte",
    "src/components/Chips.svelte",
    "src/components/Composer.svelte",
    "src/components/Message.svelte",
    "src/components/ThemeButton.svelte",
    "src/lib/theme.ts",
  ]) {
    assert.ok(existsSync(new URL(path, root)), path);
  }
  for (const path of ["src/components/ThemeToggle.svelte", "src/components/Tabs.svelte", "src/components/index.ts", "src/nodestep.css"]) {
    assert.ok(!existsSync(new URL(path, root)), path);
  }
  const app = read("src/App.svelte");
  assert.match(app, /import Composer from "\.\/components\/Composer\.svelte";/);
  assert.match(app, /import ThemeButton from "\.\/components\/ThemeButton\.svelte";/);
  assert.match(read("src/components/ThemeButton.svelte"), /from "\.\.\/lib\/theme\.ts";/);
  const turn = read("src/components/TurnView.svelte");
  for (const name of ["Badge", "Chips", "Message"]) {
    assert.match(turn, new RegExp(`import ${name} from "\\./${name}\\.svelte";`), name);
  }
  assert.match(turn, /<Message role="assistant" busy=\{turn\.streaming\}>/);
  assert.match(turn, /<Chips label="Follow-up questions" items=\{suggestions\} disabled=\{busy\} onchoose=\{onsuggest\} \/>/);
  assert.match(turn, /<Badge status="stopped" \/>/);
  const user = read("src/components/UserTurn.svelte");
  assert.match(user, /import Message from "\.\/Message\.svelte";/);
  assert.match(user, /<Message\s+role="user"/);
  const appCss = read("src/app.css");
  for (const name of [".layout", ".sidebar", ".column", ".bubble", ".chip", ".composer", ".dock", ".log", ".turn-", ".panel-close", ".thread-"]) {
    assert.ok(!appCss.includes(name), name);
  }
});

test("the app is a fixed nodestep-design app shell with the threads at the start and the schema and memory at the end", () => {
  const app = read("src/App.svelte");
  assert.match(app, /<div class="nodestep-app nodestep-app-fixed" data-panel=\{panel === null \? undefined : SIDES\[panel\]\}>/);
  assert.match(app, /const SIDES: Record<Panel, "start" \| "end"> = \{ threads: "start", schema: "end", memory: "end" \};/);
  assert.match(app, /<main class="nodestep-app-main nodestep-chat chat" id="chat" tabindex="-1">/);
  assert.match(read("src/components/ThreadList.svelte"), /<nav class="nodestep-panel nodestep-app-start" id="threads-panel"/);
  const side = read("src/components/SidePanel.svelte");
  assert.match(side, /<section class="nodestep-panel nodestep-panel-end nodestep-app-end" id="side-panel"/);
  assert.match(side, /<div class="nodestep-tabs side-tabs" role="tablist"/);
  assert.match(side, /role="tab"\s+class="nodestep-tab"/);
  for (const file of ["src/components/ThreadList.svelte", "src/components/SidePanel.svelte"]) {
    assert.match(read(file), /class="nodestep-button nodestep-button-quiet nodestep-button-small nodestep-app-close"/, file);
  }
});
