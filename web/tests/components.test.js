import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { registerHooks } from "node:module";
import { test } from "node:test";
import { createRawSnippet } from "svelte";
import { compile } from "svelte/compiler";
import { render } from "svelte/server";

registerHooks({
  load(url, context, nextLoad) {
    if (!url.endsWith(".svelte")) return nextLoad(url, context);
    const file = new URL(url);
    const { js } = compile(readFileSync(file, "utf8"), { generate: "server", filename: file.pathname });
    return { format: "module", source: js.code, shortCircuit: true };
  },
});

const root = new URL("../", import.meta.url);
const folder = new URL("src/components/", root);
const read = (path) => readFileSync(new URL(path, root), "utf8");
const COMPONENTS = ["Badge", "Chips", "Composer", "Message", "ThemeButton", "MemoryForm", "MemoryView", "SidePanel"];
const THEME_BUTTON =
  '<button class="nodestep-theme-button" type="button" aria-label="Dark theme" aria-pressed="false">' +
  '<svg class="nodestep-theme-moon" viewBox="0 0 16 16" aria-hidden="true" focusable="false">' +
  '<path d="M7.14 2.06A6 6 0 1 0 13.94 8.87 5 5 0 0 1 7.14 2.06z"></path></svg>' +
  '<svg class="nodestep-theme-sun" viewBox="0 0 16 16" aria-hidden="true" focusable="false">' +
  '<circle cx="8" cy="8" r="3"></circle>' +
  '<path d="M8 1v2M8 13v2M1 8h2M13 8h2M3.05 3.05l1.41 1.41M11.54 11.54l1.41 1.41' +
  'M3.05 12.95l1.41-1.41M11.54 4.46l1.41-1.41"></path></svg>' +
  "</button>";
const nothing = () => {};

function stylesheetClasses() {
  const plain = read("src/nodestep-design/nodestep.css").replace(/\/\*[\s\S]*?\*\//g, "");
  const classes = new Set();
  for (const prelude of plain.matchAll(/([^{};]+)\{/g)) {
    if (prelude[1].trim().startsWith("@")) continue;
    for (const match of prelude[1].matchAll(/\.(-?[_a-zA-Z][\w-]*)/g)) classes.add(match[1]);
  }
  return classes;
}

function markupClasses(html) {
  return [...html.matchAll(/\sclass="([^"]*)"/g)].flatMap((match) => match[1].split(/\s+/).filter(Boolean));
}

async function load(name) {
  const file = new URL(`${name}.svelte`, folder);
  const { js } = compile(readFileSync(file, "utf8"), { generate: "server", filename: file.pathname });
  const code = js.code.replace(/from\s+["']([^"']+)["']/g, (_, specifier) => {
    const resolved = specifier.startsWith(".") ? new URL(specifier, file).href : import.meta.resolve(specifier);
    return `from "${resolved}"`;
  });
  const module = await import(`data:text/javascript;base64,${Buffer.from(code).toString("base64")}`);
  return module.default;
}

async function rendered(name, props = {}) {
  return render(await load(name), { props })
    .body.replace(/<!--[\s\S]*?-->/g, "")
    .replace(/>\s+</g, "><")
    .trim();
}

function snippet(markup) {
  return createRawSnippet(() => ({ render: () => markup }));
}

test("the components use Svelte 5 runes and onclick, and only classes that nodestep.css has", () => {
  const known = stylesheetClasses();
  for (const name of COMPONENTS) {
    const source = read(`src/components/${name}.svelte`);
    assert.match(source, /<script lang="ts">/, name);
    assert.match(source, name === "ThemeButton" ? /\$state</ : /\$props\(\)/, name);
    assert.doesNotMatch(source, /\bon:\w+|export let |createEventDispatcher|<style/, name);
    const unknown = [...source.matchAll(/nodestep-[a-z][a-z-]*/g)]
      .map((match) => match[0])
      .filter((name) => !known.has(name) && !name.endsWith("-") && name !== "nodestep-theme");
    assert.deepEqual(unknown, [], name);
  }
});

test("ThemeButton renders one icon button named Dark theme, with a moon and a sun", async () => {
  assert.equal(await rendered("ThemeButton"), THEME_BUTTON);
});

test("Composer renders the question box with an arrow named Send", async () => {
  const html = await rendered("Composer", {
    placeholder: "Ask a question about Demo shop",
    blocked: false,
    streaming: false,
    onsend: () => true,
    onstop: nothing,
  });
  assert.match(html, /^<form class="nodestep-composer">/);
  assert.match(html, /<label for="[^"]+" class="nodestep-visually-hidden">Question<\/label>/);
  assert.match(
    html,
    /<textarea id="[^"]+" class="nodestep-composer-field" rows="1" placeholder="Ask a question about Demo shop" aria-describedby="[^"]+"><\/textarea>/,
  );
  assert.match(html, /<p id="[^"]+" class="nodestep-visually-hidden">Enter sends\. Shift\+Enter adds a line\.<\/p>/);
  assert.match(
    html,
    /<button type="submit" class="nodestep-composer-send" aria-label="Send" aria-disabled="true"><svg viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="M8 13V3\.5M3\.75 7\.75 8 3\.5l4\.25 4\.25"><\/path><\/svg><\/button><\/form>$/,
  );
  assert.doesNotMatch(html, /\b(?:fill|stroke|style)="/);
});

test("Composer shows a filled square named Stop answer while an answer streams", async () => {
  const html = await rendered("Composer", {
    placeholder: "Ask",
    blocked: true,
    streaming: true,
    onsend: () => true,
    onstop: nothing,
  });
  assert.match(
    html,
    /<button type="submit" class="nodestep-composer-send" aria-label="Stop answer" aria-disabled="false"><svg class="nodestep-composer-stop" viewBox="0 0 16 16" aria-hidden="true" focusable="false"><rect x="3\.5" y="3\.5" width="9" height="9" rx="1\.5"><\/rect><\/svg><\/button>/,
  );
});

test("Composer takes its label from a prop", async () => {
  const html = await rendered("Composer", {
    label: "Search",
    placeholder: "Search the documents",
    blocked: false,
    streaming: false,
    onsend: () => true,
    onstop: nothing,
  });
  assert.match(html, /class="nodestep-visually-hidden">Search<\/label>/);
});

test("Message renders a question in a bubble with its actions", async () => {
  const html = await rendered("Message", {
    role: "user",
    text: "Which ten products sold the most units?",
    actions: snippet('<button class="nodestep-button nodestep-button-quiet nodestep-button-small" type="button">Edit</button>'),
  });
  assert.equal(
    html,
    '<article class="nodestep-chat-message nodestep-chat-message-user" aria-label="Question">' +
      '<p class="nodestep-chat-bubble nodestep-chat-text">Which ten products sold the most units?</p>' +
      '<div class="nodestep-chat-actions"><button class="nodestep-button nodestep-button-quiet nodestep-button-small" type="button">Edit</button></div>' +
      "</article>",
  );
});

test("Message renders an answer with its content and marks it busy while it streams", async () => {
  const html = await rendered("Message", {
    role: "assistant",
    busy: true,
    children: snippet('<p class="nodestep-chat-text">Salted caramel bar sold the most units.</p>'),
  });
  assert.equal(
    html,
    '<article class="nodestep-chat-message" aria-label="Answer" aria-busy="true">' +
      '<p class="nodestep-chat-text">Salted caramel bar sold the most units.</p>' +
      "</article>",
  );
  assert.equal(
    await rendered("Message", { role: "assistant", label: "Result", text: "Done." }),
    '<article class="nodestep-chat-message" aria-label="Result"><p class="nodestep-chat-text">Done.</p></article>',
  );
});

test("Chips renders follow-up questions as chips, disabled while busy", async () => {
  const html = await rendered("Chips", {
    label: "Follow-up questions",
    items: ["Which store sold the most of it?", "How did units change by month?"],
    disabled: true,
    onchoose: nothing,
  });
  assert.equal(
    html,
    '<section class="nodestep-chips" aria-label="Follow-up questions">' +
      '<p class="nodestep-chips-label" aria-hidden="true">Follow-up questions</p>' +
      '<ul class="nodestep-chips-list">' +
      '<li><button type="button" class="nodestep-chip" aria-disabled="true">Which store sold the most of it?</button></li>' +
      '<li><button type="button" class="nodestep-chip" aria-disabled="true">How did units change by month?</button></li>' +
      "</ul></section>",
  );
});

test("Badge renders each status with its label", async () => {
  for (const [status, label] of [
    ["completed", "Completed"],
    ["running", "Running"],
    ["paused", "Paused"],
    ["stopped", "Stopped"],
    ["failed", "Failed"],
  ]) {
    assert.equal(await rendered("Badge", { status }), `<span class="nodestep-badge nodestep-badge-${status}">${label}</span>`);
  }
  assert.equal(
    await rendered("Badge", { status: "failed", label: "Tool error" }),
    '<span class="nodestep-badge nodestep-badge-failed">Tool error</span>',
  );
});

const REVENUE = {
  key: "k1",
  title: "Revenue",
  content: "Revenue counts completed orders only.",
  created_at: null,
  updated_at: null,
};
const accepted = async () => true;

function memoryView(props = {}) {
  return rendered("MemoryView", {
    entries: [REVENUE],
    skipped: [],
    error: null,
    saving: false,
    onadd: accepted,
    onedit: accepted,
    ondelete: accepted,
    ...props,
  });
}

test("MemoryView lists each memory with Edit and Delete buttons named after it", async () => {
  const html = await memoryView();
  assert.match(
    html,
    /<button type="button" class="nodestep-button nodestep-button-secondary nodestep-button-small" data-action="add">Add memory<\/button>/,
  );
  assert.match(
    html,
    /<ul class="memory-list" aria-label="Memories"><li class="memory-item" data-key="k1"><div class="memory-head"><p class="memory-title">Revenue<\/p><div class="nodestep-cluster memory-actions">.*?<\/div><\/div><p class="memory-content">Revenue counts completed orders only\.<\/p>/,
  );
  assert.match(html, /<p class="nodestep-hint">[^<]*searches the title and the text[^<]*<\/p>/);
  assert.match(html, /aria-label="Edit Revenue">Edit<\/button>/);
  assert.match(html, /aria-label="Delete Revenue">Delete<\/button>/);
  assert.doesNotMatch(html, /Loading|No memories yet|left out/);
});

test("MemoryView says when there are no memories, shows errors and names the files left out", async () => {
  assert.match(await memoryView({ entries: [] }), /<p class="nodestep-hint">No memories yet\.<\/p>/);
  assert.match(await memoryView({ entries: null }), /<p class="nodestep-hint">Loading…<\/p>/);
  const failed = await memoryView({ entries: null, error: "Could not load the memory. Failed to fetch" });
  assert.match(failed, /<p class="nodestep-message nodestep-message-error" role="alert">Could not load the memory\. Failed to fetch<\/p>/);
  assert.doesNotMatch(failed, /Loading/);
  assert.match(
    await memoryView({ skipped: ["a.json", "b.json"] }),
    /<p class="nodestep-hint">2 memory files left out: a\.json, b\.json\.<\/p>/,
  );
});

test("MemoryForm has a title field and a text box within the server's limits, and Save waits for both", async () => {
  const empty = await rendered("MemoryForm", { label: "New memory", saving: false, onsave: nothing, oncancel: nothing });
  assert.match(empty, /^<form class="nodestep-stack nodestep-stack-small memory-form" aria-label="New memory">/);
  assert.match(empty, /<label class="nodestep-label" for="[^"]+">Title<\/label><input id="[^"]+" class="nodestep-input" maxlength="120" autocomplete="off" value=""\/?>/);
  assert.match(empty, /<label class="nodestep-label" for="[^"]+">Text<\/label><textarea id="[^"]+" class="nodestep-textarea" rows="3" maxlength="2000"><\/textarea>/);
  assert.match(
    empty,
    /<div class="nodestep-cluster memory-form-actions"><button type="button" class="nodestep-button nodestep-button-secondary nodestep-button-small">Cancel<\/button><button type="submit" class="nodestep-button nodestep-button-primary nodestep-button-small" aria-disabled="true">Save<\/button><\/div>/,
  );
  const filled = await rendered("MemoryForm", { label: "Edit memory", initial: REVENUE, saving: false, onsave: nothing, oncancel: nothing });
  assert.match(filled, /value="Revenue"/);
  assert.match(filled, />Revenue counts completed orders only\.<\/textarea>/);
  assert.match(filled, /aria-disabled="false">Save<\/button>/);
});

test("SidePanel has a Schema and a Memory tab and shows the selected one", async () => {
  const html = await rendered("SidePanel", {
    tab: "memory",
    ontab: nothing,
    onclose: nothing,
    schemaTab: snippet("<p>tables</p>"),
    memoryTab: snippet("<p>memories</p>"),
  });
  assert.match(html, /<button type="button" role="tab" class="nodestep-tab" id="schema-tab" aria-selected="false" aria-controls="side-tabpanel" tabindex="-1">Schema<\/button>/);
  assert.match(html, /<button type="button" role="tab" class="nodestep-tab" id="memory-tab" aria-selected="true" aria-controls="side-tabpanel" tabindex="0">Memory<\/button>/);
  assert.match(html, /<div class="nodestep-panel-body" id="side-tabpanel" role="tabpanel" aria-labelledby="memory-tab"><p>memories<\/p><\/div>/);
  assert.doesNotMatch(html, /tables/);
});

test("every class the components render is styled by nodestep.css", async () => {
  const known = stylesheetClasses();
  const html = [
    await rendered("ThemeButton"),
    await rendered("Composer", { placeholder: "", blocked: false, streaming: true, onsend: () => true, onstop: nothing }),
    await rendered("Message", { role: "user", text: "Hi" }),
    await rendered("Chips", { label: "More", items: ["One"], disabled: false, onchoose: nothing }),
    await rendered("Badge", { status: "running" }),
  ].join("");
  assert.deepEqual(
    markupClasses(html).filter((name) => !known.has(name)),
    [],
  );
  const own = new Set([...read("src/app.css").matchAll(/\.([a-z][\w-]*)/g)].map((match) => match[1]));
  const memory = [
    await memoryView(),
    await rendered("MemoryForm", { label: "New memory", saving: false, onsave: nothing, oncancel: nothing }),
  ].join("");
  assert.deepEqual(
    markupClasses(memory).filter((name) => !known.has(name) && !own.has(name)),
    [],
  );
});
