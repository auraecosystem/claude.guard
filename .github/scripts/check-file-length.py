#!/usr/bin/env python3
"""Enforce maximum file lengths to prevent god files.

Fails when a staged code file exceeds DEFAULT_MAX lines. Files that are
legitimately larger (top-level entry-point scripts that orchestrate many
concerns) must be listed in OVERRIDES with their explicit ceiling and a
brief justification comment — adding an entry here is a deliberate exception,
not a silent bypass.

Only code files under checked directories are examined; tests, docs, generated
files, and data files are excluded so the gate doesn't fire on large test
suites or lock files.
"""

import sys
from pathlib import Path

# Default ceiling for all checked code files.
DEFAULT_MAX = 400

# Per-file overrides for files that are intentionally larger.
# Format: "repo-root-relative path": ceiling
# Adding an entry here requires a justification comment explaining why this
# file is larger than DEFAULT_MAX and why it cannot (yet) be split further.
OVERRIDES: dict[str, int] = {
    # Main wrapper: top-level orchestrator that wires together firewall, volumes,
    # git state, auth, and compose — splitting requires richer lib interfaces.
    "bin/claude-guard": 1300,
    # Installation script: handles Homebrew vs source, platform detection,
    # runtime install, shell integration, and symlink management.
    "setup.bash": 1200,
    # Container firewall bootstrap: inline iptables/dnsmasq setup interspersed
    # with config generation; hard to modularise further without a runtime loader.
    ".devcontainer/init-firewall.bash": 900,
    # Health-check CLI: rich table rendering + subprocess orchestration + multiple
    # verification concerns all need the shared Rich console object.
    "bin/claude-guard-doctor": 1100,
    # Compose lifecycle smoke test: single bash script that drives a full
    # devcontainer round-trip; splitting would need a test harness framework.
    "bin/check-compose-lifecycle.bash": 550,
    # Monitor performance gate: data collection + stats + chart generation + PR
    # comment all share the same pytest session state.
    "bin/check-monitor-perf.py": 550,
}

# Directories whose files are skipped entirely (tests, docs, generated output).
SKIP_DIRS = frozenset({"tests", "docs", "research", "node_modules", ".git"})

# File suffixes that are not source code (data, config, generated, docs).
SKIP_SUFFIXES = frozenset(
    {
        ".md",
        ".json",
        ".yaml",
        ".yml",
        ".lock",
        ".zsh",
        ".txt",
        ".html",
        ".css",
        ".js",
        ".mjs",
    }
)

# Only check files under these directories (avoids firing on random root files).
CHECKED_DIRS = frozenset({"bin", ".devcontainer", ".claude", ".github/scripts"})


def _should_check(rel: Path) -> bool:
    parts = rel.parts
    if any(part in SKIP_DIRS for part in parts):
        return False
    if rel.suffix in SKIP_SUFFIXES:
        return False
    # Only check files inside the explicitly guarded directories.
    if parts and parts[0] not in CHECKED_DIRS and str(rel) not in OVERRIDES:
        return False
    return True


def check_file(path: Path, repo_root: Path) -> str | None:
    try:
        rel = path.resolve().relative_to(repo_root.resolve())
    except ValueError:
        return None
    if not _should_check(rel):
        return None
    ceiling = OVERRIDES.get(str(rel), DEFAULT_MAX)
    try:
        line_count = path.read_text(errors="replace").count("\n")
    except OSError:
        return None
    if line_count > ceiling:
        return (
            f"{rel}: {line_count} lines exceeds limit of {ceiling} "
            f"(add to OVERRIDES in .github/scripts/check-file-length.py with justification)"
        )
    return None


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: check-file-length.py <file...>", file=sys.stderr)
        sys.exit(1)
    repo_root = Path(__file__).parent.parent.parent
    errors = [
        err
        for arg in sys.argv[1:]
        if (err := check_file(Path(arg), repo_root)) is not None
    ]
    if errors:
        print("File length violations:", file=sys.stderr)
        for err in errors:
            print(f"  {err}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
