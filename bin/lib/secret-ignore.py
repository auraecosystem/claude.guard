#!/usr/bin/env python3
"""Per-repo secret-ignore list for the startup credential scan.

The hardener's credential scan (credscan-secrets.py) emits, per flagged file, a
``CREDSCAN_FINDING`` line carrying the file's path and the SHA-256 of each
secret in it. This module is the host-side brain the wrapper
(bin/claude-guard's _surface_credential_warning) drives with those lines:

  plan   — read findings on stdin, drop any whose every secret is already
           ignored for this repo, print a human warning for the rest, and write
           their not-yet-ignored hashes to --pending. Exit 0 when something
           still warrants warning, 3 when every finding is fully ignored.
  accept — merge the --pending hashes into this repo's ignore set, so those
           exact secrets stop warning next launch. A file that later gains a NEW
           secret has a hash the set lacks, so it warns again — ignoring the
           existing secrets, never the file forever.

The list lives at $XDG_CONFIG_HOME/claude/secret-ignore.json (mode 0600),
keyed by the workspace's git remote (its root path when it has none), so it is
global to the user yet scoped per repository. It only silences the launch
warning; live transcript redaction (redact-secrets.py) is untouched, so an
ignored secret is still hidden from the model's view of tool output.
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


def ignore_file_path() -> Path:
    """Location of the per-repo secret-ignore list."""
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "claude" / "secret-ignore.json"


def repo_key(workspace: str) -> str:
    """Stable identity for ``workspace``: its origin git remote, or its absolute
    path when it has no remote (a not-yet-pushed repo or a bare directory)."""
    try:
        out = subprocess.run(
            ["git", "-C", workspace, "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        out = None
    if out is not None and out.returncode == 0 and out.stdout.strip():
        return out.stdout.strip()
    return str(Path(workspace).resolve())


def load_ignored(path: Path, key: str) -> set[str]:
    """The set of ignored secret hashes recorded for ``key``; empty when the
    file is absent or holds no entry for this repo."""
    if not path.exists():
        return set()
    data = json.loads(path.read_text("utf-8"))
    return set(data.get(key, []))


def parse_findings(lines: list[str]) -> list[tuple[str, list[str]]]:
    """Parse ``CREDSCAN_FINDING<TAB>kind<TAB>path<TAB>hashes`` lines into
    ``(path, hashes)`` pairs. A finding with no hashes (an unreadable file) keeps
    an empty list, so it can never be fully ignored."""
    findings: list[tuple[str, list[str]]] = []
    for line in lines:
        fields = line.rstrip("\n").split("\t")
        if len(fields) != 4 or fields[0] != "CREDSCAN_FINDING":
            continue
        _marker, _kind, path, joined = fields
        hashes = [h for h in joined.split(",") if h]
        findings.append((path, hashes))
    return findings


def plan(workspace: str, pending: Path) -> int:
    """Decide what still warrants warning; write pending hashes; return exit code."""
    findings = parse_findings(sys.stdin.read().splitlines())
    ignored = load_ignored(ignore_file_path(), repo_key(workspace))

    warn_paths: list[str] = []
    new_hashes: list[str] = []
    for path, hashes in findings:
        unignored = [h for h in hashes if h not in ignored]
        # A finding warns unless every secret in it is already ignored; an
        # unreadable file (no hashes) always warns and contributes nothing to
        # ignore — there is no hash to remember it by.
        if hashes and not unignored:
            continue
        warn_paths.append(path)
        new_hashes.extend(unignored)

    if not warn_paths:
        return 3

    # No prompt-referencing copy here: plan runs before the wrapper knows whether
    # a TTY follows, and on a piped launch no prompt comes. The wrapper's own
    # "[y/N]" line is the actionable bit when interactive.
    print("Secrets detected in your workspace — the agent runs commands that can")
    print("read these files and send their contents out:")
    for path in warn_paths:
        print(f"  {path}")
    print("Remove them or mount a narrower workspace.")
    pending.write_text("\n".join(dict.fromkeys(new_hashes)) + "\n", "utf-8")
    return 0


def accept(workspace: str, pending: Path) -> int:
    """Merge the pending hashes into this repo's ignore set."""
    hashes = [h for h in pending.read_text("utf-8").splitlines() if h]
    if not hashes:
        return 0
    path = ignore_file_path()
    key = repo_key(workspace)
    data = json.loads(path.read_text("utf-8")) if path.exists() else {}
    merged = dict.fromkeys(data.get(key, []))
    merged.update(dict.fromkeys(hashes))
    data[key] = list(merged)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", "utf-8")
    path.chmod(0o600)
    print(f"claude-guard: ignoring {len(hashes)} secret(s) for this repo from now on.")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="secret-ignore")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "accept"):
        p = sub.add_parser(name)
        p.add_argument("--workspace", required=True)
        p.add_argument("--pending", required=True, type=Path)
    args = parser.parse_args(argv)
    handler = {"plan": plan, "accept": accept}[args.command]
    return handler(args.workspace, args.pending)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))
