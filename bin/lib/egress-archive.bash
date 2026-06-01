# shellcheck shell=bash
# Snapshot the squid egress access log out of its firewall-only volume onto the
# host. The volume lives on the firewall container alone — the app never mounts
# it — so without an archive helper, `docker compose down -v` (or a manual
# volume rm) would erase the only tamper-resistant record of outbound traffic.
#
# Mirrors bin/lib/audit-archive.bash exactly: read-only mount, --network none
# throwaway container, lexically-sorted keep-newest-N retention. Sourced by
# bin/claude-panic; could later back a `claude-egress` reader CLI parallel to
# claude-audit if/when that's needed.

# Archive root. Reuses the same parent dir as audit so a forensics sweep can
# find both halves under one tree. Subdir is named so it can't collide with
# audit-archive's "<volname>/<UTC>.jsonl" layout.
claude_egress_archive_dir() {
  printf '%s\n' "${CLAUDE_EGRESS_ARCHIVE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/claude-monitor/egress}"
}

# Print <volname>'s squid access.log to stdout (empty if no traffic logged).
# Read-only mount + no network: cannot mutate or exfiltrate. The image arg is
# resolved by claude_monitor_image (from audit-archive.bash); we reuse it
# rather than introducing a second pinned image.
claude_read_egress_volume() {
  local volname="$1" image="$2"
  docker run --rm --network none -v "$volname":/egress:ro "$image" \
    sh -c 'cat /egress/access.log 2>/dev/null || true'
}

# Snapshot <volname> into <archive_root>/<volname>/<UTC>.log, keeping the
# newest <keep> snapshots. No-op when the log is empty. Best-effort: any
# failure is swallowed so callers (especially claude-panic) never break.
claude_archive_egress_volume() {
  local volname="$1" image="$2" archive_root="$3" keep="${4:-10}"
  local content dir
  content=$(claude_read_egress_volume "$volname" "$image" 2>/dev/null) || return 0
  [[ -n "$content" ]] || return 0
  dir="$archive_root/$volname"
  mkdir -p "$dir" 2>/dev/null || return 0
  printf '%s\n' "$content" >"$dir/$(date -u +%Y%m%dT%H%M%SZ).log" 2>/dev/null || return 0
  find "$dir" -maxdepth 1 -name '*.log' | sort -r | tail -n +"$((keep + 1))" |
    xargs rm -f 2>/dev/null || true
}

# Newest archived snapshot for <volname>, or empty if none. Lexical sort on
# UTC timestamps is chronological. Returns 0 even with no archive so callers
# can `latest=$(claude_latest_egress_archive ...)` under set -e.
claude_latest_egress_archive() {
  local volname="$1" archive_root="$2" dir="$2/$1"
  [[ -d "$dir" ]] || return 0
  find "$dir" -maxdepth 1 -name '*.log' 2>/dev/null | sort | tail -1
}
