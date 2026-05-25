"""Tests for hooks/monitor-dispatch.bash."""

from __future__ import annotations

import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DISPATCH = REPO_ROOT / "hooks" / "monitor-dispatch.bash"

_ENVELOPE = (
    '{"tool_name":"Bash","tool_input":{"command":"echo hi"},"session_id":"test-123"}'
)


def _run(env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(DISPATCH)],
        input=_ENVELOPE,
        env=env,
        capture_output=True,
        text=True,
    )


def test_warns_once_when_socket_missing(tmp_path: Path) -> None:
    """First call with no socket must emit an ASK decision so the user knows
    monitoring is inactive; subsequent calls in the same session must be silent."""
    warned = tmp_path / "claude-monitor-no-socket"
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "TMPDIR": str(tmp_path)}
    # First call — socket absent, no prior warning file.
    result = _run(env=env)
    assert result.returncode == 0
    import json

    payload = json.loads(result.stdout)
    assert payload["hookSpecificOutput"]["permissionDecision"] == "ask"
    assert "unmonitored" in payload["hookSpecificOutput"]["permissionDecisionReason"]
    assert warned.exists(), "warning sentinel file must be written"

    # Second call in same session — warning already sent, should be silent.
    result2 = _run(env=env)
    assert result2.returncode == 0
    assert result2.stdout.strip() == "", "subsequent call must produce no output"


def test_socket_present_skips_warning(tmp_path: Path) -> None:
    """When the socket exists the script must take the curl branch, not emit
    the fallback warning. We inject the socket path via a wrapper script so
    we don't need a real monitor socket at the hard-coded system path."""
    import socket
    import threading

    sock_path = tmp_path / "monitor.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(sock_path))
    server.listen(1)

    def _accept_and_close() -> None:
        try:
            conn, _ = server.accept()
            conn.close()
        except OSError:
            pass

    t = threading.Thread(target=_accept_and_close, daemon=True)
    t.start()

    # Rewrite the hard-coded socket path so we can inject a test socket without
    # needing the real system path.  sed-style replacement is more robust than
    # prepending a variable assignment because the script reassigns SOCKET in
    # its body, which would overwrite the prepended value.
    wrapper = DISPATCH.read_text().replace(
        'SOCKET="/var/run/monitor/monitor.sock"',
        f'SOCKET="{sock_path}"',
    )

    env = {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "HOME": str(tmp_path),
        "TMPDIR": str(tmp_path),
    }
    result = subprocess.run(
        ["bash", "-c", wrapper],
        input=_ENVELOPE,
        env=env,
        capture_output=True,
        text=True,
    )
    t.join(timeout=5)
    server.close()

    # The script took the curl branch (curl fails against the stub, which is
    # fine) — crucially, no warning payload must appear in stdout.
    assert "unmonitored" not in result.stdout
    assert "unmonitored" not in result.stderr
    # Sentinel file must NOT be written — socket was found.
    assert not (tmp_path / "claude-monitor-no-socket").exists()
