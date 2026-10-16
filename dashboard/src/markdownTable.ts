// Parses a pipe-syntax markdown table, e.g. the numerical diff table of a science report:
//
//   | case | quantity | max abs dev | result |
//   |---|---|---:|---|
//   | raman-532 | wavelength | 0.0124 | FAIL |
//
// Cells may contain escaped pipes (\|). Anything that is not a well-formed table gives null, and
// the caller shows the text as it is.

export type Align = "left" | "right" | "center" | null;

export interface MarkdownTable {
  header: string[];
  align: Align[];
  rows: string[][];
}

function splitRow(line: string): string[] | null {
  const text = line.trim();
  if (!text.includes("|")) return null;
  const cells: string[] = [];
  let cell = "";
  for (let i = 0; i < text.length; i += 1) {
    const char = text[i];
    if (char === "\\" && text[i + 1] === "|") {
      cell += "|";
      i += 1;
    } else if (char === "|") {
      cells.push(cell);
      cell = "";
    } else {
      cell += char;
    }
  }
  cells.push(cell);
  // A leading pipe leaves an empty first cell, an unescaped trailing pipe an empty last one.
  if (text.startsWith("|")) cells.shift();
  if (text.endsWith("|") && !text.endsWith("\\|")) cells.pop();
  return cells.map((value) => value.trim());
}

function alignment(cell: string): Align | undefined {
  const spec = cell.replace(/\s+/g, "");
  if (!/^:?-+:?$/.test(spec)) return undefined;
  const left = spec.startsWith(":");
  const right = spec.endsWith(":");
  if (left && right) return "center";
  if (right) return "right";
  if (left) return "left";
  return null;
}

export function parseMarkdownTable(text: string): MarkdownTable | null {
  const lines = text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line.length > 0);
  if (lines.length < 2) return null;
  const header = splitRow(lines[0]);
  const separator = splitRow(lines[1]);
  if (!header || !separator || header.length === 0 || separator.length !== header.length) return null;
  const align: Align[] = [];
  for (const cell of separator) {
    const value = alignment(cell);
    if (value === undefined) return null;
    align.push(value);
  }
  const rows: string[][] = [];
  for (const line of lines.slice(2)) {
    const cells = splitRow(line);
    if (!cells) return null;
    const row = cells.slice(0, header.length);
    while (row.length < header.length) row.push("");
    rows.push(row);
  }
  return { header, align, rows };
}
