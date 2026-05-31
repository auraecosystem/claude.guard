/**
 * Fast-check property tests for sanitize-output.mjs (in-process; 500+ runs).
 */
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import fc from "fast-check";
import { unified } from "unified";
import rehypeParse from "rehype-parse";
import { visit, EXIT } from "unist-util-visit";

import {
  sanitizeHtml,
  detectAndNeutralizeExfil,
  isHiddenStyle,
  isHiddenOrDangerous,
  checkExfilUrl,
} from "./sanitize-output.mjs";

const N = 500;
const opts = { numRuns: N, verbose: false };

const applyHtml = async (t) => (await sanitizeHtml(t)) ?? t;
const applyExfil = (t) => detectAndNeutralizeExfil(t)?.text ?? t;

function hasForbidden(text) {
  const tree = unified().use(rehypeParse, { fragment: true }).parse(text);
  let found = false;
  visit(tree, (n) => {
    if (isHiddenOrDangerous(n)) {
      found = true;
      return EXIT;
    }
  });
  return found;
}

// ─── 1. Idempotence ──────────────────────────────────────────────────────────

describe("property: sanitizeHtml converges within 2 passes", () => {
  // remark-stringify re-escapes markdown-special chars adjacent to HTML on
  // the first re-pass (`~` → `\~`), then converges; assert the fixed point.
  it("third pass equals second", async () => {
    const tag = fc.constantFrom(
      "div",
      "span",
      "p",
      "script",
      "style",
      "a",
      "img",
      "iframe",
      "svg",
    );
    const attr = fc
      .tuple(
        fc.constantFrom("style", "hidden", "src", "href", "id"),
        fc.string({ maxLength: 30 }).map((s) => s.replace(/["<>&]/g, "")),
      )
      .map(([k, v]) => `${k}="${v}"`);
    const element = fc
      .tuple(
        tag,
        fc.array(attr, { maxLength: 3 }),
        fc.string({ maxLength: 40 }),
      )
      .map(
        ([t, a, i]) => `<${t}${a.length ? " " + a.join(" ") : ""}>${i}</${t}>`,
      );
    const arb = fc
      .array(fc.oneof(fc.string({ maxLength: 60 }), element), { maxLength: 6 })
      .map((xs) => xs.join(" "));

    await fc.assert(
      fc.asyncProperty(arb, async (input) => {
        const a = await applyHtml(input);
        const b = await applyHtml(a);
        const c = await applyHtml(b);
        assert.equal(c, b);
      }),
      opts,
    );
  });

  it("detectAndNeutralizeExfil is idempotent on randomized markdown links", () => {
    const payload = fc.oneof(
      fc.stringMatching(/^[A-Za-z0-9+/]{1,80}$/),
      fc.stringMatching(/^[A-Fa-f0-9]{1,80}$/),
      fc.string({ maxLength: 50 }),
    );
    const arb = fc
      .tuple(
        fc
          .string({ minLength: 1, maxLength: 10 })
          .filter((s) => !/[\]()[\\]/.test(s)),
        fc.constantFrom("https://evil.com", "https://example.org", ""),
        fc.constantFrom("/log", "/track", "/", "/api/v1/x"),
        fc.constantFrom("token", "data", "payload", "secret", "key", "q"),
        payload,
      )
      .map(([t, h, p, k, v]) => `pre [${t}](${h}${p}?${k}=${v}) post`);

    fc.assert(
      fc.property(arb, (input) => {
        const a = applyExfil(input);
        assert.equal(applyExfil(a), a);
      }),
      opts,
    );
  });
});

// ─── 2. Hidden-element fuzz ──────────────────────────────────────────────────

describe("property: hidden-style variants flagged by isHiddenStyle", () => {
  const ws = fc.constantFrom("", " ", "\t", "\n ");
  const bang = fc.constantFrom("", " !important", "!important", " ! Important");
  const cased = (s) =>
    fc.constantFrom(s, s.toUpperCase(), s[0].toUpperCase() + s.slice(1));
  const z = fc.constantFrom("0", "0.0", "0.00", "00", "0e0");
  const zLen = fc
    .tuple(z, fc.constantFrom("", "px", "em", "%", "pt", "rem"))
    .map(([n, u]) => n + u);
  const offscreen = fc
    .tuple(
      fc.integer({ min: 901, max: 99999 }),
      fc.constantFrom("px", "em", "pt"),
    )
    .map(([n, u]) => `-${n}${u}`);

  // Each entry: name → arbitrary producing a *hiding* declaration.
  // `wrap` adds noise (whitespace, !important, an unrelated extra decl) so
  // the detector must scan multiple props and tolerate adversarial spacing.
  const wrap = (decl) =>
    fc
      .tuple(
        ws,
        decl,
        bang,
        ws,
        fc.constantFrom("", "; color: red", "; margin: 1px"),
      )
      .map(([a, d, b, c, e]) => a + d + b + c + e);

  /** @type {Record<string, fc.Arbitrary<string>>} */
  const variants = {
    display: cased("display").map((k) => `${k}: none`),
    visibility: cased("visibility").map((k) => `${k}: hidden`),
    opacity: fc.tuple(cased("opacity"), z).map(([k, n]) => `${k}: ${n}`),
    "off-left": fc
      .tuple(cased("position"), cased("left"), offscreen)
      .map(([p, l, n]) => `${p}: absolute; ${l}: ${n}`),
    "off-top": fc
      .tuple(cased("position"), cased("top"), offscreen)
      .map(([p, t, n]) => `${p}: fixed; ${t}: ${n}`),
    clip: cased("position").map((p) => `${p}: absolute; clip: rect(0,0,0,0)`),
    "text-indent": fc
      .tuple(cased("text-indent"), offscreen)
      .map(([k, n]) => `${k}: ${n}`),
  };
  for (const dim of ["height", "width", "font-size"]) {
    variants[dim] = fc.tuple(cased(dim), zLen).map(([k, v]) => `${k}: ${v}`);
  }
  for (const dim of ["height", "max-width"]) {
    variants[`overflow+${dim}`] = fc
      .tuple(cased("overflow"), cased(dim), zLen)
      .map(([o, d, v]) => `${o}: hidden; ${d}: ${v}`);
  }

  for (const [name, decl] of Object.entries(variants)) {
    it(`flags ${name}`, () => {
      fc.assert(
        fc.property(wrap(decl), (s) =>
          assert.equal(
            isHiddenStyle(s),
            true,
            `not flagged: ${JSON.stringify(s)}`,
          ),
        ),
        opts,
      );
    });
  }
});

// ─── 3. URL exfil monotonicity ───────────────────────────────────────────────

describe("property: checkExfilUrl monotonic in payload length", () => {
  it("appending bytes never un-flags", () => {
    const seg = fc.stringMatching(/^[A-Za-z0-9+/]{0,80}$/);
    fc.assert(
      fc.property(
        fc.constantFrom("https://x.com/p", "/log", "http://a/b/c"),
        fc.constantFrom("q", "data", "token", "x"),
        seg,
        seg,
        (base, key, head, extra) => {
          const shortU = `${base}?${key}=${head}`;
          const longU = `${base}?${key}=${head}${extra}`;
          const sf = checkExfilUrl(shortU) !== null;
          const lf = checkExfilUrl(longU) !== null;
          assert.ok(!sf || lf, `mono: ${shortU} flagged but ${longU} not`);
        },
      ),
      opts,
    );
  });
});

// ─── 4. Round-trip: no forbidden node survives ──────────────────────────────

describe("property: sanitizeHtml round-trip drops all forbidden nodes", () => {
  it("script/style/comment/hidden never survives", async () => {
    const evilStyle = fc.constantFrom(
      "display:none",
      "visibility:hidden",
      "opacity:0",
      "position:absolute;left:-9999px",
      "position:fixed;top:-10000px",
      "clip:rect(0,0,0,0);position:absolute",
      "text-indent:-9999px",
      "height:0",
      "overflow:hidden;max-width:0",
      "font-size:0",
    );
    const evil = fc.oneof(
      fc.constant("<script>alert(1)</script>"),
      fc.constant("<style>body{}</style>"),
      fc.constant("<!-- secret -->"),
      fc.constant("<div hidden>x</div>"),
      fc.constant(`<img src="data:text/html,<script>x</script>">`),
      evilStyle.map((s) => `<div style="${s}">h</div>`),
      evilStyle.map((s) => `<span style='${s}'>x</span>`),
    );
    const benign = fc.constantFrom("hello", "<p>v</p>", "<b>b</b>", "", "\n");
    const arb = fc
      .array(fc.oneof(benign, evil), { minLength: 1, maxLength: 8 })
      .map((xs) => xs.join("\n"));

    await fc.assert(
      fc.asyncProperty(arb, async (input) => {
        const out = await applyHtml(input);
        assert.equal(
          hasForbidden(out),
          false,
          `survived: ${JSON.stringify(out)}`,
        );
      }),
      opts,
    );
  });
});
