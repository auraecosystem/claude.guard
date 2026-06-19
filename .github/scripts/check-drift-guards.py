#!/usr/bin/env python3
"""Fail if a test that calls itself a drift guard lacks a justification marker.

CLAUDE.md's doctrine is "prefer SSOT over drift guards": a drift guard — a test
that reads two duplicated sources and asserts they agree — is a smell unless a
true single source of truth is genuinely infeasible (an external/upstream value
you don't control, a hand-stamped trace point a generator can't place). This
lint makes that judgement auditable: any test whose name or docstring describes
itself with drift-guard intent ("drift guard", "can't drift", "must stay in
sync", …) MUST carry

    @pytest.mark.drift_guard("why a true SSOT isn't feasible here")

with a non-empty justification. The marker is the deliberate record that the
author considered SSOT and rejected it for a stated reason — review then checks
the reason, not the mere existence of the guard.

Detection is by convention, not proof: a guard written without the intent
phrasing slips through, exactly like the repo's other heuristic check-* lints.
The phrasing is what an author writes on purpose when authoring a guard, so the
lint catches the cases that matter while staying near-zero false positive.
"""

import ast
import re
import sys
from pathlib import Path

# Phrases that express guard INTENT — the author is asserting two sources can't
# diverge — rather than merely mentioning the word "drift" (which a test of
# drift-detection tooling, e.g. test_main_check_mode_detects_drift, also does).
_GUARD_PATTERNS = (
    r"drift[- ]guard",
    r"anti[- ]?drift",
    r"can't drift",
    r"cannot drift",
    r"never drift",
    r"won't drift",
    r"can't diverge",
    r"never diverge",
    r"must (?:stay|remain) in sync",
)
_GUARD_RE = re.compile("|".join(_GUARD_PATTERNS), re.IGNORECASE)

_MARKER = "drift_guard"


def _is_drift_guard(name: str, docstring: str) -> bool:
    """A test reads as a drift guard if its name (underscores read as spaces) or
    its docstring uses guard-intent phrasing."""
    return bool(_GUARD_RE.search(name.replace("_", " ")) or _GUARD_RE.search(docstring))


def _justification(decorator: ast.expr) -> str | None:
    """The non-empty justification string of a @pytest.mark.drift_guard(...) call,
    or None if this decorator is not that marker / carries no string reason."""
    if not isinstance(decorator, ast.Call):
        return None
    func = decorator.func
    if not (isinstance(func, ast.Attribute) and func.attr == _MARKER):
        return None
    if not decorator.args:
        return None
    arg = decorator.args[0]
    if (
        isinstance(arg, ast.Constant)
        and isinstance(arg.value, str)
        and arg.value.strip()
    ):
        return arg.value
    return None


def check_file(path: Path) -> list[tuple[int, str]]:
    """(lineno, function name) for every test in `path` that reads as a drift
    guard but lacks a justified @pytest.mark.drift_guard marker."""
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        print(f"{path}: cannot read file — {e}", file=sys.stderr)
        return []
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return []

    violations: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if not node.name.startswith("test_"):
            continue
        if not _is_drift_guard(node.name, ast.get_docstring(node) or ""):
            continue
        if any(_justification(dec) for dec in node.decorator_list):
            continue
        violations.append((node.lineno, node.name))
    return violations


def main() -> int:
    rc = 0
    for arg in sys.argv[1:]:
        path = Path(arg)
        for lineno, name in check_file(path):
            print(
                f"{path}:{lineno}: drift guard {name!r} lacks a justification — add "
                f'@pytest.mark.{_MARKER}("why a true SSOT is infeasible") '
                "(see CLAUDE.md § Prefer SSOT over drift guards)."
            )
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
