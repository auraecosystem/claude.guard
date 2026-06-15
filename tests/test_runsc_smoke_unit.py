"""Unit tests for check-runsc-smoke.bash's runsc_registered debounce.

The runsc smoke job flaked because the old check was a single `docker info | grep
runsc`: `runsc install` rewrites daemon.json and a live-reloading daemon exposes
the runtime mid-reload (present one moment, gone the next), so a one-shot check
latched onto that transient flicker and reported a registration that vanished
seconds later. runsc_registered now requires the runtime on three CONSECUTIVE
polls. These tests pin that invariant — a flicker must NOT count as registered,
only stable presence does — by slicing the function out and driving `docker info`
through a per-call schedule (with sleep stubbed so the polls don't actually wait).
"""

import shutil
from pathlib import Path

from tests._helpers import REPO_ROOT, run_capture, slice_bash_function, write_exe

SCRIPT = REPO_ROOT / "bin" / "check-runsc-smoke.bash"
BASH = shutil.which("bash") or "/bin/bash"

_FN = slice_bash_function(SCRIPT, "runsc_registered")
# `sleep` is stubbed to a no-op so the poll loop runs instantly; the sliced
# function is then called with the max-seconds (= poll count) under test.
_HARNESS = f'set -euo pipefail\nsleep() {{ :; }}\n{_FN}\nrunsc_registered "$1"\n'

# A `docker` stub whose every `info` invocation emits "runsc" (or not) per the next
# entry of $SCHEDULE, indexed by a persisted call counter — so a test can script the
# exact present/absent sequence the daemon's reload flicker would produce. Past the
# end of the schedule it repeats the last entry.
_DOCKER_STUB = (
    "#!/bin/bash\n"
    'n=$(cat "$DOCKER_COUNTER" 2>/dev/null || echo 0)\n'
    'printf %s "$((n + 1))" >"$DOCKER_COUNTER"\n'
    'read -ra sched <<<"$SCHEDULE"\n'
    "v=${sched[n]:-${sched[$((${#sched[@]} - 1))]}}\n"
    '[[ "$v" == 1 ]] && echo runsc\n'
    "exit 0\n"
)


def _run(tmp_path: Path, schedule: str, max_polls: int) -> int:
    """Drive runsc_registered <max_polls> with `docker info` answering per
    `schedule` (space-separated 1/0 per call). Returns the function's exit code."""
    stub = tmp_path / "bin"
    stub.mkdir(exist_ok=True)
    write_exe(stub / "docker", _DOCKER_STUB)
    r = run_capture(
        [BASH, "-c", _HARNESS, "bash", str(max_polls)],
        env={
            "PATH": f"{stub}:/usr/bin:/bin",
            "SCHEDULE": schedule,
            "DOCKER_COUNTER": str(tmp_path / "counter"),
        },
    )
    return r.returncode


def test_stable_presence_registers(tmp_path: Path) -> None:
    """Runtime present on every poll → registered (three consecutive hits)."""
    assert _run(tmp_path, "1 1 1 1 1", 5) == 0


def test_transient_flicker_never_registers(tmp_path: Path) -> None:
    """A runtime that flickers present/absent and never holds for three polls must
    NOT count as registered — the exact false positive that caused the flake."""
    assert _run(tmp_path, "1 0 1 0 1 0 1 0 1 0", 10) == 1


def test_absent_runtime_does_not_register(tmp_path: Path) -> None:
    """Never present → not registered, exhausting the poll budget."""
    assert _run(tmp_path, "0 0 0", 3) == 1


def test_stabilizes_after_initial_flicker(tmp_path: Path) -> None:
    """A flicker that then settles (the streak resets on the gap, then reaches three
    consecutive) registers — the daemon eventually finishing its reload."""
    assert _run(tmp_path, "1 0 1 1 1", 5) == 0


def test_late_brief_presence_within_budget_is_rejected(tmp_path: Path) -> None:
    """Presence that appears only at the very end, without three consecutive hits
    before the budget runs out, is not enough — registration must be stable."""
    assert _run(tmp_path, "0 0 0 0 1", 5) == 1
