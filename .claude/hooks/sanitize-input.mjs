#!/usr/bin/env node
import { detect } from "out-of-character";
import stripAnsi from "strip-ansi";

// Unicode-category-based detection: auto-updates with Node's ICU data.
// No manual codepoint lists needed for these categories.
const CATEGORY_CHECKS = [
  ["Format character (Cf)", /\p{Cf}/gu],
  ["Non-ASCII space (Zs)", /(?![\u0020])\p{Zs}/gu],
  ["Line separator (Zl)", /\p{Zl}/gu],
  ["Paragraph separator (Zp)", /\p{Zp}/gu],
];

// Non-Cf invisible chars that \p{Cf} does not cover.
// Variation selectors are Mn; Hangul fillers are Lo; Braille blank and FFFC are So.
function re(cps, flags) {
  return new RegExp("[" + cps.map(c => String.fromCodePoint(c)).join("") + "]", flags);
}
const EXTRA_CHECKS = [
  ["Variation selector", re([
    ...Array.from({ length: 16 }, (_, i) => 0xFE00 + i),
    ...Array.from({ length: 240 }, (_, i) => 0xE0100 + i),
  ], "gu")],
  ["Blank-rendering char", re([0x2800, 0x3164, 0x115F, 0x1160, 0xFFA0, 0x17B4, 0x17B5], "gu")],
  ["Object replacement", re([0xFFFC], "g")],
];

const ALL_CHECKS = [...CATEGORY_CHECKS, ...EXTRA_CHECKS];

function findSuspiciousChars(text) {
  if (typeof text !== "string" || text.length === 0) return [];
  const findings = [];

  const ooc = detect(text);
  if (ooc) {
    for (const hit of ooc)
      findings.push(hit.name + " (" + hit.code + ") at offset " + hit.offset);
  }

  if (stripAnsi(text).length !== text.length)
    findings.push("ANSI escape sequences detected");

  for (const [label, regex] of ALL_CHECKS) {
    for (const m of text.matchAll(regex)) {
      const cp = m[0].codePointAt(0);
      const hex = "U+" + cp.toString(16).toUpperCase().padStart(cp > 0xFFFF ? 5 : 4, "0");
      findings.push(label + " " + hex + " at offset " + m.index);
    }
  }

  return findings;
}

function extractStrings(obj, prefix) {
  if (typeof obj === "string") return [{ field: prefix, value: obj }];
  if (Array.isArray(obj))
    return obj.flatMap((v, i) => extractStrings(v, prefix + "[" + i + "]"));
  if (obj && typeof obj === "object")
    return Object.entries(obj).flatMap(([k, v]) =>
      extractStrings(v, prefix ? prefix + "." + k : k));
  return [];
}

const FIELD_MAP = {
  Bash: ["command", "description"],
  Edit: ["file_path", "old_string", "new_string"],
  Write: ["file_path", "content"],
  Read: ["file_path"],
};

function getTextFields(toolName, toolInput) {
  const keys = FIELD_MAP[toolName];
  if (keys)
    return keys.filter(k => typeof toolInput[k] === "string")
      .map(k => ({ field: k, value: toolInput[k] }));
  return extractStrings(toolInput, "");
}

async function main() {
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  const raw = Buffer.concat(chunks).toString();
  if (!raw) process.exit(0);

  let input;
  try { input = JSON.parse(raw); } catch { process.exit(0); }

  const { tool_name: toolName, tool_input: toolInput } = input;
  if (!toolName || !toolInput) process.exit(0);

  const allFindings = [];
  for (const { field, value } of getTextFields(toolName, toolInput)) {
    const findings = findSuspiciousChars(value);
    if (findings.length > 0) allFindings.push({ field, findings });
  }

  if (allFindings.length === 0) process.exit(0);

  const summary = allFindings
    .map(({ field, findings }) => "  " + field + ": " + findings.join("; "))
    .join("\n");

  process.stdout.write(JSON.stringify({
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: "deny",
      permissionDecisionReason:
        "Blocked: invisible/suspicious Unicode in " + toolName + " input.\n" +
        summary + "\nThis may indicate prompt injection. Review the source.",
    },
  }));
}

main().catch(() => process.exit(0));