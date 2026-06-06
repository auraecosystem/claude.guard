// Pins the JS quality-gate scopes to a single source of truth (check-globs.mjs).
//
// c8, tsc, and ESLint must cover the same `.mjs` source set. ESLint imports
// SOURCE_GLOBS directly, but .c8rc.json and tsconfig.json are JSON and can't —
// so this test fails the moment their `include` arrays drift from SOURCE_GLOBS.
// That drift is exactly what let bin/lib/github-app sit coverage-gated while
// escaping typecheck and lint.

import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { SOURCE_GLOBS } from "../check-globs.mjs";
import eslintConfig from "../eslint.config.js";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const readJson = (rel) => JSON.parse(readFileSync(join(root, rel), "utf8"));

describe("JS gate scopes share one source of truth", () => {
  it("c8 coverage include == SOURCE_GLOBS", () => {
    assert.deepEqual(readJson(".c8rc.json").include, SOURCE_GLOBS);
  });

  it("tsc include == SOURCE_GLOBS", () => {
    assert.deepEqual(readJson("tsconfig.json").include, SOURCE_GLOBS);
  });

  it("ESLint consumes the SOURCE_GLOBS array by reference", () => {
    // eslint.config.js imports SOURCE_GLOBS and uses it as a block's `files`, so
    // reference identity proves it can't have a divergent hand-copied list.
    assert.ok(
      eslintConfig.some((block) => block.files === SOURCE_GLOBS),
      "no ESLint config block uses the shared SOURCE_GLOBS array",
    );
  });
});
