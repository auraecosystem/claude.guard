"""Behavioral tests for the dependency install (.devcontainer/deps-install.bash).

install_deps verifies node_modules offline first so an already-complete tree needs no
network and an incomplete one fails fast instead of hanging on the firewall-blocked
registry, fetching online only when a proxy is configured. It is sourced by
entrypoint.bash; here we source it directly and drive it with `su`/`pnpm` stubs, the
only way to exercise its branches without booting a container.
"""

# covers: .devcontainer/deps-install.bash

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
HELPER = REPO_ROOT / ".devcontainer" / "deps-install.bash"


def _write_exe(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(0o755)


def _stub_bin(tmp_path: Path, *, offline_ok: bool, online_ok: bool) -> Path:
    """A PATH dir with `su` (runs `su node -c CMD` as the current user) and a `pnpm`
    whose offline vs online install outcome is fixed by the flags. The pnpm stub
    refuses to "install" without --ignore-scripts, so a regression that drops the flag
    fails the suite (a lifecycle script would otherwise run with the hardener's egress)."""
    stub = tmp_path / "bin"
    stub.mkdir()
    # `su node -c "<cmd>"` → args: node -c <cmd>. Run the command as-is.
    _write_exe(stub / "su", '#!/bin/bash\nexec bash -c "$3"\n')
    # `pnpm install ... --offline ...` succeeds iff offline_ok; an online install
    # (no --offline) succeeds iff online_ok. Both require --ignore-scripts (exit 3 if
    # absent) so a dropped flag fails the suite.
    _write_exe(
        stub / "pnpm",
        f"""#!/bin/bash
ignore=0; offline=0
for a in "$@"; do
  [[ "$a" == "--ignore-scripts" ]] && ignore=1
  [[ "$a" == "--offline" ]] && offline=1
done
[[ $ignore == 1 ]] || {{ echo "pnpm install without --ignore-scripts" >&2; exit 3; }}
[[ $offline == 1 ]] && exit {0 if offline_ok else 1}
exit {0 if online_ok else 1}
""",
    )
    return stub


def _run(script: str, stub: Path, **env_extra: str) -> subprocess.CompletedProcess:
    env = {"PATH": f"{stub}:/usr/bin:/bin", **env_extra}
    return subprocess.run(
        ["bash", "-c", f'source "{HELPER}"; set -euo pipefail; {script}'],
        capture_output=True,
        text=True,
        env=env,
    )


def test_offline_verify_succeeds_without_network(tmp_path: Path) -> None:
    """A complete tree is confirmed offline — no online install even if it would fail."""
    stub = _stub_bin(tmp_path, offline_ok=True, online_ok=False)
    r = _run(f'install_deps "{tmp_path}"', stub)
    assert r.returncode == 0, r.stderr


def test_incomplete_without_proxy_fails_fast(tmp_path: Path) -> None:
    """An incomplete tree with no proxy fails loudly rather than hanging online."""
    stub = _stub_bin(tmp_path, offline_ok=False, online_ok=True)
    r = _run(f'install_deps "{tmp_path}"', stub)
    assert r.returncode == 1
    assert "no registry access" in r.stderr


def test_incomplete_with_proxy_fetches_online(tmp_path: Path) -> None:
    """With a proxy configured, an offline miss falls back to an online install."""
    stub = _stub_bin(tmp_path, offline_ok=False, online_ok=True)
    r = _run(f'install_deps "{tmp_path}"', stub, HTTPS_PROXY="http://172.30.0.2:3128")
    assert r.returncode == 0, r.stderr
    assert "via proxy" in r.stdout


def test_online_failure_propagates(tmp_path: Path) -> None:
    """When even the online install fails, the non-zero status reaches the caller."""
    stub = _stub_bin(tmp_path, offline_ok=False, online_ok=False)
    r = _run(f'install_deps "{tmp_path}"', stub, HTTP_PROXY="http://172.30.0.2:3128")
    assert r.returncode != 0
