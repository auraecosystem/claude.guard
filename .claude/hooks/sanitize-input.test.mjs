import { describe, it } from "node:test";
import { spawn } from "node:child_process";
import assert from "node:assert/strict";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const INPUT_HOOK = join(__dirname, "sanitize-input.mjs");
const OUTPUT_HOOK = join(__dirname, "sanitize-output.mjs");

function run(hook, input) {
  return new Promise((resolve, reject) => {
    const child = spawn("node", [hook], { stdio: ["pipe", "pipe", "pipe"] });
    const chunks = [];
    child.stdout.on("data", (d) => chunks.push(d));
    child.on("error", reject);
    child.on("close", () => {
      const out = Buffer.concat(chunks).toString().trim();
      resolve(out ? JSON.parse(out) : null);
    });
    child.stdin.end(JSON.stringify(input));
  });
}

const pre = (tool, input) => run(INPUT_HOOK, { tool_name: tool, tool_input: input });
const post = (tool, text) =>
  run(OUTPUT_HOOK, { tool_name: tool, tool_input: {}, tool_result: { type: "text", text } });
const h = (r) => r?.hookSpecificOutput;

// -- PreToolUse: confusable normalization --

describe("sanitize-input (PreToolUse)", () => {
  it("passes clean input", async () => {
    assert.equal(await pre("Bash", { command: "ls -la" }), null);
  });

  it("normalizes Cyrillic in file path", async () => {
    const r = h(await pre("Read", {
      file_path: "/etc/p" + String.fromCodePoint(0x0430) + "sswd",
    }));
    assert.equal(r.updatedInput.file_path, "/etc/passwd");
    assert.match(r.additionalContext, /homoglyph/i);
  });

  it("normalizes confusables in Bash command", async () => {
    const r = h(await pre("Bash", {
      command: "c" + String.fromCodePoint(0x0430) + "t /tmp/x",
    }));
    assert.equal(r.updatedInput.command, "cat /tmp/x");
  });

  it("does NOT normalize Write content", async () => {
    assert.equal(await pre("Write", {
      file_path: "/tmp/test.txt",
      content: "text with " + String.fromCodePoint(0x0430) + " char",
    }), null);
  });

  it("does NOT normalize Edit old_string/new_string", async () => {
    assert.equal(await pre("Edit", {
      file_path: "/tmp/test.txt",
      old_string: "hello",
      new_string: "h" + String.fromCodePoint(0x0430) + "llo",
    }), null);
  });
});

// -- PostToolUse: payload-capable character stripping --

describe("sanitize-output (PostToolUse)", () => {
  it("passes clean output", async () => {
    assert.equal(await post("Read", "clean output"), null);
  });

  // -- Should strip: payload-capable chars --

  it("strips zero-width space (Cf)", async () => {
    const r = h(await post("Read", "hello" + String.fromCodePoint(0x200B) + "world"));
    assert.equal(r.updatedToolOutput, "helloworld");
    assert.match(r.additionalContext, /Format chars/);
  });

  it("strips bidi override (Cf)", async () => {
    const r = h(await post("Read", "text" + String.fromCodePoint(0x202E) + "hidden"));
    assert.equal(r.updatedToolOutput, "texthidden");
  });

  it("strips Unicode tag characters (Cf)", async () => {
    const tags = String.fromCodePoint(0xE0001) + String.fromCodePoint(0xE0065);
    const r = h(await post("Read", "echo " + tags + "hello"));
    assert.equal(r.updatedToolOutput, "echo hello");
  });

  it("strips variation selectors", async () => {
    const r = h(await post("Read", "test" + String.fromCodePoint(0xFE0F) + "data"));
    assert.equal(r.updatedToolOutput, "testdata");
    assert.match(r.additionalContext, /Variation/);
  });

  it("strips ANSI escapes", async () => {
    const r = h(await post("Bash", "\x1b[32mfile.txt\x1b[0m"));
    assert.equal(r.updatedToolOutput, "file.txt");
    assert.match(r.additionalContext, /ANSI/);
  });

  // -- Should NOT strip: legitimate formatting --

  it("preserves NBSP (U+00A0)", async () => {
    assert.equal(await post("Read", "hello world"), null);
  });

  it("preserves NNBSP (U+202F)", async () => {
    assert.equal(await post("Read", "hello world"), null);
  });

  it("preserves soft hyphen (U+00AD)", async () => {
    assert.equal(await post("Read", "mal­ware"), null);
  });

  it("preserves BOM (U+FEFF)", async () => {
    assert.equal(await post("Read", "﻿hello"), null);
  });

  it("preserves ideographic space (U+3000)", async () => {
    assert.equal(await post("Read", "echo　hello"), null);
  });

  // -- Run-length detection --

  it("short run: no semantic injection warning", async () => {
    const r = h(await post("Read", "x" + String.fromCodePoint(0x200B) + "y"));
    assert.doesNotMatch(r.additionalContext, /injection payload/);
  });

  it("long run (10+): warns about semantic injection", async () => {
    const payload = Array.from({ length: 15 },
      (_, i) => String.fromCodePoint(0xE0041 + i)).join("");
    const r = h(await post("Read", "normal " + payload + " text"));
    assert.match(r.additionalContext, /injection payload/);
    assert.equal(r.updatedToolOutput, "normal  text");
  });

  // -- Edge cases --

  it("handles empty/malformed input", async () => {
    assert.equal(await run(OUTPUT_HOOK, {}), null);
  });

  it("100KB clean output: no action", async () => {
    assert.equal(await post("Bash", "x".repeat(100000)), null);
  });
});
