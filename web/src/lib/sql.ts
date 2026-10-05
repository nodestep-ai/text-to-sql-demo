export type SqlTokenKind =
  | "keyword"
  | "function"
  | "identifier"
  | "string"
  | "number"
  | "comment"
  | "operator"
  | "punctuation"
  | "space";

export interface SqlToken {
  kind: SqlTokenKind;
  text: string;
}

export interface SqlLine {
  lead: string;
  indent: number;
  tokens: SqlToken[];
}

const TAB_WIDTH = 2;

const TOKEN_CLASSES: Record<SqlTokenKind, string | null> = {
  keyword: "nodestep-token-keyword",
  function: "nodestep-token-function",
  identifier: "nodestep-token-identifier",
  string: "nodestep-token-string",
  number: "nodestep-token-number",
  comment: "nodestep-token-comment",
  operator: "nodestep-token-operator",
  punctuation: "nodestep-token-operator",
  space: null,
};

export function tokenClass(kind: SqlTokenKind): string | null {
  return TOKEN_CLASSES[kind];
}

const KEYWORDS = new Set(
  `ALL AND AS ASC BETWEEN BY CASE CAST COLLATE CROSS CURRENT CURRENT_DATE CURRENT_TIME CURRENT_TIMESTAMP DESC DISTINCT
  ELSE END ESCAPE EXCEPT EXCLUDE EXISTS FALSE FILTER FIRST FOLLOWING FROM FULL GLOB GROUP GROUPS HAVING IN INNER
  INTERSECT IS ISNULL JOIN LAST LEFT LIKE LIMIT MATCH MATERIALIZED NATURAL NO NOT NOTNULL NULL NULLS OFFSET ON OR ORDER
  OTHERS OUTER OVER PARTITION PRECEDING RANGE RECURSIVE REGEXP RIGHT ROW ROWS SELECT THEN TIES TRUE UNBOUNDED UNION
  USING VALUES WHEN WHERE WINDOW WITH`.split(/\s+/),
);

const BARE_NAME = /^[\p{L}_][\p{L}\p{N}_$]*$/u;

const PATTERNS: [SqlTokenKind, RegExp][] = [
  ["space", /\s+/y],
  ["comment", /--[^\n]*/y],
  ["comment", /\/\*[\s\S]*?(?:\*\/|$)/y],
  ["string", /[xX]'[^']*(?:'|$)/y],
  ["string", /'(?:[^']|'')*(?:'|$)/y],
  ["identifier", /"(?:[^"]|"")*(?:"|$)/y],
  ["identifier", /`(?:[^`]|``)*(?:`|$)/y],
  ["identifier", /\[[^\]]*(?:\]|$)/y],
  ["number", /0[xX][0-9a-fA-F]+|(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?/y],
  ["identifier", /[\p{L}_][\p{L}\p{N}_$]*/uy],
  ["operator", /\|\||<<|>>|<=|>=|<>|!=|==|[-+*/%<>=~&|]/y],
];

export function tokenizeSql(sql: string): SqlToken[] {
  const tokens: SqlToken[] = [];
  let position = 0;
  while (position < sql.length) {
    const token = readToken(sql, position);
    position += token.text.length;
    if (token.kind === "identifier" && sql[position] === "(" && BARE_NAME.test(token.text)) token.kind = "function";
    tokens.push(token);
  }
  return tokens;
}

export function sqlLines(tokens: SqlToken[]): SqlLine[] {
  const lines: SqlToken[][] = [[]];
  for (const token of tokens) {
    token.text.split("\n").forEach((piece, index) => {
      if (index > 0) lines.push([]);
      if (piece !== "") lines.at(-1)?.push({ kind: token.kind, text: piece });
    });
  }
  return lines.map(splitLead);
}

function splitLead(tokens: SqlToken[]): SqlLine {
  const first = tokens[0];
  const lead = first?.text.match(/^[ \t]*/)?.[0] ?? "";
  if (lead === "") return { lead, indent: 0, tokens };
  const indent = [...lead].reduce((width, character) => width + (character === "\t" ? TAB_WIDTH : 1), 0);
  const rest = first.text.slice(lead.length);
  return { lead, indent, tokens: rest === "" ? tokens.slice(1) : [{ kind: first.kind, text: rest }, ...tokens.slice(1)] };
}

function readToken(sql: string, position: number): SqlToken {
  for (const [kind, pattern] of PATTERNS) {
    pattern.lastIndex = position;
    const match = pattern.exec(sql);
    if (match === null || match[0] === "") continue;
    const text = match[0];
    if (kind === "identifier" && KEYWORDS.has(text.toUpperCase())) return { kind: "keyword", text };
    return { kind, text };
  }
  return { kind: "punctuation", text: sql[position] };
}
