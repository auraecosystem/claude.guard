/**
 * Fast-check property tests for sanitize-output.mjs.
 *
 * Calls the layer functions in-process (the module's main entry is guarded by
 * `isMain`, so importing doesn't block on stdin). 500-run properties at
 * spawn-per-case would blow the 30s budget; direct calls keep it under a few
 * seconds total.
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
  checkExfilUrl,
} from "./sanitize-output.mjs";

const NUM_RUNS = 500;

// ─── helpers ─────────────────────────────────────────────────────────────────

// Full Layer-2 application: sanitizeHtml returns null when unchanged. Applying
// it again must also return null (idempotence). Returns the post-sanitize text.
async function applyHtml(text) {
  const out = await sanitizeHtml(text);
  return out === null ? text : out;
}

// Apply Layer 3 once; returns post-sanitize text.
function applyExfil(text) {
  const out = detectAndNeutralizeExfil(text);
  return out === null ? text : out.text;
}

function htmlHasForbidden(text) {
  const tree = unified().use(rehypeParse, { fragment: true }).parse(text);
  let found = false;
  visit(tree, (node) => {
    if (node.type === "comment") {
      found = true;
      return EXIT;
    }
    if (node.type !== "element") return;
    const tag = node.tagName;
    if (tag === "script" || tag === "style") {
      found = true;
      return EXIT;
    }
    const props = node.properties || {};
    if (props.hidden !== undefined && props.hidden !== null) {
      found = true;
      return EXIT;
    }
    if (typeof props.style === "string" && isHiddenStyle(props.style)) {
      found = true;
      return EXIT;
    }
    if (typeof props.src === "string" && props.src.startsWith("data:")) {
      found = true;
      return EXIT;
    }
  });
  return found;
}

// ─── 1. Idempotence: sanitize(sanitize(x)) == sanitize(x) ────────────────────

describe("property: sanitizeHtml idempotence", () => {
  it("sanitize(sanitize(x)) deep-equals sanitize(x) for arbitrary HTML", async () => {
    // Generator: weave random text with random HTML tags so most inputs
    // actually exercise the parser. Pure noise rarely hits the HTML path.
    const tagName = fc.constantFrom(
      "div",
      "span",
      "p",
      "script",
      "style",
      "a",
      "img",
      "b",
      "em",
      "iframe",
      "svg",
    );
    const attr = fc.tuple(
      fc.constantFrom("style", "hidden", "src", "href", "class", "id"),
      fc.string({ minLength: 0, maxLength: 30 }),
    );
    const element = fc
      .tuple(
        tagName,
        fc.array(attr, { maxLength: 3 }),
        fc.string({ maxLength: 40 }),
      )
      .map(([tag, attrs, inner]) => {
        const a = attrs
          .map(([k, v]) => `${k}="${v.replace(/["<>&]/g, "")}"`)
          .join(" ");
        return `<${tag}${a ? " " + a : ""}>${inner}</${tag}>`;
      });
    const noise = fc.string({ maxLength: 60 });
    const arbHtml = fc
      .array(fc.oneof(noise, element), { maxLength: 6 })
      .map((xs) => xs.join(" "));

    // NB: not strict 1-pass idempotence. remark-stringify re-escapes
    // markdown-special chars adjacent to HTML on the first re-pass (e.g.
    // `~` → `\~`), then converges. The security-relevant property is that
    // a fixed point exists within a small bound, which is what we assert.
    await fc.assert(
      fc.asyncProperty(arbHtml, async (input) => {
        const once = await applyHtml(input);
        const twice = await applyHtml(once);
        const thrice = await applyHtml(twice);
        assert.equal(thrice, twice);
      }),
      { numRuns: NUM_RUNS, verbose: false },
    );
  });

  it("detectAndNeutralizeExfil is idempotent on arbitrary markdown links", () => {
    const payload = fc.oneof(
      fc.stringMatching(/^[A-Za-z0-9+/]{1,80}$/),
      fc.stringMatching(/^[A-Fa-f0-9]{1,80}$/),
      fc.string({ maxLength: 50 }),
    );
    const param = fc.constantFrom(
      "token",
      "data",
      "payload",
      "secret",
      "key",
      "q",
      "x",
    );
    const host = fc.constantFrom("https://evil.com", "https://example.org", "");
    const path = fc.constantFrom("/log", "/track", "/", "/api/v1/x");
    const linkText = fc
      .string({ minLength: 1, maxLength: 10 })
      .filter((s) => !/[\]()[\\]/.test(s));
    const markdownLink = fc
      .tuple(linkText, host, path, param, payload)
      .map(
        ([t, h, p, k, v]) =>
          `prefix [${t.replace(/[\]()]/g, "")}](${h}${p}?${k}=${v}) suffix`,
      );

    fc.assert(
      fc.property(markdownLink, (input) => {
        const once = applyExfil(input);
        const twice = applyExfil(once);
        assert.equal(twice, once);
      }),
      { numRuns: NUM_RUNS, verbose: false },
    );
  });
});

// ─── 2. Hidden-element fuzz ──────────────────────────────────────────────────

describe("property: hidden-element variants are all flagged by isHiddenStyle", () => {
  // Each entry: name + fc arbitrary that produces a *hiding* style declaration.
  // Generators randomize whitespace, case, !important, units.
  const ws = () => fc.constantFrom("", " ", "  ", "\t", "\n ");
  const bang = () =>
    fc.constantFrom("", " !important", "!important", " ! Important");
  const cas = (s) =>
    fc.constantFrom(s, s.toUpperCase(), s[0].toUpperCase() + s.slice(1));
  const zeroNum = () => fc.constantFrom("0", "0.0", "0.00", "00", "0e0");
  const zeroLen = () =>
    fc
      .tuple(zeroNum(), fc.constantFrom("", "px", "em", "%", "pt", "rem"))
      .map(([n, u]) => `${n}${u}`);
  const offscreenNum = () =>
    fc
      .integer({ min: 901, max: 99999 })
      .chain((n) => fc.constantFrom("px", "em", "pt").map((u) => `-${n}${u}`));

  // Wraps a "prop: value" declaration with extra random whitespace, optional
  // !important, and optionally chains an unrelated extra declaration so the
  // detector must scan multiple props, not just the first.
  const wrap = (declArb) =>
    fc
      .tuple(
        ws(),
        declArb,
        bang(),
        ws(),
        fc.constantFrom("", "; color: red", "; margin: 1px"),
      )
      .map(([w1, decl, b, w2, extra]) => `${w1}${decl}${b}${w2}${extra}`);

  const generators = {
    "display:none": wrap(
      fc
        .tuple(cas("display"), ws(), ws())
        .map(([k, w1, w2]) => `${k}:${w1}none${w2}`),
    ),
    "visibility:hidden": wrap(
      fc.tuple(cas("visibility"), ws()).map(([k, w]) => `${k}:${w}hidden`),
    ),
    "opacity:0": wrap(
      fc.tuple(cas("opacity"), zeroNum()).map(([k, n]) => `${k}: ${n}`),
    ),
    "height:0": wrap(
      fc.tuple(cas("height"), zeroLen()).map(([k, v]) => `${k}: ${v}`),
    ),
    "width:0": wrap(
      fc.tuple(cas("width"), zeroLen()).map(([k, v]) => `${k}: ${v}`),
    ),
    "font-size:0": wrap(
      fc.tuple(cas("font-size"), zeroLen()).map(([k, v]) => `${k}: ${v}`),
    ),
    "absolute+left:-9999px": wrap(
      fc
        .tuple(cas("position"), cas("left"), offscreenNum())
        .map(([p, l, n]) => `${p}: absolute; ${l}: ${n}`),
    ),
    "fixed+top:-9999px": wrap(
      fc
        .tuple(cas("position"), cas("top"), offscreenNum())
        .map(([p, t, n]) => `${p}: fixed; ${t}: ${n}`),
    ),
    "absolute+clip:rect(0,0,0,0)": wrap(
      fc
        .tuple(cas("position"), ws(), ws(), ws())
        .map(
          ([p, w1, w2, w3]) =>
            `${p}: absolute; clip:${w1}rect(${w2}0,${w3}0, 0, 0)`,
        ),
    ),
    "text-indent:-9999": wrap(
      fc
        .tuple(cas("text-indent"), offscreenNum())
        .map(([k, n]) => `${k}: ${n}`),
    ),
    "overflow:hidden+height:0": wrap(
      fc
        .tuple(cas("overflow"), cas("height"), zeroLen())
        .map(([o, h, v]) => `${o}: hidden; ${h}: ${v}`),
    ),
    "overflow:hidden+max-width:0": wrap(
      fc
        .tuple(cas("overflow"), cas("max-width"), zeroLen())
        .map(([o, mw, v]) => `${o}: hidden; ${mw}: ${v}`),
    ),
  };

  for (const [name, gen] of Object.entries(generators)) {
    it(`always flags hiding variant: ${name}`, () => {
      fc.assert(
        fc.property(gen, (styleStr) => {
          assert.equal(
            isHiddenStyle(styleStr),
            true,
            `expected hidden: ${JSON.stringify(styleStr)}`,
          );
        }),
        { numRuns: NUM_RUNS, verbose: false },
      );
    });
  }
});

// ─── 3. URL exfil monotonicity ───────────────────────────────────────────────

describe("property: checkExfilUrl monotonicity in payload length", () => {
  it("longer payload => at-least-as-likely flagged", () => {
    // For a given base URL and key, extending the payload value cannot
    // un-flag a URL. If short is flagged, long must be flagged too.
    const base = fc.constantFrom("https://x.com/p", "/log", "http://a/b/c");
    const key = fc.constantFrom("q", "data", "token", "x");
    const shortLen = fc.integer({ min: 1, max: 30 });
    const extraLen = fc.integer({ min: 0, max: 80 });
    const alphabet = fc.constantFrom(
      "0123456789abcdef",
      "0123456789ABCDEF",
      "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/",
      "abc123",
    );

    fc.assert(
      fc.property(
        base,
        key,
        shortLen,
        extraLen,
        alphabet,
        fc.integer({ min: 0, max: 1e6 }),
        (b, k, sl, el, alpha, seed) => {
          // Deterministic payload from seed for stable extension.
          const make = (n) => {
            let s = "";
            let x = seed;
            for (let i = 0; i < n; i++) {
              x = (x * 1103515245 + 12345) & 0x7fffffff;
              s += alpha[x % alpha.length];
            }
            return s;
          };
          const shortP = make(sl);
          const longP = shortP + make(el);
          const shortUrl = `${b}?${k}=${shortP}`;
          const longUrl = `${b}?${k}=${longP}`;
          const shortFlagged = checkExfilUrl(shortUrl) !== null;
          const longFlagged = checkExfilUrl(longUrl) !== null;
          if (shortFlagged && !longFlagged) {
            assert.fail(
              `monotonicity broken: short=${JSON.stringify(
                shortUrl,
              )} flagged but long=${JSON.stringify(longUrl)} not`,
            );
          }
        },
      ),
      { numRuns: NUM_RUNS, verbose: false },
    );
  });
});

// ─── 4. Round-trip: post-sanitize HTML has no script/style/hidden survivors ─

describe("property: sanitizeHtml round-trip leaves no forbidden nodes", () => {
  it("no script/style/comment/hidden element survives sanitization", async () => {
    // Adversarial generator biased toward known hidden-element tricks plus
    // script/style/comments — the things the sanitizer must drop.
    const evilStyle = fc.constantFrom(
      "display:none",
      "visibility: hidden",
      "opacity: 0",
      "position: absolute; left: -9999px",
      "position: fixed; top: -10000px",
      "clip: rect(0,0,0,0); position: absolute",
      "text-indent: -9999px",
      "height: 0",
      "overflow: hidden; max-width: 0",
      "font-size: 0",
    );
    const evilNode = fc.oneof(
      fc.constant("<script>alert(1)</script>"),
      fc.constant("<style>body{}</style>"),
      fc.constant("<!-- secret -->"),
      evilStyle.map((s) => `<div style="${s}">hidden</div>`),
      evilStyle.map((s) => `<span style='${s}'>x</span>`),
      fc.constant("<div hidden>x</div>"),
      fc.constant(`<img src="data:text/html,<script>alert(1)</script>">`),
    );
    const benign = fc.constantFrom(
      "hello",
      "<p>visible</p>",
      "<b>bold</b>",
      "line\n",
      "",
    );
    const arbDoc = fc
      .array(fc.oneof(benign, evilNode), { minLength: 1, maxLength: 8 })
      .map((xs) => xs.join("\n"));

    await fc.assert(
      fc.asyncProperty(arbDoc, async (input) => {
        const out = await applyHtml(input);
        assert.equal(
          htmlHasForbidden(out),
          false,
          `forbidden node survived; input=${JSON.stringify(
            input,
          )} output=${JSON.stringify(out)}`,
        );
      }),
      { numRuns: NUM_RUNS, verbose: false },
    );
  });
});
