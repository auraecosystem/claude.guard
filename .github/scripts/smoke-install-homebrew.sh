#!/usr/bin/env bash
# smoke-install-homebrew.sh — install the formula from the working tree and run
# the shared smoke, plus a regression guard for the link_overwrite fix: a
# pre-existing `claude` on the Homebrew prefix must NOT stop Homebrew from
# linking the keg (without link_overwrite the whole keg is left unlinked and
# `claude-guard` never reaches PATH).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
export HOMEBREW_NO_AUTO_UPDATE=1 HOMEBREW_NO_ENV_HINTS=1

prefix="$(brew --prefix)"

# Seed a conflicting `claude` so linking the keg must overwrite it.
printf '#!/bin/sh\n' >"$prefix/bin/claude"
chmod +x "$prefix/bin/claude"

brew install --formula "$REPO_ROOT/packaging/homebrew/claude-guard.rb"

# The keg linked despite the conflict ⇒ claude-guard reached PATH.
bash "$SCRIPT_DIR/smoke-assert-claude-guard.sh"

# And `claude` now routes to the guard (the override the formula installs over
# the seeded file).
claude_target="$(readlink "$prefix/bin/claude" || true)"
echo "claude -> $claude_target"
[[ "$claude_target" == *claude-guard* ]] || {
  echo "FAIL: claude was not overridden to the guard wrapper" >&2
  exit 1
}

echo "PASS: Homebrew link-overwrite regression guard"
