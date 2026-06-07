"""Behavioral tests for the lockfile-keyed dependency install (.devcontainer/deps-install.bash).

The helper decides whether the hardener must run `pnpm install` at all: it skips when
node_modules already satisfies the lockfile, verifies offline first so an incomplete
tree fails fast instead of hanging on the firewall-blocked registry, and only fetches
online when a proxy is configured. It is sourced by entrypoint.bash; here we source it
directly and drive it with `su`/`pnpm` stubs, the only way to exercise its branches
without booting a container.
"""

# covers: .devcontainer/deps-install.bash

import hashlib
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HELPER = REPO_ROOT / ".devcontainer" / "deps-install.bash"


def _write_exe(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(0o755)


def _stub_bin(tmp_path: Path, *, offline_ok: bool, online_ok: bool) -> Path:
    """A PATH dir with `su` (runs `su node -c CMD` as the current user) and a `pnpm`
    whose offline vs online install outcome is fixed by the flags."""
    stub = tmp_path / "bin"
    stub.mkdir()
    # `su node -c "<cmd>"` → args: node -c <cmd>. Run the command as-is.
    _write_exe(stub / "su", '#!/bin/bash\nexec bash -c "$3"\n')
    # `pnpm install ... --offline ...` succeeds iff offline_ok; an online install
    # (no --offline) succeeds iff online_ok.
    _write_exe(
        stub / "pnpm",
        "#!/bin/bash\n"
        'for a in "$@"; do [[ "$a" == "--offline" ]] && '
        f"exit {0 if offline_ok else 1}; done\n"
        f"exit {0 if online_ok else 1}\n",
    )
    return stub


def _run(
    script: str, cwd: Path, stub: Path, **env_extra: str
) -> subprocess.CompletedProcess:
    env = {"PATH": f"{stub}:/usr/bin:/bin", "HOME": str(cwd), **env_extra}
    return subprocess.run(
        ["bash", "-c", f'source "{HELPER}"; {script}'],
        capture_output=True,
        text=True,
        cwd=cwd,
        env=env,
    )


def _make_project(
    root: Path, *, pkg: str = '{"name":"p"}', lock: str | None = "lock\n"
) -> Path:
    (root / "package.json").write_text(pkg)
    if lock is not None:
        (root / "pnpm-lock.yaml").write_text(lock)
    return root


def _fingerprint(root: Path) -> str:
    blob = (root / "package.json").read_bytes()
    lock = root / "pnpm-lock.yaml"
    if lock.exists():
        blob += lock.read_bytes()
    return hashlib.sha256(blob).hexdigest()


# --------------------------------------------------------------------------- #
# deps_fingerprint
# --------------------------------------------------------------------------- #


def test_fingerprint_matches_python_hash(tmp_path: Path) -> None:
    """The bash fingerprint is sha256(package.json ++ pnpm-lock.yaml)."""
    proj = _make_project(tmp_path)
    stub = _stub_bin(tmp_path, offline_ok=True, online_ok=True)
    r = _run(f'deps_fingerprint "{proj}"', proj, stub)
    assert r.returncode == 0
    assert r.stdout.strip() == _fingerprint(proj)


def test_fingerprint_changes_with_lockfile(tmp_path: Path) -> None:
    """A lockfile edit changes the fingerprint, so a stale stamp won't match."""
    proj = _make_project(tmp_path)
    stub = _stub_bin(tmp_path, offline_ok=True, online_ok=True)
    before = _run(f'deps_fingerprint "{proj}"', proj, stub).stdout
    (proj / "pnpm-lock.yaml").write_text("lock2\n")
    after = _run(f'deps_fingerprint "{proj}"', proj, stub).stdout
    assert before != after


# --------------------------------------------------------------------------- #
# deps_up_to_date
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "with_node_modules, stamp, expected",
    [
        (False, None, 1),  # no node_modules
        (True, None, 1),  # node_modules but no stamp
        (True, "stale", 1),  # stamp does not match fingerprint
        (True, "match", 0),  # stamp matches → up to date
    ],
)
def test_up_to_date_branches(
    tmp_path: Path, with_node_modules: bool, stamp: str | None, expected: int
) -> None:
    proj = _make_project(tmp_path)
    stub = _stub_bin(tmp_path, offline_ok=True, online_ok=True)
    if with_node_modules:
        nm = proj / "node_modules"
        nm.mkdir()
        if stamp == "match":
            (nm / ".claude-guard-deps-stamp").write_text(_fingerprint(proj))
        elif stamp == "stale":
            (nm / ".claude-guard-deps-stamp").write_text("deadbeef")
    r = _run(f'deps_up_to_date "{proj}"', proj, stub)
    assert r.returncode == expected


# --------------------------------------------------------------------------- #
# install_deps
# --------------------------------------------------------------------------- #


def test_install_skips_when_current(tmp_path: Path) -> None:
    """A matching stamp short-circuits — no pnpm invoked even if it would fail."""
    proj = _make_project(tmp_path)
    nm = proj / "node_modules"
    nm.mkdir()
    (nm / ".claude-guard-deps-stamp").write_text(_fingerprint(proj))
    stub = _stub_bin(tmp_path, offline_ok=False, online_ok=False)
    r = _run(f'install_deps "{proj}"', proj, stub)
    assert r.returncode == 0, r.stderr
    assert "skipping install" in r.stdout


def test_install_offline_success_stamps(tmp_path: Path) -> None:
    """An offline-verifiable tree installs with no network and records the stamp."""
    proj = _make_project(tmp_path)
    (proj / "node_modules").mkdir()
    stub = _stub_bin(tmp_path, offline_ok=True, online_ok=False)
    r = _run(f'install_deps "{proj}"', proj, stub)
    assert r.returncode == 0, r.stderr
    stamp = proj / "node_modules" / ".claude-guard-deps-stamp"
    assert stamp.read_text() == _fingerprint(proj)


def test_install_incomplete_without_proxy_fails_fast(tmp_path: Path) -> None:
    """An incomplete tree with no proxy fails loudly rather than hanging online."""
    proj = _make_project(tmp_path)
    (proj / "node_modules").mkdir()
    stub = _stub_bin(tmp_path, offline_ok=False, online_ok=True)
    r = _run(f'install_deps "{proj}"', proj, stub)
    assert r.returncode == 1
    assert "no registry access" in r.stderr
    assert not (proj / "node_modules" / ".claude-guard-deps-stamp").exists()


def test_install_incomplete_with_proxy_fetches_online(tmp_path: Path) -> None:
    """With a proxy configured, an offline miss falls back to an online install."""
    proj = _make_project(tmp_path)
    (proj / "node_modules").mkdir()
    stub = _stub_bin(tmp_path, offline_ok=False, online_ok=True)
    r = _run(f'install_deps "{proj}"', proj, stub, HTTPS_PROXY="http://172.30.0.2:3128")
    assert r.returncode == 0, r.stderr
    assert "via proxy" in r.stdout
    assert (
        proj / "node_modules" / ".claude-guard-deps-stamp"
    ).read_text() == _fingerprint(proj)


def test_install_no_lockfile_under_strict_mode(tmp_path: Path) -> None:
    """A workspace with package.json but no lockfile must still stamp and succeed —
    under `set -euo pipefail` (how entrypoint.bash sources this), the fingerprint's
    missing-file read must not abort the launch."""
    proj = _make_project(tmp_path, lock=None)
    (proj / "node_modules").mkdir()
    stub = _stub_bin(tmp_path, offline_ok=True, online_ok=False)
    r = _run(f'set -euo pipefail; install_deps "{proj}"', proj, stub)
    assert r.returncode == 0, r.stderr
    assert (proj / "node_modules" / ".claude-guard-deps-stamp").exists()


def test_install_online_failure_propagates(tmp_path: Path) -> None:
    """When even the online install fails, the non-zero status reaches the caller and
    no stamp is written (so the next launch retries rather than trusting a bad tree)."""
    proj = _make_project(tmp_path)
    (proj / "node_modules").mkdir()
    stub = _stub_bin(tmp_path, offline_ok=False, online_ok=False)
    r = _run(f'install_deps "{proj}"', proj, stub, HTTP_PROXY="http://172.30.0.2:3128")
    assert r.returncode != 0
    assert not (proj / "node_modules" / ".claude-guard-deps-stamp").exists()
