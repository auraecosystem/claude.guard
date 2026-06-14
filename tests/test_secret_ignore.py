"""In-process unit tests for bin/lib/secret-ignore.py — the host-side brain that
drives the per-repo secret-ignore prompt from the hardener's CREDSCAN_FINDING
lines. The wrapper plumbing is covered in test_claude_guard_coverage.py; these
import the module directly so the coverage gate traces it.
"""

import importlib.util
import io
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from tests._helpers import REPO_ROOT

# covers: bin/lib/secret-ignore.py

SRC = REPO_ROOT / "bin" / "lib" / "secret-ignore.py"

# Two distinct credential-shaped hashes (any 64-hex strings work as opaque ids).
H1 = "a" * 64
H2 = "b" * 64
H3 = "c" * 64


@pytest.fixture(name="mod", scope="module")
def fixture_mod() -> ModuleType:
    spec = importlib.util.spec_from_file_location("secret_ignore", SRC)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["secret_ignore"] = module
    spec.loader.exec_module(module)
    return module


def _finding(kind: str, path: str, hashes: str) -> str:
    return f"CREDSCAN_FINDING\t{kind}\t{path}\t{hashes}"


# ─── ignore_file_path ────────────────────────────────────────────────────────


def test_ignore_file_path_honors_xdg(
    mod: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert mod.ignore_file_path() == tmp_path / "claude" / "secret-ignore.json"


def test_ignore_file_path_falls_back_to_home_config(
    mod: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(mod.Path, "home", staticmethod(lambda: tmp_path))
    assert (
        mod.ignore_file_path() == tmp_path / ".config" / "claude" / "secret-ignore.json"
    )


# ─── repo_key ────────────────────────────────────────────────────────────────


def test_repo_key_uses_origin_remote(mod: ModuleType, tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "remote", "add", "origin", "https://x.test/r.git"],
        check=True,
    )
    assert mod.repo_key(str(tmp_path)) == "https://x.test/r.git"


def test_repo_key_falls_back_to_path_without_remote(
    mod: ModuleType, tmp_path: Path
) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    assert mod.repo_key(str(tmp_path)) == str(tmp_path.resolve())


def test_repo_key_falls_back_when_git_missing(
    mod: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def _boom(*_a: object, **_k: object) -> None:
        raise OSError("no git")

    monkeypatch.setattr(mod.subprocess, "run", _boom)
    assert mod.repo_key(str(tmp_path)) == str(tmp_path.resolve())


# ─── load_ignored ────────────────────────────────────────────────────────────


def test_load_ignored_absent_file_is_empty(mod: ModuleType, tmp_path: Path) -> None:
    assert mod.load_ignored(tmp_path / "missing.json", "k") == set()


def test_load_ignored_returns_repo_entry(mod: ModuleType, tmp_path: Path) -> None:
    p = tmp_path / "i.json"
    p.write_text(json.dumps({"k": [H1, H2], "other": [H3]}), "utf-8")
    assert mod.load_ignored(p, "k") == {H1, H2}


def test_load_ignored_missing_repo_entry_is_empty(
    mod: ModuleType, tmp_path: Path
) -> None:
    p = tmp_path / "i.json"
    p.write_text(json.dumps({"other": [H3]}), "utf-8")
    assert mod.load_ignored(p, "k") == set()


# ─── parse_findings ──────────────────────────────────────────────────────────


def test_parse_findings_extracts_path_and_hashes(mod: ModuleType) -> None:
    lines = [_finding("secret", "/workspace/.env", f"{H1},{H2}")]
    assert mod.parse_findings(lines) == [("/workspace/.env", [H1, H2])]


def test_parse_findings_empty_hashes_keeps_empty_list(mod: ModuleType) -> None:
    assert mod.parse_findings([_finding("key", "/workspace/id_rsa", "")]) == [
        ("/workspace/id_rsa", [])
    ]


def test_parse_findings_ignores_malformed_lines(mod: ModuleType) -> None:
    lines = [
        "unrelated log line",
        "CREDSCAN_FINDING\tsecret\tonly-three-fields",
        _finding("secret", "/workspace/.env", H1),
    ]
    assert mod.parse_findings(lines) == [("/workspace/.env", [H1])]


# ─── plan ────────────────────────────────────────────────────────────────────


def _run_plan(
    mod: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    findings: str,
    ignored: dict[str, list[str]] | None = None,
) -> tuple[int, Path]:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    ws = tmp_path / "ws"
    ws.mkdir()
    if ignored is not None:
        f = mod.ignore_file_path()
        f.parent.mkdir(parents=True, exist_ok=True)
        # Key the ignore entry by what repo_key will compute for this workspace.
        f.write_text(json.dumps({mod.repo_key(str(ws)): ignored["k"]}), "utf-8")
    monkeypatch.setattr("sys.stdin", io.StringIO(findings))
    pending = tmp_path / "pending"
    rc = mod.plan(str(ws), pending)
    return rc, pending


def test_plan_warns_and_writes_pending(
    mod: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc, pending = _run_plan(
        mod, monkeypatch, tmp_path, _finding("secret", "/workspace/.env", f"{H1},{H2}")
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "/workspace/.env" in out and "Secrets detected in your workspace" in out
    assert pending.read_text().split() == [H1, H2]


def test_plan_suppresses_fully_ignored_finding(
    mod: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    rc, _ = _run_plan(
        mod,
        monkeypatch,
        tmp_path,
        _finding("secret", "/workspace/.env", f"{H1},{H2}"),
        ignored={"k": [H1, H2]},
    )
    assert rc == 3


def test_plan_re_warns_on_new_secret(
    mod: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    rc, pending = _run_plan(
        mod,
        monkeypatch,
        tmp_path,
        _finding("secret", "/workspace/.env", f"{H1},{H2},{H3}"),
        ignored={"k": [H1, H2]},
    )
    assert rc == 0
    assert pending.read_text().split() == [H3]


def test_plan_unreadable_finding_always_warns(
    mod: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # No hashes (unreadable, fail-closed): warns even with an ignore list present,
    # and contributes nothing to pending — there is no hash to remember it by.
    rc, pending = _run_plan(
        mod,
        monkeypatch,
        tmp_path,
        _finding("key", "/workspace/id_rsa", ""),
        ignored={"k": [H1]},
    )
    assert rc == 0
    assert pending.read_text().split() == []


# ─── accept ──────────────────────────────────────────────────────────────────


def test_accept_creates_and_merges(
    mod: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    ws = tmp_path / "ws"
    ws.mkdir()
    pending = tmp_path / "pending"
    pending.write_text(f"{H1}\n{H2}\n", "utf-8")
    assert mod.accept(str(ws), pending) == 0
    f = mod.ignore_file_path()
    assert json.loads(f.read_text())[mod.repo_key(str(ws))] == [H1, H2]
    assert (f.stat().st_mode & 0o777) == 0o600
    assert "ignoring 2 secret(s)" in capsys.readouterr().out

    # A second accept merges without duplicating the existing hashes.
    pending.write_text(f"{H2}\n{H3}\n", "utf-8")
    assert mod.accept(str(ws), pending) == 0
    assert json.loads(f.read_text())[mod.repo_key(str(ws))] == [H1, H2, H3]


def test_accept_empty_pending_is_noop(
    mod: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    pending = tmp_path / "pending"
    pending.write_text("\n", "utf-8")
    assert mod.accept(str(tmp_path), pending) == 0
    assert not mod.ignore_file_path().exists()


# ─── main dispatch ───────────────────────────────────────────────────────────


def test_main_plan_dispatch(
    mod: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setattr("sys.stdin", io.StringIO(_finding("secret", "/w/.env", H1)))
    pending = tmp_path / "pending"
    assert mod.main(["plan", "--workspace", str(ws), "--pending", str(pending)]) == 0


def test_main_accept_dispatch(
    mod: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    ws = tmp_path / "ws"
    ws.mkdir()
    pending = tmp_path / "pending"
    pending.write_text(f"{H1}\n", "utf-8")
    assert mod.main(["accept", "--workspace", str(ws), "--pending", str(pending)]) == 0
    assert mod.ignore_file_path().exists()
