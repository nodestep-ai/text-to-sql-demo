import assert from "node:assert/strict";
import { test } from "node:test";
import { sqlLines, tokenizeSql } from "../src/lib/sql.ts";

function kinds(sql) {
  return tokenizeSql(sql)
    .filter((token) => token.kind !== "space")
    .map((token) => [token.kind, token.text]);
}

test("the tokens put back together give the SQL unchanged", () => {
  const samples = [
    "SELECT\n  p.name AS product,\n  SUM(i.quantity) AS units\nFROM order_items AS i\nLIMIT 10",
    "SELECT 'it''s' -- note\n/* block\ncomment */ FROM \"odd \"\"name\"\"\"",
    "SELECT 'unterminated",
    "",
  ];
  for (const sql of samples) {
    assert.equal(
      tokenizeSql(sql)
        .map((token) => token.text)
        .join(""),
      sql,
    );
  }
});

test("keywords are found in any case, and other words are identifiers", () => {
  assert.deepEqual(kinds("SELECT name from Orders AS o WHERE o.status IS NOT NULL"), [
    ["keyword", "SELECT"],
    ["identifier", "name"],
    ["keyword", "from"],
    ["identifier", "Orders"],
    ["keyword", "AS"],
    ["identifier", "o"],
    ["keyword", "WHERE"],
    ["identifier", "o"],
    ["punctuation", "."],
    ["identifier", "status"],
    ["keyword", "IS"],
    ["keyword", "NOT"],
    ["keyword", "NULL"],
  ]);
});

test("a bare name right before an opening parenthesis is a function", () => {
  assert.deepEqual(kinds("ROUND(SUM(units), 2)"), [
    ["function", "ROUND"],
    ["punctuation", "("],
    ["function", "SUM"],
    ["punctuation", "("],
    ["identifier", "units"],
    ["punctuation", ")"],
    ["punctuation", ","],
    ["number", "2"],
    ["punctuation", ")"],
  ]);
  assert.deepEqual(kinds("strftime('%Y', o.ordered_at)").slice(0, 2), [
    ["function", "strftime"],
    ["punctuation", "("],
  ]);
});

test("keywords, quoted names and names followed by a space stay what they are before a parenthesis", () => {
  assert.deepEqual(kinds('CAST(x AS REAL) IN (1) OVER (w) "sum"(a) totals (b)').filter(([kind]) => kind !== "punctuation"), [
    ["keyword", "CAST"],
    ["identifier", "x"],
    ["keyword", "AS"],
    ["identifier", "REAL"],
    ["keyword", "IN"],
    ["number", "1"],
    ["keyword", "OVER"],
    ["identifier", "w"],
    ["identifier", '"sum"'],
    ["identifier", "a"],
    ["identifier", "totals"],
    ["identifier", "b"],
  ]);
});

test("quoted identifiers keep their doubled quotes and brackets", () => {
  assert.deepEqual(kinds('SELECT "order ""id""", `total`, [unit price] FROM t'), [
    ["keyword", "SELECT"],
    ["identifier", '"order ""id"""'],
    ["punctuation", ","],
    ["identifier", "`total`"],
    ["punctuation", ","],
    ["identifier", "[unit price]"],
    ["keyword", "FROM"],
    ["identifier", "t"],
  ]);
});

test("strings keep escaped quotes, and a blob literal is a string", () => {
  assert.deepEqual(kinds("WHERE name = 'O''Brien' OR data = X'0A'"), [
    ["keyword", "WHERE"],
    ["identifier", "name"],
    ["operator", "="],
    ["string", "'O''Brien'"],
    ["keyword", "OR"],
    ["identifier", "data"],
    ["operator", "="],
    ["string", "X'0A'"],
  ]);
  assert.deepEqual(kinds("'open"), [["string", "'open"]]);
});

test("numbers include decimals, exponents and hexadecimal, but not digits inside a name", () => {
  assert.deepEqual(kinds("SELECT 10, 2.5, .75, 1e3, 0x1F, t1.col2 - -3"), [
    ["keyword", "SELECT"],
    ["number", "10"],
    ["punctuation", ","],
    ["number", "2.5"],
    ["punctuation", ","],
    ["number", ".75"],
    ["punctuation", ","],
    ["number", "1e3"],
    ["punctuation", ","],
    ["number", "0x1F"],
    ["punctuation", ","],
    ["identifier", "t1"],
    ["punctuation", "."],
    ["identifier", "col2"],
    ["operator", "-"],
    ["operator", "-"],
    ["number", "3"],
  ]);
});

test("line and block comments run to their end, and an open block comment to the end of the text", () => {
  assert.deepEqual(kinds("SELECT 1 -- the count\nFROM t /* all\nrows */ LIMIT 1 /* open"), [
    ["keyword", "SELECT"],
    ["number", "1"],
    ["comment", "-- the count"],
    ["keyword", "FROM"],
    ["identifier", "t"],
    ["comment", "/* all\nrows */"],
    ["keyword", "LIMIT"],
    ["number", "1"],
    ["comment", "/* open"],
  ]);
});

test("operators of two characters stay together", () => {
  assert.deepEqual(kinds("a <= b || c <> d != e"), [
    ["identifier", "a"],
    ["operator", "<="],
    ["identifier", "b"],
    ["operator", "||"],
    ["identifier", "c"],
    ["operator", "<>"],
    ["identifier", "d"],
    ["operator", "!="],
    ["identifier", "e"],
  ]);
});

test("line breaks and indentation are kept as space tokens", () => {
  const tokens = tokenizeSql("SELECT\n  a");
  assert.deepEqual(tokens, [
    { kind: "keyword", text: "SELECT" },
    { kind: "space", text: "\n  " },
    { kind: "identifier", text: "a" },
  ]);
});

test("window-frame and CTE keywords are keywords", () => {
  const words = (sql) =>
    kinds(sql)
      .filter(([kind]) => kind === "keyword")
      .map(([, text]) => text);
  assert.deepEqual(words("ROWS BETWEEN 1 PRECEDING AND CURRENT ROW EXCLUDE NO OTHERS"), [
    "ROWS",
    "BETWEEN",
    "PRECEDING",
    "AND",
    "CURRENT",
    "ROW",
    "EXCLUDE",
    "NO",
    "OTHERS",
  ]);
  assert.deepEqual(words("GROUPS UNBOUNDED PRECEDING EXCLUDE TIES"), ["GROUPS", "UNBOUNDED", "PRECEDING", "EXCLUDE", "TIES"]);
  assert.deepEqual(words("WITH x AS NOT MATERIALIZED (SELECT 1)"), ["WITH", "AS", "NOT", "MATERIALIZED", "SELECT"]);
});

test("lines carry their indentation apart from their tokens, so wrapped text can hang below the first word", () => {
  const lines = sqlLines(tokenizeSql("SELECT\n  ROUND(SUM(x), 2) AS r\n\nFROM t"));
  assert.deepEqual(
    lines.map((line) => [line.indent, line.tokens.map((token) => token.text).join("")]),
    [
      [0, "SELECT"],
      [2, "ROUND(SUM(x), 2) AS r"],
      [0, ""],
      [0, "FROM t"],
    ],
  );
  assert.equal(lines[1].tokens[0].kind, "function");
});

test("a token that spans lines is split at the line breaks and keeps its kind", () => {
  const lines = sqlLines(tokenizeSql("SELECT 1 /* all\n   rows */ FROM t"));
  assert.deepEqual(
    lines.map((line) => [line.indent, line.tokens.map((token) => [token.kind, token.text])]),
    [
      [
        0,
        [
          ["keyword", "SELECT"],
          ["space", " "],
          ["number", "1"],
          ["space", " "],
          ["comment", "/* all"],
        ],
      ],
      [
        3,
        [
          ["comment", "rows */"],
          ["space", " "],
          ["keyword", "FROM"],
          ["space", " "],
          ["identifier", "t"],
        ],
      ],
    ],
  );
});

test("the lines put back together give the SQL unchanged", () => {
  const samples = ["SELECT\n  a,\n\tb\nFROM t", "SELECT 'x\n  y'\n", "", "  SELECT 1"];
  for (const sql of samples) {
    const text = sqlLines(tokenizeSql(sql))
      .map((line) => line.lead + line.tokens.map((token) => token.text).join(""))
      .join("\n");
    assert.equal(text, sql);
  }
});
