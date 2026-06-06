"""Guard the host-executed bash wrappers against GNU-only flags.

bin/claude-guard and its siblings run on the *host*, which on a macOS/Colima
setup ships BSD coreutils, not GNU. Their pytest + kcov suites run only on the
Linux CI runner, where GNU `tail`/`sort` accept flags BSD lacks — so a
GNU-ism like `tail -zn +11` executes cleanly in CI yet dies with
`tail: invalid option -- z` on a user's Mac. This static lint runs on the same
Linux runner and fails fast on the construct, since no macOS job exercises the
launch path.
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# BSD `tail` has no zero-delimited mode: -z / --zero-terminated are GNU-only.
# Match a `tail` invocation whose first flag token carries a `z` (covers -z,
# -zn, -nz) or the long form. `\btail` avoids matching words like "retail", and
# requiring the flag right after `tail ` keeps prose mentioning the flag (e.g.
# a comment explaining this very pitfall) from tripping it.
_GNU_TAIL = re.compile(r"\btail\s+(?:-[A-Za-z]*z|--zero-terminated)")


def _host_shell_scripts() -> list[Path]:
    """Every host-run shell script under bin/ (shebang names sh or bash)."""
    scripts = []
    for path in sorted(REPO.glob("bin/**/*")):
        if not path.is_file():
            continue
        first = path.read_text(errors="replace").splitlines()[:1]
        if (
            first
            and first[0].startswith("#!")
            and re.search(r"\b(?:ba)?sh\b", first[0])
        ):
            scripts.append(path)
    return scripts


def test_no_gnu_only_tail_z_in_host_scripts() -> None:
    """No host wrapper may use GNU-only `tail -z`: it aborts on a BSD host."""
    offenders = []
    for script in _host_shell_scripts():
        for lineno, line in enumerate(script.read_text().splitlines(), 1):
            if _GNU_TAIL.search(line):
                rel = script.relative_to(REPO)
                offenders.append(f"{rel}:{lineno}: {line.strip()}")
    assert not offenders, (
        "GNU-only `tail -z` runs on the host but BSD tail (macOS/Colima) rejects "
        "it. Use a portable pipeline (e.g. `find | sort -r | tail -n +N`):\n"
        + "\n".join(offenders)
    )
