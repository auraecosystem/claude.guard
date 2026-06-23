"""The sandbox image must bake the Python launcher (uv) the guardrails need.

session-setup.sh drives every Python provisioning step through uv — `uv sync`
builds the project .venv (the lint/test toolchain AND the detect_secrets runtime
dep behind the redact-secrets.py PostToolUse hook), and `uv tool install`
provisions ruff/zizmor/pre-commit. Each of those calls is gated behind
`command -v uv` and is a SILENT no-op when uv is absent. The image bakes
python3/pip but historically not uv, so a guarded repo running in this image
(e.g. a foreign workspace) got no Python tooling and `uv sync` never ran —
exactly the friction this asserts against. Baking uv (a pinned wheel installed
alongside detect-secrets) also means provisioning works under restricted
outgoing access, with no per-session network bootstrap.

This is a presence+pin check, not a drift guard: uv is pinned only here, so
there is nothing for it to drift against.
"""

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(
    subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip()
)

DOCKERFILE = REPO_ROOT / ".devcontainer" / "Dockerfile"


def test_image_bakes_uv_pinned() -> None:
    """The Dockerfile installs uv pinned to an exact version (==X.Y.Z), via the
    same `pip3 install --break-system-packages` step that bakes detect-secrets."""
    text = DOCKERFILE.read_text()
    m = re.search(r"pip3 install --break-system-packages[^\n]*", text)
    assert m, "Dockerfile is missing the baked pip3 install step"
    pip_line = m.group(0)
    assert re.search(r"'uv==\d+\.\d+\.\d+'", pip_line), (
        f"Dockerfile must bake uv at a pinned version; got: {pip_line!r}"
    )
    # Baked beside detect-secrets so a guarded repo's redact-secrets.py dep and its
    # provisioning launcher are provisioned by the same image layer.
    assert "detect-secrets==" in pip_line
