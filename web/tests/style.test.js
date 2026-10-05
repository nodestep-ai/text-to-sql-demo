import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { test } from "node:test";
import { tokenClass } from "../src/lib/sql.ts";

const root = new URL("../", import.meta.url);
const read = (path) => readFileSync(new URL(path, root), "utf8");
const nodestepCss = read("src/nodestep-design/nodestep.css");
const appCss = read("src/app.css");
const componentFiles = [
  "src/App.svelte",
  ...readdirSync(new URL("src/components/", root)).map((name) => `src/components/${name}`),
];
const components = componentFiles.map((path) => [path, read(path)]);

function selectors(css) {
  const plain = css.replace(/\/\*[\s\S]*?\*\//g, "");
  return [...plain.matchAll(/([^{}]+)\{/g)]
    .map((match) => match[1].trim())
    .filter((prelude) => !prelude.startsWith("@") && !/^(from|to|\d+%)$/.test(prelude))
    .flatMap((prelude) => prelude.split(",").map((selector) => selector.trim()));
}

const libraryFiles = readdirSync(new URL("src/lib/", root)).map((name) => `src/lib/${name}`);
const sources = [
  ["src/nodestep-design/nodestep.css", nodestepCss],
  ["src/app.css", appCss],
  ["index.html", read("index.html")],
  ...components,
  ...libraryFiles.map((path) => [path, read(path)]),
];

const COLOR_ROLES = [
  "page",
  "surface",
  "sunk",
  "border",
  "border-strong",
  "text",
  "text-secondary",
  "text-muted",
  "on-accent",
  "on-danger",
  "accent",
  "accent-hover",
  "accent-text",
  "accent-wash",
  "danger",
  "danger-hover",
  "danger-wash",
];

function hasClassSelector(css, name) {
  return new RegExp(`\\.${name}(?![\\w-])`).test(css);
}

const tokenValues = new Set(
  [...nodestepCss.matchAll(/--nodestep-color-[\w-]+: light-dark\((#[0-9a-f]{6}), (#[0-9a-f]{6})\)/g)].flatMap((match) => [
    match[1],
    match[2],
  ]),
);

function shapes(svg) {
  return [...svg.matchAll(/<(path|circle|rect)\b([^>]*)>/g)].map((match) =>
    [...match[2].matchAll(/(?<![\w-])(d|cx|cy|r|x|y|width|height|rx)="([^"]+)"/g)].map((pair) => pair.slice(1).join("=")).join(" "),
  );
}

function colorLiterals(text) {
  return [...text.matchAll(/(?<![\w&/])#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})\b|\b(?:rgba?|hsla?)\([^)]*\)/g)].map(
    (match) => match[0],
  );
}

test("the vendored nodestep.css declares exactly the nodestep-design color roles", () => {
  const declared = [...nodestepCss.matchAll(/--nodestep-color-([\w-]+)\s*:/g)].map((match) => match[1]);
  assert.deepEqual([...new Set(declared)].sort(), [...COLOR_ROLES].sort());
});

test("colors are written only in the color and shadow tokens of nodestep.css, and the favicon uses their values", () => {
  const found = [...sources, ["public/favicon.svg", read("public/favicon.svg")]].flatMap(([path, text]) => {
    const rest = path === "src/nodestep-design/nodestep.css" ? text.replace(/--nodestep-(?:color|shadow)-[\w-]+\s*:[^;]*;/g, "") : text;
    const allowed = path === "public/favicon.svg" ? tokenValues : new Set();
    return colorLiterals(rest)
      .filter((literal) => !allowed.has(literal.toLowerCase()))
      .map((literal) => `${path}: ${literal}`);
  });
  assert.deepEqual(found, []);
});

test("app text and lines in the accent use accent-text", () => {
  const plain = appCss.replace(/\/\*[\s\S]*?\*\//g, "");
  const bright = [...plain.matchAll(/(?<![\w-])(?:color|stroke):\s*var\(--nodestep-color-accent(?:-hover)?\)/g)].map(
    (match) => match[0],
  );
  assert.deepEqual(bright, []);
});

test("the vendored nodestep.css is the pinned nodestep-design 0.1.0a1", () => {
  assert.match(nodestepCss, /--nodestep-design-version: "0\.1\.0a1";/);
  assert.match(nodestepCss, /light-dark\(/);
});

test("main.ts loads nodestep.css before the app's own stylesheet", () => {
  const main = read("src/main.ts");
  const stylesheet = main.indexOf('import "./nodestep-design/nodestep.css";');
  const app = main.indexOf('import "./app.css";');
  assert.ok(stylesheet >= 0 && app > stylesheet);
});

test("the page sets the stored theme in its head, before anything is drawn", () => {
  const html = read("index.html");
  const head = html.slice(0, html.indexOf("</head>"));
  assert.match(head, /<meta name="color-scheme" content="light dark"/);
  assert.match(head, /<script>[\s\S]*localStorage\.getItem\("nodestep-theme"\)[\s\S]*dataset\.theme[\s\S]*<\/script>/);
  assert.match(head, /try \{[\s\S]*\} catch \{\}/);
});

test("app.css has no color literals of its own", () => {
  const plain = appCss.replace(/\/\*[\s\S]*?\*\//g, "");
  assert.doesNotMatch(plain, /#[0-9a-fA-F]{3,8}\b/);
  assert.doesNotMatch(plain, /\b(rgb|rgba|hsl|hsla|oklch|lab)\(/);
  assert.doesNotMatch(plain, /:\s*(white|black)\b/);
});

test("app.css leaves the base element styles to nodestep.css", () => {
  const base = ["*", "html", "body", "h1", "h2", "p", "button", "input", "select", "textarea", "table", "th", "td"];
  const repeated = selectors(appCss).filter(
    (selector) =>
      base.includes(selector) ||
      selector.startsWith(":focus-visible") ||
      selector.startsWith("*::") ||
      /^(button|input|textarea)\[/.test(selector) ||
      [".visually-hidden", ".skip-link"].some((name) => selector.startsWith(name)),
  );
  assert.deepEqual(repeated, []);
});

test("every custom property the app reads is defined by nodestep.css or app.css", () => {
  const defined = new Set([...`${nodestepCss}\n${appCss}`.matchAll(/(--[\w-]+)\s*:/g)].map((match) => match[1]));
  const sources = [["src/app.css", appCss], ...components];
  const missing = sources.flatMap(([path, text]) =>
    [...text.matchAll(/var\((--[\w-]+)/g)].map((match) => match[1]).filter((name) => !defined.has(name)).map((name) => `${path}: ${name}`),
  );
  assert.deepEqual(missing, []);
});

test("every nodestep- class in the components exists in nodestep.css", () => {
  const missing = components.flatMap(([path, text]) => {
    const used = new Set([...text.matchAll(/(?<![-\w])nodestep-[a-z][a-z-]*/g)].map((match) => match[0]));
    return [...used]
      .filter((name) => name !== "nodestep-design" && !name.endsWith("-") && !hasClassSelector(nodestepCss, name))
      .map((name) => `${path}: ${name}`);
  });
  assert.deepEqual(missing, []);
});

test("every button, select and textarea carries a class", () => {
  const bare = components.flatMap(([path, text]) =>
    [...text.matchAll(/<(button|select|textarea)\b[^>]*>/g)]
      .filter((match) => !/\bclass=/.test(match[0]))
      .map((match) => `${path}: ${match[0].replace(/\s+/g, " ").slice(0, 60)}`),
  );
  assert.deepEqual(bare, []);
});

test("each SQL token kind gets a nodestep-design code token class that nodestep.css styles", () => {
  const expected = {
    keyword: "nodestep-token-keyword",
    function: "nodestep-token-function",
    identifier: "nodestep-token-identifier",
    string: "nodestep-token-string",
    number: "nodestep-token-number",
    comment: "nodestep-token-comment",
    operator: "nodestep-token-operator",
    punctuation: "nodestep-token-operator",
  };
  for (const [kind, name] of Object.entries(expected)) {
    assert.equal(tokenClass(kind), name);
    assert.ok(hasClassSelector(nodestepCss, name), name);
  }
  assert.equal(tokenClass("space"), null);
});

test("the page loads the favicon as a file, with the mark of the README in heavier strokes", () => {
  const html = read("index.html");
  assert.match(html, /<link rel="icon" href="\/favicon\.svg" type="image\/svg\+xml" \/>/);
  assert.doesNotMatch(html, /data:image/);
  const icon = read("public/favicon.svg");
  const accent = nodestepCss.match(/--nodestep-color-accent: light-dark\((#[0-9a-f]{6}), /)[1];
  assert.ok(icon.includes(`fill="${accent}"`));
  assert.match(icon, /@media \(prefers-color-scheme: dark\)/);
  assert.equal(shapes(icon).length, shapes(read("../assets/text-to-sql-demo.svg")).length);
});

test("the brand is the mark of the README, with the ink from the text and the accent from nodestep.css", () => {
  const brand = read("src/App.svelte").match(
    /<h1 class="nodestep-brand"\s*>\s*<svg class="nodestep-brand-mark" viewBox="0 0 64 64" aria-hidden="true" focusable="false"\s*>([\s\S]*?)<\/svg\s*>text-to-sql-demo<\/h1\s*>/,
  );
  assert.ok(brand);
  assert.deepEqual(shapes(brand[1]), shapes(read("../assets/text-to-sql-demo.svg")));
  assert.deepEqual([...new Set([...brand[1].matchAll(/\b(?:fill|stroke)="([^"]+)"/g)].map((match) => match[1]))], [
    "currentColor",
  ]);
  assert.match(brand[1], /<circle class="nodestep-mark-accent-fill"/);
  assert.ok(
    read("../README.md").startsWith(
      '<picture>\n  <source media="(prefers-color-scheme: dark)" srcset="assets/text-to-sql-demo-dark.svg">\n' +
        '  <img src="assets/text-to-sql-demo.svg" alt="" width="56">\n</picture>\n\n# text-to-sql-demo\n',
    ),
  );
  for (const [file, ink] of [
    ["../assets/text-to-sql-demo.svg", "#1f1b17"],
    ["../assets/text-to-sql-demo-dark.svg", "#ede9e4"],
  ]) {
    const logo = read(file);
    assert.deepEqual(shapes(logo), shapes(read("../assets/text-to-sql-demo.svg")));
    assert.deepEqual([...new Set(logo.match(/#[0-9a-f]{6}\b/g))].sort(), [ink, "#f5a524"].sort());
    assert.doesNotMatch(logo, /prefers-color-scheme/);
  }
});

test("the theme button in the top bar is ThemeButton: one icon button named Dark theme, with a moon and a sun", () => {
  const app = read("src/App.svelte");
  assert.match(app, /import ThemeButton from "\.\/components\/ThemeButton\.svelte";/);
  assert.match(app, /<ThemeButton \/>\s*<\/div>\s*<\/header>/);
  const toggle = read("src/components/ThemeButton.svelte");
  const button = toggle.match(/<button\b([^>]*)>([\s\S]*?)<\/button\s*>/);
  assert.ok(button);
  assert.match(button[1], /\btype="button"/);
  assert.match(button[1], /\bclass="nodestep-theme-button"/);
  assert.match(button[1], /\baria-label="Dark theme"/);
  assert.match(button[1], /\baria-pressed=\{shown === "dark"\}/);
  assert.match(button[1], /\bonclick=\{toggle\}/);
  const icons = [...button[2].matchAll(/<svg\s+class="(nodestep-theme-\w+)"\s+viewBox="0 0 16 16"\s+aria-hidden="true"\s+focusable="false"/g)];
  assert.deepEqual(
    icons.map((match) => match[1]),
    ["nodestep-theme-moon", "nodestep-theme-sun"],
  );
  assert.doesNotMatch(button[2], /\b(?:fill|stroke|style)="/);
});

test("below 48rem the panel toggles in the top bar show an icon and keep their names", () => {
  const app = read("src/App.svelte");
  for (const [name, side] of [
    ["Threads", "start"],
    ["Schema", "end"],
    ["Memory", "end"],
  ]) {
    const button = app.match(
      new RegExp(`<button[^>]*nodestep-app-toggle nodestep-app-toggle-${side} toggle"\\s*aria-label="${name}"[\\s\\S]*?</button`),
    );
    assert.ok(button, name);
    assert.match(button[0], new RegExp(`aria-label="${name}"`));
    assert.match(button[0], /<svg class="toggle-icon" viewBox="0 0 16 16" aria-hidden="true" focusable="false"/);
    assert.match(button[0], new RegExp(`<span class="toggle-label">${name}</span>`));
  }
  const plain = appCss.replace(/\/\*[\s\S]*?\*\//g, "");
  assert.match(plain, /\.toggle-icon \{[^}]*display: none;/);
  const narrow = plain.match(/@media \(width < 48rem\) \{([\s\S]*?)\n\}/);
  assert.ok(narrow);
  assert.match(narrow[1], /\.toggle-icon \{\s*display: block;\s*\}/);
  assert.match(narrow[1], /\.toggle-label \{\s*display: none;\s*\}/);
});

test("the composer and the edit box are the nodestep-design question box, with the shared focus ring while their field has focus", () => {
  const plain = nodestepCss.replace(/\/\*[\s\S]*?\*\//g, "");
  const rule = plain.match(/\.nodestep-composer:has\(\.nodestep-composer-field:focus-visible\) \{([^}]*)\}/);
  assert.ok(rule);
  assert.match(rule[1], /outline: var\(--nodestep-focus-ring\);/);
  assert.match(rule[1], /box-shadow: var\(--nodestep-focus-halo\);/);
  const edit = read("src/components/UserTurn.svelte");
  assert.match(edit, /<form class="nodestep-composer nodestep-composer-stacked" onsubmit=\{save\}>/);
  assert.match(edit, /class="nodestep-composer-field"/);
  const app = appCss.replace(/\/\*[\s\S]*?\*\//g, "");
  assert.doesNotMatch(app, /:focus-within|\.composer|\.edit/);
});

test("the composer has one round icon button, an arrow named Send or a filled square named Stop answer", () => {
  const composer = read("src/components/Composer.svelte");
  const buttons = [...composer.matchAll(/<button\b([^>]*)>([\s\S]*?)<\/button\s*>/g)];
  assert.equal(buttons.length, 1);
  const [, attributes, content] = buttons[0];
  assert.match(attributes, /\btype="submit"/);
  assert.match(attributes, /\bclass="nodestep-composer-send"/);
  assert.match(attributes, /\baria-label=\{streaming \? "Stop answer" : "Send"\}/);
  assert.match(content, /<svg\s+class="nodestep-composer-stop"\s+viewBox="0 0 16 16"\s+aria-hidden="true"\s+focusable="false"/);
  assert.match(content, /<svg\s+viewBox="0 0 16 16"\s+aria-hidden="true"\s+focusable="false"\s*>\s*<path d="M8 13V3\.5/);
  assert.doesNotMatch(content, /\b(?:fill|stroke|style)="/);
  assert.match(composer, /<p id="\{uid\}-hint" class="nodestep-visually-hidden">\s*Enter sends\. Shift\+Enter adds a line\.\s*<\/p>/);
  const plain = nodestepCss.replace(/\/\*[\s\S]*?\*\//g, "");
  const send = plain.match(/\.nodestep-composer-send \{([^}]*)\}/);
  assert.ok(send);
  assert.match(send[1], /border-radius: var\(--nodestep-radius-pill\);/);
  assert.match(send[1], /background: var\(--nodestep-color-accent\);/);
  const disabled = plain.match(/\.nodestep-composer-send\[aria-disabled="true"\] \{([^}]*)\}/);
  assert.ok(disabled);
  assert.match(disabled[1], /background: var\(--nodestep-color-sunk\);/);
  assert.match(read("src/App.svelte"), /<Composer\b[^>]*\bonsend=\{ask\}/);
});

test("chart bars have an accent-text edge", () => {
  const plain = nodestepCss.replace(/\/\*[\s\S]*?\*\//g, "");
  const rule = plain.match(/\.nodestep-chart-bar \{([^}]*)\}/);
  assert.ok(rule);
  assert.match(rule[1], /stroke: var\(--nodestep-color-accent-text\);/);
  assert.match(read("src/components/Chart.svelte"), /class="nodestep-chart-bar"/);
});

test("rules that hide the focus ring also hide the focus edge of nodestep.css", () => {
  for (const css of [appCss, nodestepCss]) {
    const plain = css.replace(/\/\*[\s\S]*?\*\//g, "");
    const hidden = [...plain.matchAll(/([^{}]+)\{([^}]*outline: none;[^}]*)\}/g)];
    assert.deepEqual(
      hidden.filter((match) => !/box-shadow: none;/.test(match[2])).map((match) => match[1].trim()),
      [],
    );
  }
});

test("a long tool summary wraps inside its row", () => {
  const plain = appCss.replace(/\/\*[\s\S]*?\*\//g, "");
  const rule = plain.match(/\.tool-summary \{([^}]*)\}/);
  assert.ok(rule);
  assert.match(rule[1], /overflow-wrap: anywhere;/);
});

test("the Schema and Memory tabs share the panel width, and memory rows put their buttons beside the title", () => {
  const plain = appCss.replace(/\/\*[\s\S]*?\*\//g, "");
  assert.match(plain, /\.side-tabs \{[^}]*flex: 1;/);
  assert.match(plain, /\.side-tabs \.nodestep-tab \{[^}]*flex: 1 1 0;[^}]*justify-content: center;/);
  assert.match(plain, /\.memory-head \{[^}]*display: flex;[^}]*justify-content: space-between;/);
  assert.match(plain, /\.memory-form-actions \{[^}]*justify-content: flex-end;/);
});
