"""Tests for .github/scripts/check-claude-model.py — the CI lint that requires
every anthropics/claude-code-action step to pin an explicit --model in claude_args.

Imports the module by path and drives check_file / main directly so every branch
is covered: clean steps, missing --model, opted-out steps, non-dict YAML shapes,
and non-action steps are all exercised.
"""

import importlib.util
from pathlib import Path

SRC = (
    Path(__file__).resolve().parent.parent
    / ".github"
    / "scripts"
    / "check-claude-model.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("check_claude_model", SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ccm = _load()


def _write(dirpath: Path, name: str, body: str) -> Path:
    dirpath.mkdir(parents=True, exist_ok=True)
    path = dirpath / name
    path.write_text(body)
    return path


ACTION = ccm.ACTION

# ── helpers ──────────────────────────────────────────────────────────────────

_USES_WITH_MODEL = f"""\
jobs:
  x:
    steps:
      - uses: {ACTION}@abc123 # v1
        with:
          claude_args: "--model claude-haiku-4-5 --allowedTools Bash"
"""

_USES_WITHOUT_MODEL = f"""\
jobs:
  x:
    steps:
      - uses: {ACTION}@abc123 # v1
        with:
          claude_args: "--allowedTools Bash"
"""

_USES_NO_CLAUDE_ARGS = f"""\
jobs:
  x:
    steps:
      - uses: {ACTION}@abc123 # v1
        with:
          anthropic_api_key: "***"
"""

_USES_NULL_CLAUDE_ARGS = f"""\
jobs:
  x:
    steps:
      - uses: {ACTION}@abc123 # v1
        with:
          claude_args: null
"""

_USES_OPTED_OUT = f"""\
jobs:
  x:
    steps:
      - uses: {ACTION}@abc123 # v1 # {ccm.OPT_OUT}
        with:
          claude_args: "--allowedTools Bash"
"""

_USES_OTHER_ACTION = """\
jobs:
  x:
    steps:
      - uses: actions/checkout@abc123
"""

_NON_DICT_DOC = "- item1\n- item2\n"

_NON_DICT_JOBS = "jobs: scalar\n"

_NON_DICT_JOB = "jobs:\n  x: scalar\n"

_NON_DICT_STEP = f"""\
jobs:
  x:
    steps:
      - scalar_step
      - uses: {ACTION}@abc123 # v1
"""

_NULL_USES = f"""\
jobs:
  x:
    steps:
      - uses: null
      - uses: {ACTION}@abc123 # v1
"""


# ── check_file ────────────────────────────────────────────────────────────────


def test_check_file_passes_with_model(tmp_path):
    assert ccm.check_file(_write(tmp_path, "wf.yaml", _USES_WITH_MODEL)) == []


def test_check_file_flags_missing_model(tmp_path):
    violations = ccm.check_file(_write(tmp_path, "wf.yaml", _USES_WITHOUT_MODEL))
    assert len(violations) == 1
    lineno, msg = violations[0]
    assert lineno == 4
    assert ACTION in msg and "--model" in msg


def test_check_file_flags_no_claude_args(tmp_path):
    violations = ccm.check_file(_write(tmp_path, "wf.yaml", _USES_NO_CLAUDE_ARGS))
    assert len(violations) == 1
    assert "--model" in violations[0][1]


def test_check_file_flags_null_claude_args(tmp_path):
    # `claude_args: null` → the `or ""` coercion must still trigger a violation.
    violations = ccm.check_file(_write(tmp_path, "wf.yaml", _USES_NULL_CLAUDE_ARGS))
    assert len(violations) == 1


def test_check_file_respects_opt_out(tmp_path):
    assert ccm.check_file(_write(tmp_path, "wf.yaml", _USES_OPTED_OUT)) == []


def test_check_file_ignores_other_actions(tmp_path):
    assert ccm.check_file(_write(tmp_path, "wf.yaml", _USES_OTHER_ACTION)) == []


def test_check_file_ignores_non_dict_document(tmp_path):
    assert ccm.check_file(_write(tmp_path, "wf.yaml", _NON_DICT_DOC)) == []


def test_check_file_ignores_non_dict_jobs(tmp_path):
    assert ccm.check_file(_write(tmp_path, "wf.yaml", _NON_DICT_JOBS)) == []


def test_check_file_ignores_non_dict_job(tmp_path):
    assert ccm.check_file(_write(tmp_path, "wf.yaml", _NON_DICT_JOB)) == []


def test_check_file_ignores_non_dict_step(tmp_path):
    # The scalar step is skipped; the claude-code-action step (no claude_args) fires.
    violations = ccm.check_file(_write(tmp_path, "wf.yaml", _NON_DICT_STEP))
    assert len(violations) == 1


def test_check_file_ignores_null_uses(tmp_path):
    # `uses: null` is not a str → skipped; the claude-code-action step (no args) fires.
    violations = ccm.check_file(_write(tmp_path, "wf.yaml", _NULL_USES))
    assert len(violations) == 1


def test_check_file_uses_line_1_when_action_not_in_text(tmp_path):
    # A YAML unicode escape resolves to the action string, but the literal bytes
    # ("\\u0040" vs "@") differ — the for loop exhausts all lines, leaving uses_lineno=1.
    body = 'jobs:\n  j:\n    steps:\n      - uses: "anthropics/claude-code-action\\u0040abc"\n'
    violations = ccm.check_file(_write(tmp_path, "wf.yaml", body))
    assert len(violations) == 1
    assert violations[0][0] == 1  # defaulted to line 1


# ── main ─────────────────────────────────────────────────────────────────────


def _point_at(tmp_path, monkeypatch):
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(ccm, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(ccm, "WORKFLOWS_DIR", wf)
    return wf


def test_main_returns_zero_when_clean(tmp_path, monkeypatch, capsys):
    wf = _point_at(tmp_path, monkeypatch)
    _write(wf, "ok.yaml", _USES_WITH_MODEL)
    assert ccm.main() == 0
    assert "ERROR" not in capsys.readouterr().out


def test_main_reports_and_fails_on_violation(tmp_path, monkeypatch, capsys):
    wf = _point_at(tmp_path, monkeypatch)
    _write(wf, "bad.yaml", _USES_WITHOUT_MODEL)
    _write(wf, "ok.yaml", _USES_WITH_MODEL)
    assert ccm.main() == 1
    out = capsys.readouterr().out
    assert "::error file=.github/workflows/bad.yaml,line=4::" in out
    assert "1 claude-code-action step(s)" in out
