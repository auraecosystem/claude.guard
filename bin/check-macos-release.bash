#!/bin/bash
# Pre-release smoke check for macOS. GitHub's macOS runners cannot host the
# OrbStack VM this stack depends on, so the macOS happy path is never exercised
# by CI — run this on a real Mac before tagging a release and treat a failure
# as a release blocker. It automates what a script can verify (provider,
# runtime, container launch, workspace writability as the unprivileged user)
# and ends with the short manual checklist a script cannot drive (a live
# session, a firewall block, a monitor push, the audit log).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/runtime-detect.bash disable=SC1091
source "$SCRIPT_DIR/lib/runtime-detect.bash"
# shellcheck source=lib/docker-retry.bash disable=SC1091
source "$SCRIPT_DIR/lib/docker-retry.bash"

ALPINE_IMAGE="${ALPINE_IMAGE:-alpine:3.21}"

status() { printf ':: %s\n' "$1"; }
die() {
  printf '!! %s\n' "$1" >&2
  exit 1
}

[[ "$(uname -s)" == "Darwin" ]] ||
  die "this is the macOS pre-release check — run it on a Mac (CI cannot host the OrbStack VM)"

status "1/5 Docker provider"
docker info >/dev/null 2>&1 || die "Docker not reachable — start OrbStack and retry"
docker_provider_is_orbstack ||
  die "Docker provider is not OrbStack — only OrbStack maps bind-mount ownership so the agent can write /workspace (brew install orbstack)"
status "OrbStack is the active provider"

status "2/5 Runtime resolution"
rt="$(detect_container_runtime)"
runtime_isolation_summary "$rt"
status "effective runtime: $rt — $ISOLATION_LABEL"
if [[ "$rt" != "runc" ]] && ! docker_runtime_executes "$rt"; then
  die "runtime '$rt' is selected but cannot launch a container — re-run setup.bash"
fi

status "3/5 Container launch under $rt"
pull_with_retry "$ALPINE_IMAGE"
output=$(docker run --rm --runtime="$rt" "$ALPINE_IMAGE" echo "launch-ok")
[[ "$output" == "launch-ok" ]] || die "container output mismatch: expected 'launch-ok', got '$output'"
status "container launches"

status "4/5 Workspace writable by the unprivileged user"
# The agent runs as uid 1000 (node) against a bind-mounted workspace; providers
# that present the mount as root:root (Colima's virtiofs, lima-vm/lima#4053)
# leave the agent unable to write. Prove a non-root write works end-to-end.
ws=$(mktemp -d)
docker run --rm --runtime="$rt" -u 1000:1000 -v "$ws":/workspace "$ALPINE_IMAGE" \
  sh -c 'echo write-ok > /workspace/.release-check && cat /workspace/.release-check' |
  grep -qx "write-ok" || {
  rm -rf "$ws"
  die "uid 1000 cannot write a bind-mounted workspace under $rt — sessions would be read-only"
}
rm -rf "$ws"
status "workspace writes work as uid 1000"

status "5/5 Doctor"
"$SCRIPT_DIR/claude-guard-doctor" || die "doctor did not report PROTECTED — fix the findings above before releasing"

status "Automated checks passed. Finish the release check manually:"
status "  - launch a session (claude-guard) and confirm Claude responds"
status "  - inside it, confirm a non-allowlisted domain is blocked (e.g. curl https://example.org fails)"
status "  - trigger a monitor notification and confirm the push arrives on your phone"
status "  - run 'claude-guard audit' and confirm the session's tool calls are listed"
