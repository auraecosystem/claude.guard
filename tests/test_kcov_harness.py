"""Unit tests for the kcov harness logic (interceptor + gate helpers).

These decide whether the bash-coverage CI gate passes, so they are tested in
process rather than only exercised end-to-end by run-kcov.sh.
"""

import pytest

from tests import _kcov, kcov_gate
from tests._helpers import REPO_ROOT


def test_wrap_argv_wraps_enrolled_script(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SCCD_KCOV_OUT", str(tmp_path))
    enrolled = str(REPO_ROOT / _kcov.KCOV_ENROLLED[0])
    wrapped = _kcov.wrap_argv([enrolled, "--flag"])
    assert wrapped[0] == "kcov"
    assert "--bash-method=DEBUG" in wrapped
    assert wrapped[-2:] == [enrolled, "--flag"]
    # The exclusion flags must be present so kcov-ignore markers are honored.
    assert any(a.startswith("--exclude-region=") for a in wrapped)
    assert any(a.startswith("--exclude-line=") for a in wrapped)


def test_wrap_argv_passes_through_non_enrolled(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SCCD_KCOV_OUT", str(tmp_path))
    argv = ["/usr/bin/git", "status"]
    assert _kcov.wrap_argv(argv) is argv


@pytest.mark.parametrize("argv", ["a string", [], None])
def test_wrap_argv_ignores_non_list_argv(monkeypatch, tmp_path, argv) -> None:
    monkeypatch.setenv("SCCD_KCOV_OUT", str(tmp_path))
    assert _kcov.wrap_argv(argv) is argv


def test_install_is_noop_without_env(monkeypatch) -> None:
    monkeypatch.delenv("SCCD_KCOV_OUT", raising=False)
    import subprocess

    before = subprocess.run
    _kcov.install()
    assert subprocess.run is before  # untouched when the env var is absent


@pytest.mark.parametrize(
    "nums,expected",
    [
        ([5], "5"),
        ([1, 2, 3], "1-3"),
        ([1, 3, 4, 5, 9], "1, 3-5, 9"),
        ([2, 4, 6], "2, 4, 6"),
    ],
)
def test_ranges(nums, expected) -> None:
    assert kcov_gate._ranges(nums) == expected


def test_relpath_normalizes_absolute_and_relative() -> None:
    assert (
        kcov_gate._relpath(str(REPO_ROOT / "bin" / "claude-guard"))
        == "bin/claude-guard"
    )
    assert kcov_gate._relpath("bin/claude-guard") == "bin/claude-guard"
