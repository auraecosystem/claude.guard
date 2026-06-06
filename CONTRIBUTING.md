# Contributing to `claude-guard`

Thanks for helping make a responsible Claude Code setup the default. Issues and PRs are welcome — this is security tooling written by an AI professional who is **not** a security professional, so extra eyes are genuinely valuable.

[`CLAUDE.md`](CLAUDE.md) is the canonical, in-depth reference for this repo's conventions (coverage gates, code style, CI patterns). This file is the short version for human contributors.

## Getting set up

```bash
git clone https://github.com/alexander-turner/claude-guard.git
cd claude-guard
pnpm install   # installs deps; postinstall points git at the repo's .hooks/
```

Use **pnpm** (not npm). To run the full sandbox locally, `bash setup.bash` provisions Docker, the sandbox runtime, and supporting tools (see the [README](README.md#install)).

## Development loop

```bash
pnpm format         # Prettier
pnpm lint           # ESLint
pnpm check          # tsc --noEmit
pnpm test           # node --test
pnpm test:coverage  # c8 — JS hooks gated at 100% per file
pre-commit run --all-files   # shellcheck/shfmt + wider hygiene checks
```

`pre-commit` is **not** re-run in CI, so run it before pushing — especially after any `--no-verify` commit, which silently lets banned patterns through.

## Commits

Commits **must** follow [Conventional Commits](https://www.conventionalcommits.org/) (`<type>(<scope>): <desc>`; types `feat fix refactor docs test chore ci style perf build`, `!` for breaking). The `commit-msg` hook enforces this.

**Never rewrite published history** — once pushed, don't rebase, amend, or force-push. Resolve conflicts with a merge commit, not a rebase. Multi-commit branches are fine; don't squash to tidy the count.

## Tests

Don't skip or weaken tests unless a maintainer asks. New JS hooks and bash wrappers are coverage-gated at **100% per file**; see [CLAUDE.md](CLAUDE.md) for how the c8 and kcov gates are wired before adding either.

## Pull requests

Open PRs against `main` using [the template](.github/PULL_REQUEST_TEMPLATE.md), and make sure:

- CI is green (fix any pre-existing red in the same PR, in its own `fix(...)` commit).
- The description matches the diff. Touch `README.md` / `SECURITY.md` only when a user-facing or security-boundary change requires it.
- A `## Lessons Learned` section appears **only** for insights that would help a maintainer of an unrelated project — delete it otherwise.

**The `[monitor-eval]` title tag triggers a real ~350-call LLM eval run** — add it only when a PR changes the monitor model, its policy, or the eval harness.

## Code style

Fail loudly, prefer flat control flow, and optimize for the reader landing cold. The full rationale and rules live in [CLAUDE.md](CLAUDE.md#code-style).

## Reporting a security issue

See [`SECURITY.md`](SECURITY.md) — don't open a public issue for an exploitable vulnerability.
