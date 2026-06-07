"""Tests for ensure_docker_compose_version (bin/lib/docker-engine.bash).

The sandbox compose file uses the start_interval healthcheck key, which only
Compose 2.20.3+ accepts; an older Compose fails `docker compose up`. setup.bash
calls this to flag — and on macOS, upgrade — a too-old Compose. We stub the
setup-provided helpers (status/warn/command_exists/IS_MAC) and a `docker` whose
`compose version --short` reports a chosen version, then assert the branch taken.
"""

import shutil
from pathlib import Path

from tests._helpers import REPO_ROOT, run_capture, write_exe

PLUGINS = REPO_ROOT / "bin" / "lib" / "docker-plugins.bash"
ENGINE = REPO_ROOT / "bin" / "lib" / "docker-engine.bash"
BASH = shutil.which("bash") or "/bin/bash"


def _docker_stub(compose_version: str | None) -> str:
    """A `docker` whose `compose version --short` prints `compose_version` (and
    `compose version` execs, so docker_plugin_works sees the plugin). `None` makes
    every compose call fail — the "no compose installed" case."""
    if compose_version is None:
        return f"#!{BASH}\nexit 1\n"
    return (
        f"#!{BASH}\n"
        '[[ "$1" == "compose" && "$2" == "version" && "$3" == "--short" ]]'
        f' && {{ echo "{compose_version}"; exit 0; }}\n'
        '[[ "$1" == "compose" && "$2" == "version" ]] && exit 0\n'
        "exit 0\n"
    )


def _run(
    tmp_path: Path,
    *,
    compose_version: str | None,
    is_mac: bool = False,
    brew: bool = False,
) -> str:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    write_exe(bin_dir / "docker", _docker_stub(compose_version))
    if brew:
        # A brew that "succeeds" but cannot change our fixed docker stub's version,
        # so the post-upgrade re-check still sees the old version.
        write_exe(bin_dir / "brew", f"#!{BASH}\nexit 0\n")
    harness = (
        f'source "{PLUGINS}"\n'
        f'source "{ENGINE}"\n'
        f"IS_MAC={'true' if is_mac else 'false'}\n"
        'status(){ echo "STATUS: $*"; }\n'
        'warn(){ echo "WARN: $*"; }\n'
        'command_exists(){ command -v "$1" >/dev/null 2>&1; }\n'
        "repair_docker_cli_plugin(){ :; }\n"
        "ensure_docker_compose_version\n"
    )
    r = run_capture([BASH, "-c", harness], env={"PATH": f"{bin_dir}:/usr/bin:/bin"})
    assert r.returncode == 0, r.stderr
    return r.stdout


def test_current_compose_reports_ok(tmp_path: Path) -> None:
    out = _run(tmp_path, compose_version="2.39.0")
    assert "STATUS: Docker Compose 2.39.0 is current" in out
    assert "WARN:" not in out


def test_too_old_on_linux_warns_with_fix(tmp_path: Path) -> None:
    out = _run(tmp_path, compose_version="2.10.0", is_mac=False)
    assert "WARN: Docker Compose 2.10.0 is below 2.20.3" in out
    assert "docker-compose-plugin" in out  # Linux guidance, not a brew upgrade
    assert "Upgrading docker-compose via Homebrew" not in out


def test_absent_compose_is_noop(tmp_path: Path) -> None:
    # No compose to gate: stay silent (presence is the plugin/distro path's job).
    out = _run(tmp_path, compose_version=None)
    assert out.strip() == ""


def test_too_old_on_mac_attempts_brew_upgrade(tmp_path: Path) -> None:
    # macOS + brew: try the upgrade; our stub can't raise the version, so it then
    # warns that Homebrew did not reach the floor (exercises the brew branch).
    out = _run(tmp_path, compose_version="2.10.0", is_mac=True, brew=True)
    assert "Upgrading docker-compose via Homebrew" in out
    assert "WARN:   Homebrew did not raise Compose to 2.20.3" in out
