// Helpers for turning pasted or uploaded text into a list of records.

// Splits CSV text into rows of cells, handling quoted cells, escaped quotes and line breaks inside quotes.
function parseCsvRows(text) {
  const rows = [];
  let row = [];
  let cell = "";
  let inQuotes = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (inQuotes) {
      if (ch === '"' && text[i + 1] === '"') {
        cell += '"';
        i++;
      } else if (ch === '"') {
        inQuotes = false;
      } else {
        cell += ch;
      }
    } else if (ch === '"') {
      inQuotes = true;
    } else if (ch === ",") {
      row.push(cell);
      cell = "";
    } else if (ch === "\n" || ch === "\r") {
      if (ch === "\r" && text[i + 1] === "\n") i++;
      row.push(cell);
      cell = "";
      rows.push(row);
      row = [];
    } else {
      cell += ch;
    }
  }
  if (cell !== "" || row.length) {
    row.push(cell);
    rows.push(row);
  }
  return rows.filter((r) => r.some((c) => c.trim() !== ""));
}

// Parses pasted/uploaded text as a JSON array of objects, or as CSV with a header row.
// Returns { records } on success or { error } with a plain message.
export function parseRecords(text) {
  const trimmed = text.replace(/^﻿/, "").trim();
  if (!trimmed) return { records: [] };

  if (trimmed.startsWith("[") || trimmed.startsWith("{")) {
    try {
      const parsed = JSON.parse(trimmed);
      const list = Array.isArray(parsed) ? parsed : [parsed];
      if (!list.every((r) => r && typeof r === "object" && !Array.isArray(r))) {
        return { error: "Each record in the JSON must be an object like {\"id\": 1}." };
      }
      return { records: list };
    } catch (e) {
      return { error: `That is not valid JSON: ${e.message}` };
    }
  }

  const rows = parseCsvRows(trimmed);
  if (rows.length < 2) return { error: "CSV needs a header row and at least one data row." };
  const headers = rows[0].map((h) => h.trim());
  if (headers.some((h) => !h)) return { error: "Every CSV column needs a header name." };
  if (new Set(headers).size !== headers.length) return { error: "CSV column names must be unique." };
  const records = rows.slice(1).map((cells) => Object.fromEntries(headers.map((h, i) => [h, (cells[i] ?? "").trim()])));
  return { records };
}

// Lists every field name used across the records, in first-seen order.
export function fieldNames(records) {
  const seen = new Set();
  for (const r of records) Object.keys(r).forEach((k) => seen.add(k));
  return [...seen];
}

// Returns the names of fields whose value is present and different in every record (candidates for the match key).
export function uniqueFields(records) {
  return fieldNames(records).filter((f) => {
    const values = records.map((r) => r[f]);
    return values.every((v) => v !== undefined && v !== null && v !== "") && new Set(values.map(String)).size === records.length;
  });
}

// Picks the field that identifies each record: the first column whose values are all present and different,
// preferring an "id" style name. Returns "" when none qualifies (rows are then numbered).
export function suggestMatchKey(records) {
  const unique = uniqueFields(records);
  const preferred = ["record_id", "id", "email", "order_id", "sku", "code"];
  return preferred.find((p) => unique.includes(p)) ?? unique[0] ?? "";
}

// Finds the website address in the task description: the first link, or a bare localhost / www. address.
// Returns a full URL string, or null when there is none.
export function findUrl(text) {
  const tail = String.raw`[^\s<>"')\]]*`;
  const withScheme = text.match(new RegExp(String.raw`https?://` + tail, "i"));
  const local = text.match(new RegExp(String.raw`\b(?:localhost|127\.0\.0\.1)(?::\d+)?(?:/` + tail + ")?", "i"));
  const www = text.match(new RegExp(String.raw`\bwww\.[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,}(?:/` + tail + ")?", "i"));
  // The earliest address in the text wins, whichever form it is written in
  const first = [withScheme, local, www].filter(Boolean).sort((a, b) => a.index - b.index)[0];
  const raw = first?.[0];
  if (!raw) return null;
  const url = raw.replace(/[.,;:!?]+$/, "");
  if (/^https?:\/\//i.test(url)) return url;
  return (/^www\./i.test(url) ? "https://" : "http://") + url;
}

// Makes a short task name from the description: the website plus the first few words of what to do,
// for example "localhost:5174 · credit desk". Falls back to whichever of the two exists.
export function autoName(text) {
  const firstLine = text.split("\n").map((l) => l.trim()).find(Boolean) ?? "";
  let host = "";
  try {
    const url = findUrl(text);
    if (url) host = new URL(url).host.replace(/^www\./, "");
  } catch {
    /* no usable address yet */
  }
  const clause = firstLine
    .replace(/https?:\/\/\S+/gi, "")
    .replace(/^(go to|open|visit)\b[\s.,:;-]*/i, "")
    .replace(/^[\s.,:;-]+/, "")
    .split(/[.:;]|, /)[0]
    .replace(/^you\s+(are|work\s+(at|in|as|for))\s+(an?|the)?\s*/i, "")
    .replace(/\s+/g, " ")
    .trim();
  const short = clause.length > 30 ? clause.slice(0, 29).trimEnd() + "…" : clause;
  if (host && short) return `${host} · ${short}`;
  return host || short || "Untitled task";
}
