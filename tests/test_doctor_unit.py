"""In-process unit tests for claude-guard-doctor pure helpers.

doctor is extensionless (`#!/usr/bin/env python3`), so pytest-cov never globs it
for a line gate — it is exercised behaviorally and (here) in-process. Importing it
has no side effects: its work is guarded behind `if __name__ == '__main__'`.
"""

import contextlib
import importlib.util
import json
import types
from importlib.machinery import SourceFileLoader

from tests._helpers import REPO_ROOT

DOCTOR = REPO_ROOT / "bin" / "claude-guard-doctor"


def load_doctor() -> types.ModuleType:
    loader = SourceFileLoader("claude_guard_doctor", str(DOCTOR))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


# ── _token_file_fault: token-file permission verdict ────────────────────────
# Mirrors claude-auth.bash's claude_auth_resolve_token 0o77 mask, so the doctor
# and the launcher can never disagree on the same file.


def test_token_fault_owner_only_is_clean() -> None:
    doctor = load_doctor()
    assert doctor._token_file_fault("600") is None
    assert doctor._token_file_fault("400") is None


def test_token_fault_unreadable_by_owner() -> None:
    doctor = load_doctor()
    display, _reason = doctor._token_file_fault("000")
    assert display == "unreadable by owner"


def test_token_fault_group_other_read_write() -> None:
    doctor = load_doctor()
    for perms in ("640", "644", "660", "604"):
        fault = doctor._token_file_fault(perms)
        assert fault is not None and fault[0] == "group/other-accessible", perms


def test_token_fault_catches_execute_only_bits() -> None:
    """The 0o77 mask (not the old 0o66) catches a group/other *execute* bit, so a
    mode like 0610/0601 is refused — the boundary the launcher now enforces too."""
    doctor = load_doctor()
    for perms in ("610", "601"):
        fault = doctor._token_file_fault(perms)
        assert fault is not None and fault[0] == "group/other-accessible", perms


def test_token_fault_unparsable_mode_is_no_fault() -> None:
    """A '?'/non-octal mode (stat failed) is not treated as a fault here — the
    caller only invokes this for concrete numeric modes."""
    doctor = load_doctor()
    assert doctor._token_file_fault("?") is None


# ── bash version probe: empty version must not crash the health check ─────────


def _drive_required_tools(
    monkeypatch, bash_ver_stdout: str, *, rc: int = 0
) -> types.ModuleType:
    """Run report_required_tools with every tool present and the bash version probe
    forced to a chosen stdout. Returns the loaded module so the caller can read its
    `degraded` list (we only care that it returns without crashing)."""
    doctor = load_doctor()
    monkeypatch.setattr(doctor, "section", lambda *a, **k: None)
    monkeypatch.setattr(doctor.console, "print", lambda *a, **k: None)
    # Every tool resolves so the only branch under test is the bash version parse.
    monkeypatch.setattr(doctor, "which", lambda name: f"/usr/bin/{name}")

    def fake_run_bash(script: str):
        if "BASH_VERSINFO" in script:
            return types.SimpleNamespace(stdout=bash_ver_stdout, returncode=rc)
        return types.SimpleNamespace(stdout="", returncode=0)

    monkeypatch.setattr(doctor, "run_bash", fake_run_bash)
    monkeypatch.setattr(doctor, "degraded", [])
    doctor.report_required_tools()
    return doctor


def test_empty_bash_version_degrades_without_crashing(monkeypatch) -> None:
    """An empty version string (bash_ver == "") must report DEGRADED, not raise
    IndexError on bash_ver[0] and abort the whole health check."""
    doctor = _drive_required_tools(monkeypatch, "")
    assert any("< 5.0" in d for d in doctor.degraded), doctor.degraded


def test_nonnumeric_bash_version_degrades(monkeypatch) -> None:
    """A 'unknown' version (probe failed) is also guarded and degrades cleanly."""
    doctor = _drive_required_tools(monkeypatch, "unknown", rc=1)
    assert any("< 5.0" in d for d in doctor.degraded), doctor.degraded


def test_modern_bash_version_is_clean(monkeypatch) -> None:
    """bash 5.x parses to major >= 5 and adds no degrade entry."""
    doctor = _drive_required_tools(monkeypatch, "5.2")
    assert not any("< 5.0" in d for d in doctor.degraded), doctor.degraded


# ── /dev/kvm usability: existence is not enough for Kata/Firecracker ──────────
# The launcher's host_supports_kata gate is [[ -r /dev/kvm && -w /dev/kvm ]]; the
# doctor must mirror it or it reports a false-green on a host where /dev/kvm
# exists but the user isn't in the 'kvm' group.


def _drive_container_runtime(
    monkeypatch, *, runtime: str, kvm_usable: bool, kvm_exists: bool
) -> tuple[types.ModuleType, dict[str, str]]:
    """Run report_container_runtime with a stubbed bash probe whose only varying
    fact is host_supports_kata's verdict (line 5) and the runtime (line 1). Returns
    the module (for its `degraded` list) and a label→value map of the kv() rows."""
    doctor = load_doctor()
    # The container-runtime section is compose-only now; pin the backend so the
    # runtime branches under test are reached rather than the sbx skip row.
    monkeypatch.setenv("CLAUDE_GUARD_SANDBOX_BACKEND", "compose")
    monkeypatch.setattr(doctor, "section", lambda *a, **k: None)
    rows: dict[str, str] = {}
    monkeypatch.setattr(
        doctor, "kv", lambda label, value: rows.__setitem__(label, str(value))
    )
    monkeypatch.setattr(doctor, "degraded", [])
    monkeypatch.setattr(doctor.Path, "exists", lambda self: kvm_exists)
    # report_container_runtime now short-circuits when Docker is absent/unreachable
    # (so a stopped daemon isn't mislabeled a registration failure). Present a
    # reachable docker here so the runtime branches under test are reached without
    # depending on the runner actually having docker installed. orb stays absent so
    # the runsc-branch hint is unchanged.
    monkeypatch.setattr(
        doctor, "which", lambda name: "/usr/bin/docker" if name == "docker" else None
    )

    def fake_run_bash(script: str, timeout: float | None = None):
        out = json.dumps(
            {
                "runtime": runtime,
                "registered": True,
                "works": True,
                "executes": True,
                "kvm_usable": kvm_usable,
                "isolation": f"{runtime} isolation",
                "wsl_hint": "add [wsl2] nestedVirtualization=true …",
            }
        )
        return types.SimpleNamespace(stdout=out, returncode=0)

    monkeypatch.setattr(doctor, "run_bash", fake_run_bash)
    doctor.report_container_runtime()
    return doctor, rows


def test_kvm_present_and_usable_is_clean(monkeypatch) -> None:
    """Device present and rw-accessible: the existing 'available' row, no degrade."""
    doctor, rows = _drive_container_runtime(
        monkeypatch, runtime="kata-fc", kvm_usable=True, kvm_exists=True
    )
    assert rows["/dev/kvm"] == "present (hardware virtualization available)"
    assert not any("kata-fc but" in d for d in doctor.degraded), doctor.degraded


def test_kvm_present_but_not_usable_degrades(monkeypatch) -> None:
    """Device exists but the user lacks rw access (not in 'kvm' group): the row must
    flag inaccessibility and the kata degrade must fire naming the rw/group cause —
    the false-green this fix closes."""
    doctor, rows = _drive_container_runtime(
        monkeypatch, runtime="kata-fc", kvm_usable=False, kvm_exists=True
    )
    assert "not accessible to this user" in rows["/dev/kvm"]
    assert "'kvm' group" in rows["/dev/kvm"]
    degrade = next(d for d in doctor.degraded if "kata-fc but" in d)
    assert "readable+writable" in degrade and "'kvm' group" in degrade


def test_kvm_absent_degrades_with_absent_cause(monkeypatch) -> None:
    """No device at all: the row says absent and the kata degrade names absence."""
    doctor, rows = _drive_container_runtime(
        monkeypatch, runtime="kata-fc", kvm_usable=False, kvm_exists=False
    )
    assert rows["/dev/kvm"].startswith("absent (no KVM")
    degrade = next(d for d in doctor.degraded if "kata-fc but" in d)
    assert "/dev/kvm is absent" in degrade


def test_runtime_probe_timeout_is_reported_as_failure(monkeypatch) -> None:
    """A timed-out probe (returncode != 0) surfaces as the benign 'probe failed' row
    + unprotected entry — never a silent default past a wedged daemon."""
    doctor = load_doctor()
    monkeypatch.setenv("CLAUDE_GUARD_SANDBOX_BACKEND", "compose")
    monkeypatch.setattr(doctor, "section", lambda *a, **k: None)
    printed: list = []
    monkeypatch.setattr(doctor.errs, "print", lambda *a, **k: printed.append(a))
    monkeypatch.setattr(doctor, "unprotected", [])
    monkeypatch.setattr(
        doctor,
        "run_bash",
        lambda script, **k: types.SimpleNamespace(stdout="", returncode=124),
    )
    doctor.report_container_runtime()
    assert any("probe failed" in str(a) for a in printed), printed
    assert any("probe failed" in u for u in doctor.unprotected)
    assert not any("BROKEN" in u for u in doctor.unprotected), doctor.unprotected


def test_runtime_probe_non_json_is_reported_as_broken(monkeypatch) -> None:
    """A probe that exited 0 but emitted non-JSON garbage is a BROKEN install — a
    distinct, louder verdict than the timed-out 'probe failed' case. Conflating the
    two let a daemon spewing junk read as merely 'unverified'."""
    doctor = load_doctor()
    monkeypatch.setenv("CLAUDE_GUARD_SANDBOX_BACKEND", "compose")
    monkeypatch.setattr(doctor, "section", lambda *a, **k: None)
    printed: list = []
    monkeypatch.setattr(doctor.errs, "print", lambda *a, **k: printed.append(a))
    monkeypatch.setattr(doctor, "unprotected", [])
    monkeypatch.setattr(
        doctor,
        "run_bash",
        lambda script, **k: types.SimpleNamespace(
            stdout="not json at all", returncode=0
        ),
    )
    doctor.report_container_runtime()
    assert any("BROKEN" in str(a) for a in printed), printed
    assert any("BROKEN" in u for u in doctor.unprotected), doctor.unprotected
    assert not any("probe failed" in u for u in doctor.unprotected), doctor.unprotected


def test_probe_facts_distinguishes_three_outcomes() -> None:
    """probe_facts returns a dict for valid JSON, None for a probe that failed to run
    (returncode != 0), and the MALFORMED_PROBE sentinel for a probe that exited 0 but
    emitted non-JSON — the three states callers branch on. Exact-equality assertions."""
    doctor = load_doctor()

    failed = types.SimpleNamespace(stdout="", returncode=124)
    assert doctor.probe_facts(failed) is None

    # returncode != 0 wins even when stdout happens to be valid JSON: the probe did
    # not complete, so its (partial) output is not trustworthy facts.
    failed_with_json = types.SimpleNamespace(stdout='{"a": 1}', returncode=1)
    assert doctor.probe_facts(failed_with_json) is None

    valid = types.SimpleNamespace(stdout='{"a": 1, "b": "x"}', returncode=0)
    assert doctor.probe_facts(valid) == {"a": 1, "b": "x"}

    malformed = types.SimpleNamespace(stdout="not json at all", returncode=0)
    assert doctor.probe_facts(malformed) is doctor.MALFORMED_PROBE


def test_occupant_note_classifies_each_kind() -> None:
    """_occupant_note words each occupant kind distinctly: the protected 'in use …
    keep' must not bleed onto an orphan/spare/persistent, and only the real session
    carries 'keep'. Pure function — no docker needed."""
    doctor = load_doctor()
    base = {"subnet": "172.30.0.0/24", "name": "n", "project": "p", "pid": "42"}
    active = doctor._occupant_note({**base, "kind": "active"})
    assert "in use (pid 42) — keep" in active

    orphan = doctor._occupant_note({**base, "kind": "orphaned"})
    assert "leftover — auto-removed next launch" in orphan
    assert "keep" not in orphan

    spare = doctor._occupant_note({**base, "kind": "spare"})
    assert "idle spare — auto-cleaned" in spare
    assert "keep" not in spare

    persistent = doctor._occupant_note({**base, "kind": "persistent"})
    assert "kept (persistent session)" in persistent
    # The per-stack note no longer carries the teardown command — that moved to a
    # single combined line built by the caller (see test_claude_doctor.py).
    assert "docker compose" not in persistent

    # Containerless: just the bare location line, no classification tail.
    bare = doctor._occupant_note({**base, "kind": "containerless"})
    assert bare == "• 172.30.0.0/24  (n)"


def test_subnet_probe_failure_does_not_false_degrade_as_occupied(monkeypatch) -> None:
    """The invariant the JSON+probe_facts refactor protects: a failed/timed-out
    subnet probe must report the failure, NOT read as free=0 and claim "all subnets
    occupied" (the false degrade the old default-to-0 positional parse produced)."""
    doctor = load_doctor()
    monkeypatch.setenv("CLAUDE_GUARD_SANDBOX_BACKEND", "compose")
    monkeypatch.setattr(doctor, "section", lambda *a, **k: None)
    rows: dict = {}
    monkeypatch.setattr(
        doctor, "kv", lambda label, value: rows.__setitem__(label, str(value))
    )
    monkeypatch.setattr(doctor, "degraded", [])
    monkeypatch.setattr(doctor, "which", lambda name: "/usr/bin/docker")

    def fake_run_bash(script: str, timeout: float | None = None):
        # docker ps / volume inspect succeed; the subnet pool probe times out; the
        # occupants listing returns nothing.
        if "_sandbox_subnet" in script:
            return types.SimpleNamespace(stdout="", returncode=124)
        return types.SimpleNamespace(stdout="", returncode=0)

    monkeypatch.setattr(doctor, "run_bash", fake_run_bash)
    doctor.report_launch_preconditions()
    assert "probe failed" in rows["sandbox subnets"]
    assert not any("occupied" in d for d in doctor.degraded), doctor.degraded
    assert any("probe failed" in d for d in doctor.degraded), doctor.degraded


# ── print_verdict: bug-report footer ────────────────────────────────────────


def _run_print_verdict(monkeypatch, *, unprotected, degraded, error_boxes):
    """Drive print_verdict with the verdict-state globals forced, capturing every
    console.print line and swallowing its sys.exit."""
    doctor = load_doctor()
    doctor.unprotected = unprotected
    doctor.degraded = degraded
    doctor.error_boxes = error_boxes
    printed: list[str] = []
    monkeypatch.setattr(
        doctor.console,
        "print",
        lambda *a, **k: printed.append(" ".join(str(x) for x in a)),
    )
    with contextlib.suppress(SystemExit):
        doctor.print_verdict()
    return "\n".join(printed)


def test_bug_report_footer_suppressed_on_clean_verdict(monkeypatch) -> None:
    out = _run_print_verdict(monkeypatch, unprotected=[], degraded=[], error_boxes=[])
    assert "Found a bug?" not in out


def test_bug_report_footer_shown_when_unprotected(monkeypatch) -> None:
    out = _run_print_verdict(
        monkeypatch, unprotected=["something is wrong"], degraded=[], error_boxes=[]
    )
    assert "Found a bug?" in out


# ── int_env: defensive integer env-override parsing ─────────────────────────


def test_int_env_returns_default_when_unset(monkeypatch) -> None:
    doctor = load_doctor()
    monkeypatch.delenv("CLAUDE_GUARD_DOCTOR_TEST_INT", raising=False)
    assert doctor.int_env("CLAUDE_GUARD_DOCTOR_TEST_INT", 7) == 7


def test_int_env_parses_valid_override(monkeypatch) -> None:
    doctor = load_doctor()
    monkeypatch.setenv("CLAUDE_GUARD_DOCTOR_TEST_INT", "42")
    assert doctor.int_env("CLAUDE_GUARD_DOCTOR_TEST_INT", 7) == 42


def test_int_env_defaults_and_warns_on_non_integer(monkeypatch, capsys) -> None:
    """A malformed knob (e.g. DEVCONTAINER_APP_MEM_MB=8g, mirroring Docker's `8g`)
    degrades to the default with a warning instead of crashing the report with a
    ValueError traceback."""
    doctor = load_doctor()
    monkeypatch.setenv("CLAUDE_GUARD_DOCTOR_TEST_INT", "8g")
    assert doctor.int_env("CLAUDE_GUARD_DOCTOR_TEST_INT", 7) == 7
    assert "ignoring non-integer" in capsys.readouterr().err


# ── report_sandbox_claude_version: baked vs synced version visibility ────────


def _drive_sandbox_version(monkeypatch, probe_stdout: str):
    """Run report_sandbox_claude_version with a stubbed bash probe, capturing the
    rendered rows as (label, text) tuples. The probe emits the same tab-separated
    `pin\\tsync\\tau\\thost` the in-doctor snippet does."""
    doctor = load_doctor()
    monkeypatch.setenv("CLAUDE_GUARD_SANDBOX_BACKEND", "compose")
    rows: list[tuple[str, str]] = []
    monkeypatch.setattr(doctor, "section", lambda *a, **k: None)
    monkeypatch.setattr(
        doctor, "kv", lambda label, value: rows.append((label, str(value)))
    )
    monkeypatch.setattr(
        doctor,
        "kv_state",
        lambda label, ok, **k: rows.append((label, "on" if ok else "off")),
    )
    # Both file-existence guards (the lib and package.json) pass for these tests.
    monkeypatch.setattr(doctor.Path, "is_file", lambda self: True)
    monkeypatch.setattr(
        doctor,
        "run_bash",
        lambda script, timeout=None: types.SimpleNamespace(
            stdout=probe_stdout, returncode=0, stderr=""
        ),
    )
    doctor.report_sandbox_claude_version()
    return dict(rows)


def test_sandbox_version_shows_sync_when_newer(monkeypatch) -> None:
    """Auto-update on and a newer release: the section shows the baked pin and the
    version this launch will sync into the container."""
    rows = _drive_sandbox_version(monkeypatch, "2.1.168\t2.1.177\ton\t2.1.177\n")
    assert rows["auto-update"] == "on"
    assert rows["host CLI"] == "2.1.177"
    assert "baked 2.1.168" in rows["sandbox claude-code"]
    assert "syncs 2.1.177" in rows["sandbox claude-code"]


def test_sandbox_version_shows_baked_only_when_no_sync(monkeypatch) -> None:
    """No override (host on the pin, auto-update off): only the baked version, no
    sync — and no spurious host-CLI row when the probe reports none."""
    rows = _drive_sandbox_version(monkeypatch, "2.1.168\t\toff\t\n")
    assert rows["auto-update"] == "off"
    assert "host CLI" not in rows
    assert "baked 2.1.168 (no newer version to sync" in rows["sandbox claude-code"]


def test_sandbox_version_handles_unreadable_pin(monkeypatch) -> None:
    """An empty pin (jq couldn't read package.json) reports a clear can't-check note
    rather than rendering a bogus 'baked' line."""
    rows = _drive_sandbox_version(monkeypatch, "\t\toff\t\n")
    assert "cannot read the baked version pin" in rows["sandbox claude-code"]
    assert "auto-update" not in rows


def test_sandbox_version_missing_lib_is_noted(monkeypatch) -> None:
    """When claude-resolve.bash or package.json is absent, the section says so and
    never shells out."""
    doctor = load_doctor()
    monkeypatch.setenv("CLAUDE_GUARD_SANDBOX_BACKEND", "compose")
    rows: list[tuple[str, str]] = []
    monkeypatch.setattr(doctor, "section", lambda *a, **k: None)
    monkeypatch.setattr(
        doctor, "kv", lambda label, value: rows.append((label, str(value)))
    )
    monkeypatch.setattr(doctor.Path, "is_file", lambda self: False)

    def _boom(*a, **k):
        raise AssertionError("run_bash must not be called when inputs are missing")

    monkeypatch.setattr(doctor, "run_bash", _boom)
    doctor.report_sandbox_claude_version()
    assert "cannot check" in dict(rows)["sandbox claude-code"]


# ── selected_backend / report_backend: the wrapper's backend dispatch, mirrored ──
# The doctor must resolve CLAUDE_GUARD_SANDBOX_BACKEND exactly as the wrapper's
# `${CLAUDE_GUARD_SANDBOX_BACKEND:-sbx}` does (empty string falls to the default
# too), or the report describes a launch that would never happen.


def test_selected_backend_defaults_to_sbx(monkeypatch) -> None:
    doctor = load_doctor()
    monkeypatch.delenv("CLAUDE_GUARD_SANDBOX_BACKEND", raising=False)
    assert doctor.selected_backend() == "sbx"


def test_selected_backend_empty_value_falls_to_default(monkeypatch) -> None:
    """`:-` semantics: an empty value is the default, not a distinct backend."""
    doctor = load_doctor()
    monkeypatch.setenv("CLAUDE_GUARD_SANDBOX_BACKEND", "")
    assert doctor.selected_backend() == "sbx"


def test_selected_backend_env_override_and_passthrough(monkeypatch) -> None:
    doctor = load_doctor()
    monkeypatch.setenv("CLAUDE_GUARD_SANDBOX_BACKEND", "compose")
    assert doctor.selected_backend() == "compose"
    # An unknown value passes through unchanged so report_backend can name it.
    monkeypatch.setenv("CLAUDE_GUARD_SANDBOX_BACKEND", "bogus")
    assert doctor.selected_backend() == "bogus"


def _drive_backend(monkeypatch, backend: str | None):
    """Run report_backend with the env pinned (None = unset), capturing rows and
    the verdict lists."""
    doctor = load_doctor()
    if backend is None:
        monkeypatch.delenv("CLAUDE_GUARD_SANDBOX_BACKEND", raising=False)
    else:
        monkeypatch.setenv("CLAUDE_GUARD_SANDBOX_BACKEND", backend)
    monkeypatch.setattr(doctor, "section", lambda *a, **k: None)
    rows: dict[str, str] = {}
    monkeypatch.setattr(
        doctor, "kv", lambda label, value: rows.__setitem__(label, str(value))
    )
    monkeypatch.setattr(doctor, "unprotected", [])
    doctor.report_backend()
    return doctor, rows


def test_report_backend_default_names_sbx(monkeypatch) -> None:
    doctor, rows = _drive_backend(monkeypatch, None)
    assert rows["backend"].startswith("sbx")
    assert doctor.unprotected == []


def test_report_backend_compose_selected(monkeypatch) -> None:
    doctor, rows = _drive_backend(monkeypatch, "compose")
    assert rows["backend"].startswith("compose")
    assert doctor.unprotected == []


def test_report_backend_unknown_value_is_unprotected(monkeypatch) -> None:
    """An unrecognized backend makes the wrapper refuse every launch, so the doctor
    flags it UNPROTECTED with the wrapper's own wording."""
    doctor, rows = _drive_backend(monkeypatch, "bogus")
    assert "unknown CLAUDE_GUARD_SANDBOX_BACKEND 'bogus'" in rows["backend"]
    assert doctor.unprotected == [
        "unknown CLAUDE_GUARD_SANDBOX_BACKEND 'bogus' — every launch refuses "
        "to start; expected 'compose' or 'sbx'"
    ]


# ── report_sbx_backend: sbx CLI / KVM / squid / flattened verdicts ────────────


def _sbx_facts(**overrides) -> dict:
    """A fully healthy sbx probe result; override per-case."""
    facts = {
        "cli": True,
        "kvm": True,
        "flattened": False,
        "squid": True,
        "certgen": True,
        "version_ok": True,
        "version": "sbx 0.3.0",
        "login": "ok",
    }
    facts.update(overrides)
    return facts


def _drive_sbx_backend(
    monkeypatch,
    facts: dict | None = None,
    *,
    backend: str | None = "sbx",
    rc: int = 0,
    stdout: str | None = None,
):
    """Run report_sbx_backend with the backend env pinned (None = unset → the sbx
    default) and the bash probe stubbed to emit `facts` as JSON (or a raw `stdout`
    with returncode `rc`). Returns (module, kv-rows, errs-printed lines)."""
    doctor = load_doctor()
    if backend is None:
        monkeypatch.delenv("CLAUDE_GUARD_SANDBOX_BACKEND", raising=False)
    else:
        monkeypatch.setenv("CLAUDE_GUARD_SANDBOX_BACKEND", backend)
    monkeypatch.setattr(doctor, "section", lambda *a, **k: None)
    rows: dict[str, str] = {}
    monkeypatch.setattr(
        doctor, "kv", lambda label, value: rows.__setitem__(label, str(value))
    )
    printed: list[str] = []
    monkeypatch.setattr(
        doctor.errs, "print", lambda *a, **k: printed.append(" ".join(map(str, a)))
    )
    monkeypatch.setattr(doctor, "unprotected", [])
    monkeypatch.setattr(doctor, "degraded", [])
    out = stdout if stdout is not None else json.dumps(facts)
    monkeypatch.setattr(
        doctor,
        "run_bash",
        lambda script, timeout=None: types.SimpleNamespace(stdout=out, returncode=rc),
    )
    doctor.report_sbx_backend()
    return doctor, rows, printed


def test_sbx_all_healthy_under_default_backend(monkeypatch) -> None:
    """Env unset (the sbx default): a healthy probe yields all-green rows and an
    untouched verdict."""
    doctor, rows, _ = _drive_sbx_backend(monkeypatch, _sbx_facts(), backend=None)
    assert "working (sbx 0.3.0)" in rows["sbx CLI"]
    assert "hardware virtualization available" in rows["microVM support"]
    assert "logged in ('sbx ls' succeeds)" in rows["login"]
    assert "squid + security_file_certgen found" in rows["read-only filter"]
    assert doctor.unprotected == []
    assert doctor.degraded == []


def test_sbx_version_string_absent_still_green(monkeypatch) -> None:
    """`sbx version` succeeded but printed nothing: green without a bogus '()'."""
    doctor, rows, _ = _drive_sbx_backend(monkeypatch, _sbx_facts(version=""))
    assert "working" in rows["sbx CLI"]
    assert "()" not in rows["sbx CLI"]
    assert doctor.unprotected == []


def test_sbx_cli_missing_is_unprotected_with_install_hint(monkeypatch) -> None:
    doctor, rows, _ = _drive_sbx_backend(
        monkeypatch, _sbx_facts(cli=False, version_ok=False, login="not-checked")
    )
    assert "not found on PATH" in rows["sbx CLI"]
    reason = next(u for u in doctor.unprotected if "'sbx' CLI is not installed" in u)
    assert "docker-sbx" in reason and "sbx login" in reason
    assert "not checked" in rows["login"]


def test_sbx_version_failure_is_unprotected(monkeypatch) -> None:
    """CLI present but `sbx version` fails: the same actionable hint the launcher's
    preflight prints (kvm group / sbx login)."""
    doctor, rows, _ = _drive_sbx_backend(
        monkeypatch, _sbx_facts(version_ok=False, version="", login="not-checked")
    )
    assert "'sbx version' fails" in rows["sbx CLI"]
    reason = next(u for u in doctor.unprotected if "'sbx version' failed" in u)
    assert "kvm group" in reason and "sbx login" in reason


def test_sbx_kvm_missing_is_unprotected(monkeypatch) -> None:
    doctor, rows, _ = _drive_sbx_backend(monkeypatch, _sbx_facts(kvm=False))
    assert "NO hardware virtualization" in rows["microVM support"]
    reason = next(u for u in doctor.unprotected if "hardware virtualization" in u)
    assert "no software fallback" in reason
    assert "nested virtualization" in reason


def test_sbx_squid_missing_is_unprotected_naming_flattened_optout(monkeypatch) -> None:
    """Missing squid fails the doctor (a launch refuses to start, fail-closed) and
    the reason must name CLAUDE_GUARD_SBX_ALLOW_FLATTENED=1 as a security trade-off,
    never as a neutral workaround."""
    doctor, rows, _ = _drive_sbx_backend(
        monkeypatch, _sbx_facts(squid=False, certgen=False)
    )
    assert "'squid' not found" in rows["read-only filter"]
    reason = next(u for u in doctor.unprotected if "needs 'squid'" in u)
    assert "CLAUDE_GUARD_SBX_ALLOW_FLATTENED=1" in reason
    assert "security trade-off" in reason
    assert "writable" in reason
    # The squid-missing branch wins; the certgen message must not double-fire.
    assert not any("security_file_certgen" in u for u in doctor.unprotected)


def test_sbx_certgen_missing_is_unprotected(monkeypatch) -> None:
    """squid present but without its ssl-bump helper: the distinct certgen message
    (an ssl-bump-less squid build), still naming the flattened opt-out."""
    doctor, rows, _ = _drive_sbx_backend(monkeypatch, _sbx_facts(certgen=False))
    assert "security_file_certgen" in rows["read-only filter"]
    reason = next(u for u in doctor.unprotected if "security_file_certgen" in u)
    assert "ssl-bump" in reason
    assert "CLAUDE_GUARD_SBX_ALLOW_FLATTENED=1" in reason


def test_sbx_flattened_optin_degrades_not_unprotected(monkeypatch) -> None:
    """The explicit flattened opt-in: a launch proceeds (no unprotected) but the
    lost read-only tier is a real missing protection — DEGRADED, loudly."""
    doctor, rows, _ = _drive_sbx_backend(
        monkeypatch, _sbx_facts(flattened=True, squid=False, certgen=False)
    )
    assert "OFF by choice" in rows["read-only filter"]
    assert doctor.unprotected == []
    reason = next(
        d for d in doctor.degraded if "CLAUDE_GUARD_SBX_ALLOW_FLATTENED=1" in d
    )
    assert "writable" in reason and "upload-capable" in reason


def test_sbx_login_failed_degrades(monkeypatch) -> None:
    """`sbx ls` failing is an honest indirect signal (likely not logged in): a
    degrade with the 'sbx login' action, never unprotected."""
    doctor, rows, _ = _drive_sbx_backend(monkeypatch, _sbx_facts(login="failed"))
    assert "'sbx ls' fails" in rows["login"]
    assert doctor.unprotected == []
    assert any("'sbx ls' failed" in d and "sbx login" in d for d in doctor.degraded)


def test_sbx_probe_failure_is_reported_not_defaulted(monkeypatch) -> None:
    """A timed-out/failed probe (returncode != 0) reports the failure — never
    silently defaults facts past a wedged host."""
    doctor, _rows, printed = _drive_sbx_backend(monkeypatch, rc=124, stdout="")
    assert any("probe failed" in p for p in printed), printed
    assert any("sbx probe failed" in u for u in doctor.unprotected)
    assert not any("BROKEN" in u for u in doctor.unprotected)


def test_sbx_probe_non_json_is_broken(monkeypatch) -> None:
    """MALFORMED_PROBE (exit 0, junk stdout) is the louder BROKEN verdict, distinct
    from the benign probe-failed case."""
    doctor, _rows, printed = _drive_sbx_backend(monkeypatch, stdout="junk not json")
    assert any("BROKEN" in p for p in printed), printed
    assert any("BROKEN" in u for u in doctor.unprotected)
    assert not any("probe failed" in u for u in doctor.unprotected)


def test_sbx_section_skips_under_compose_without_probing(monkeypatch) -> None:
    doctor = load_doctor()
    monkeypatch.setenv("CLAUDE_GUARD_SANDBOX_BACKEND", "compose")
    monkeypatch.setattr(doctor, "section", lambda *a, **k: None)
    rows: dict[str, str] = {}
    monkeypatch.setattr(
        doctor, "kv", lambda label, value: rows.__setitem__(label, str(value))
    )
    monkeypatch.setattr(doctor, "unprotected", [])
    monkeypatch.setattr(doctor, "degraded", [])

    def _boom(*a, **k):
        raise AssertionError("the sbx probe must not run under the compose backend")

    monkeypatch.setattr(doctor, "run_bash", _boom)
    doctor.report_sbx_backend()
    assert rows["sbx backend"] == "not selected — sessions use the compose backend"
    assert doctor.unprotected == [] and doctor.degraded == []


def test_sbx_section_skips_under_unknown_backend(monkeypatch) -> None:
    """An unknown backend is report_backend's failure to flag; the sbx section
    skips quietly instead of piling on misleading sbx verdicts."""
    doctor = load_doctor()
    monkeypatch.setenv("CLAUDE_GUARD_SANDBOX_BACKEND", "bogus")
    monkeypatch.setattr(doctor, "section", lambda *a, **k: None)
    rows: dict[str, str] = {}
    monkeypatch.setattr(
        doctor, "kv", lambda label, value: rows.__setitem__(label, str(value))
    )
    monkeypatch.setattr(doctor, "unprotected", [])
    monkeypatch.setattr(doctor, "degraded", [])

    def _boom(*a, **k):
        raise AssertionError("the sbx probe must not run under an unknown backend")

    monkeypatch.setattr(doctor, "run_bash", _boom)
    doctor.report_sbx_backend()
    assert "not recognized" in rows["sbx backend"]
    assert doctor.unprotected == [] and doctor.degraded == []


def test_sbx_missing_lib_is_broken_install(monkeypatch) -> None:
    """A missing sbx lib is a broken install (UNPROTECTED), reported before any
    probe runs."""
    doctor = load_doctor()
    monkeypatch.setenv("CLAUDE_GUARD_SANDBOX_BACKEND", "sbx")
    monkeypatch.setattr(doctor, "section", lambda *a, **k: None)
    printed: list[str] = []
    monkeypatch.setattr(
        doctor.errs, "print", lambda *a, **k: printed.append(" ".join(map(str, a)))
    )
    monkeypatch.setattr(doctor, "unprotected", [])
    monkeypatch.setattr(doctor.Path, "is_file", lambda self: False)

    def _boom(*a, **k):
        raise AssertionError("the probe must not run when a lib is missing")

    monkeypatch.setattr(doctor, "run_bash", _boom)
    doctor.report_sbx_backend()
    assert doctor.unprotected == ["sbx-detect.bash missing (broken install)"]
    assert any("sbx-detect.bash not found" in p for p in printed), printed


# ── compose-only sections: skipped (not failed) when compose isn't selected ──
# One case per gated reporter, driven from the list itself so a reporter added to
# the gate without a test fails to compile a case.

_COMPOSE_ONLY_REPORTERS = (
    ("report_container_runtime", "runtime"),
    ("report_resources", "resources"),
    ("report_docker_cli_plugins", "plugins"),
    ("report_launch_preconditions", "preconditions"),
    ("report_prebuilt_image", "image status"),
    ("report_sandbox_claude_version", "sandbox claude-code"),
    ("report_launch_plan", "plan"),
)


def _assert_reporter_skips_under_sbx(monkeypatch, reporter: str, label: str) -> None:
    """One gated reporter under the sbx backend: exactly the one-line dim skip row,
    no probing, verdict untouched."""
    doctor = load_doctor()
    monkeypatch.setenv("CLAUDE_GUARD_SANDBOX_BACKEND", "sbx")
    monkeypatch.setattr(doctor, "section", lambda *a, **k: None)
    rows: dict[str, str] = {}
    monkeypatch.setattr(
        doctor, "kv", lambda key, value: rows.__setitem__(key, str(value))
    )
    monkeypatch.setattr(doctor, "unprotected", [])
    monkeypatch.setattr(doctor, "degraded", [])

    def _boom(*a, **k):
        raise AssertionError(f"{reporter} must not probe under the sbx backend")

    monkeypatch.setattr(doctor, "run_bash", _boom)
    monkeypatch.setattr(doctor, "which", _boom)
    getattr(doctor, reporter)()
    assert rows == {label: "skipped — compose backend not selected"}, reporter
    assert doctor.unprotected == [] and doctor.degraded == [], reporter


def test_compose_sections_skip_under_sbx_backend(monkeypatch) -> None:
    """Every compose-only section skips under the sbx backend — one case per gated
    reporter, driven from the list."""
    for reporter, label in _COMPOSE_ONLY_REPORTERS:
        _assert_reporter_skips_under_sbx(monkeypatch, reporter, label)


def test_compose_section_gate_open_under_compose(monkeypatch) -> None:
    """Non-vacuity of the gate: with compose selected, a gated section proceeds past
    the skip into its real checks (here: the docker-missing n/a row)."""
    doctor = load_doctor()
    monkeypatch.setenv("CLAUDE_GUARD_SANDBOX_BACKEND", "compose")
    monkeypatch.setattr(doctor, "section", lambda *a, **k: None)
    rows: dict[str, str] = {}
    monkeypatch.setattr(
        doctor, "kv", lambda label, value: rows.__setitem__(label, str(value))
    )
    monkeypatch.setattr(doctor, "which", lambda name: None)
    doctor.report_docker_cli_plugins()
    assert rows["plugins"] == "n/a (docker not installed)"


def test_skip_unless_compose_exact_row(monkeypatch) -> None:
    doctor = load_doctor()
    rows: dict[str, str] = {}
    monkeypatch.setattr(
        doctor, "kv", lambda label, value: rows.__setitem__(label, str(value))
    )
    monkeypatch.setenv("CLAUDE_GUARD_SANDBOX_BACKEND", "sbx")
    assert doctor.skip_unless_compose("x") is True
    assert rows == {"x": "skipped — compose backend not selected"}
    rows.clear()
    monkeypatch.setenv("CLAUDE_GUARD_SANDBOX_BACKEND", "compose")
    assert doctor.skip_unless_compose("x") is False
    assert rows == {}


# ── report_required_tools: devcontainer is critical only for compose ─────────


def _drive_tools_missing_devcontainer(monkeypatch, backend: str):
    """Run report_required_tools with every tool resolving except devcontainer."""
    doctor = load_doctor()
    monkeypatch.setenv("CLAUDE_GUARD_SANDBOX_BACKEND", backend)
    monkeypatch.setattr(doctor, "section", lambda *a, **k: None)
    monkeypatch.setattr(doctor.console, "print", lambda *a, **k: None)
    monkeypatch.setattr(
        doctor,
        "which",
        lambda name: None if name == "devcontainer" else f"/usr/bin/{name}",
    )
    monkeypatch.setattr(
        doctor,
        "run_bash",
        lambda script, **k: types.SimpleNamespace(stdout="5.2", returncode=0),
    )
    monkeypatch.setattr(doctor, "unprotected", [])
    monkeypatch.setattr(doctor, "degraded", [])
    doctor.report_required_tools()
    return doctor


def test_missing_devcontainer_is_unprotected_under_compose(monkeypatch) -> None:
    doctor = _drive_tools_missing_devcontainer(monkeypatch, "compose")
    assert doctor.unprotected == [
        "devcontainer not on PATH (sandboxed launch cannot start)"
    ]


def test_missing_devcontainer_is_ignored_under_sbx(monkeypatch) -> None:
    """The sbx backend launches microVMs without the devcontainer CLI, so its
    absence must not fail the doctor there."""
    doctor = _drive_tools_missing_devcontainer(monkeypatch, "sbx")
    assert doctor.unprotected == []
    assert doctor.degraded == []
