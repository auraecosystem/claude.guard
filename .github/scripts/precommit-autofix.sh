#!/usr/bin/env bash
# Provision the pre-commit toolchain, auto-fix what's fixable, push the fix as a
# NEW commit (never an amend/rebase — that would orphan on a shallow clone and
# close the PR), then re-run pre-commit so any non-autofixable violation surfaces
# as the job's verdict. Invoked by .github/workflows/pre-commit.yaml.
set -euo pipefail

export CLAUDE_PROJECT_DIR="${GITHUB_WORKSPACE:-$PWD}"

# session-setup.sh is the SSOT provisioner: it installs pre-commit (uv), shellharden
# (the one binary the `language: system` hooks shell out to), the node + python deps
# the generator hooks need, and pre-warms the pinned hook environments with retries.
bash .claude/hooks/session-setup.sh
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$CLAUDE_PROJECT_DIR/.venv/bin:$PATH"

command -v pre-commit >/dev/null 2>&1 || {
  echo "pre-commit was not provisioned by session-setup.sh" >&2
  exit 1
}

# Autofix pass: pre-commit exits non-zero whenever it modifies a file, so its exit
# status here is not a verdict — tolerate it and judge by the resulting diff.
pre-commit run --all-files --color always || true

# --porcelain (not `git diff --quiet`) so we catch every shape of change: the
# whitespace/format hooks modify the working tree (unstaged), while the gen-*
# hooks `git add` their regenerated output (staged) — a working-tree-only check
# would silently skip the latter.
if [[ -n "$(git status --porcelain)" ]]; then
  git config user.name "github-actions[bot]"
  git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
  git add -A
  # --no-verify: the local pre-commit/lint-staged git hooks would re-run this whole
  # suite recursively. We just ran pre-commit, so the tree is already clean — the
  # verification pass below re-proves it before the job's exit code is set.
  git commit --no-verify -m "style: apply pre-commit autofixes"
  # Push to the PR's head branch. GITHUB_TOKEN-authored pushes do NOT retrigger
  # workflows, so this cannot loop.
  git push origin "HEAD:${GITHUB_HEAD_REF}"
fi

# Verification pass over the now-clean tree: autofixers make no further changes, so
# a non-zero here means a genuine, non-autofixable violation the author must fix.
pre-commit run --all-files --color always
