#!/usr/bin/env bash
# smoke-assert-claude-guard.sh — post-install checks shared by every package
# format (deb/rpm/Homebrew/AUR). Proves the installed package put the wrapper on
# PATH and that it runs to its first screen (--version / --help) WITHOUT a
# sandbox launch (no Docker, no auth, no image pull). Run after the package is
# installed.
set -euo pipefail

cg="$(command -v claude-guard)" || {
  echo "FAIL: claude-guard is not on PATH after install" >&2
  exit 1
}
echo "claude-guard on PATH: $cg"

# --version reads the packaged package.json via jq (a hard dep of every format)
# and prints the released semver. This proves the wrapper canonicalized its own
# packaged symlink back to the install root, not merely that a file exists.
version_line="$(claude-guard --version)"
echo "version: $version_line"
grep -Eq '^claude-guard [0-9]+\.[0-9]+\.[0-9]+' <<<"$version_line" || {
  echo "FAIL: --version did not print 'claude-guard <semver>'" >&2
  exit 1
}

# --help renders the wrapper usage — the cheapest "first screen" reachable with
# no Docker/auth. `doctor` is a documented subcommand in that usage.
help_out="$(claude-guard --help)"
grep -q 'doctor' <<<"$help_out" || {
  echo "FAIL: --help did not render the wrapper usage" >&2
  exit 1
}

echo "PASS: claude-guard install smoke"
