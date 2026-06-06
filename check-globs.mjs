// Single source of truth for which `.mjs` files the JS quality gates cover.
//
// c8 (coverage, .c8rc.json), tsc (typecheck, tsconfig.json), and ESLint
// (eslint.config.js) must all scope to the SAME source set. Before this module
// existed the set was hand-copied into three configs and drifted:
// `bin/lib/github-app/**` was coverage-gated to 100% yet silently skipped by
// both tsc and ESLint, hiding real type and lint errors.
//
// ESLint is JS and imports SOURCE_GLOBS directly. The JSON configs can't import,
// so `tests/source-globs-drift.test.mjs` pins their `include` arrays to this
// list — add a source root here and the drift test fails until the JSON catches
// up, which is the point.
export const SOURCE_GLOBS = [
  ".claude/hooks/**/*.mjs",
  "bin/lib/github-app/**/*.mjs",
  ".github/actions/**/*.mjs",
];
