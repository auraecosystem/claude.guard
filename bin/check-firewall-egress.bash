#!/bin/bash
# End-to-end (NON-STUBBED) proof of the egress-side guardrails the rest of the
# suite only exercises structurally (parsing generated configs, stubbed docker/dig):
#
#   1. The firewall actually BLOCKS. The real squid + dnsmasq, built from the
#      project's own config generators, deny a non-allowlisted domain, method-block
#      writes to a read-only domain with a 403 (GET still passes), and pass a
#      read-write domain through. Assertions are on observed responses (status codes / origin reached),
#      not config text. See tests/smoke/firewall-egress-probe.sh for the in-container
#      half; squid-config.yaml only `squid -k parse`s the config — nothing else
#      proves it enforces.
#
#   2. --dangerously-skip-firewall actually DISENGAGES. The CONVERSE of (1): the
#      same real squid+dnsmasq scaffolding brings the firewall up TWICE in one
#      container — first the normal allowlisted config (negative control: a chosen
#      domain is blocked, while a control domain proves the harness can reach the
#      loopback origin), then the allow-all config write_squid_allow_all_conf emits
#      under DANGEROUSLY_SKIP_FIREWALL. The EXACT request the allowlist blocked now
#      reaches the origin (200), and the squid access log still records it — proving
#      the proxy stays in the egress path even with the allowlist off (SECURITY.md).
#      The block→pass flip on one domain rules out a dead harness: only the firewall
#      config differs between the two phases. Asserted on observed responses + the
#      access-log contents, never on rendered config; firewall-checks.yaml only
#      `squid -k parse`s the allow-all config, which does not prove it disengages.
#
#   3. The secret scrubber actually SCRUBS. The in-container BASH_ENV scrub
#      (profiles/scrub-secrets.sh) unsets a token-named env var, so an agent
#      shelling out to `echo $CLAUDE_CODE_OAUTH_TOKEN` reaches the transcript empty;
#      and the PostToolUse output sanitizer (sanitize-output.mjs -> redact-secrets.py)
#      redacts a token that leaks into a command's output. Both run through the real
#      in-container invocation paths, not by calling a regex directly.
#
# Real docker, real image, no external network, nothing stubbed — this IS the
# unstubbed layer. Needs docker. Runs in CI via firewall-egress-smoke.yaml.
#
# Usage:
#   bash bin/check-firewall-egress.bash            # image must already exist
#   bash bin/check-firewall-egress.bash --build    # build the image first (local)
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
IMAGE="${CLAUDE_GUARD_IMAGE_MAIN:-secure-claude-sandbox:local}"
PROBE="$REPO_ROOT/tests/smoke/firewall-egress-probe.sh"

status() { printf ':: %s\n' "$1"; }
die() {
  printf '!! %s\n' "$1" >&2
  exit 1
}
pass() { printf 'PASS: %s\n' "$1"; }
fail() {
  printf 'FAIL: %s\n' "$1" >&2
  FAILURES=$((FAILURES + 1))
}
FAILURES=0

command -v docker >/dev/null 2>&1 || die "docker not found"
[[ -f "$PROBE" ]] || die "probe script not found at $PROBE"

BUILD=false
for arg in "$@"; do
  [[ "$arg" == "--build" ]] && BUILD=true
done

if "$BUILD" || ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  status "Building $IMAGE (Dockerfile build context mirrors docker-compose.yml)..."
  # context=.devcontainer, guard-src=repo root — the same shape docker-compose.yml's
  # build block uses (context: ., additional_contexts: guard-src: ..).
  docker build -f "$REPO_ROOT/.devcontainer/Dockerfile" \
    --build-context "guard-src=$REPO_ROOT" \
    -t "$IMAGE" "$REPO_ROOT/.devcontainer" || die "image build failed"
fi

# ── 1. Firewall actually blocks ──────────────────────────────────────────────
# Run as root (dnsmasq binds :53; squid drops to proxy itself) with default caps,
# which include NET_BIND_SERVICE. Do NOT add --cap-drop here: dropping
# NET_BIND_SERVICE would stop dnsmasq binding :53 and silently fail the probe. The
# probe brings up squid+dnsmasq+origin and drives traffic through the proxy,
# asserting on observed responses.
status "[1/4] Firewall egress enforcement (real squid + dnsmasq + loopback origin)"
if docker run --rm --user root -v "$PROBE:/probe.sh:ro" \
  --entrypoint bash "$IMAGE" /probe.sh; then
  pass "firewall blocks: deny non-allowlisted, method-block read-only writes, pass read-write"
else
  fail "firewall egress probe reported failures (see output above)"
fi

# ── 2. --dangerously-skip-firewall actually disengages ───────────────────────
# Bring the real firewall up TWICE in one container (same image, same generators,
# same loopback origin) and flip ONE domain's reachability by swapping ONLY the
# firewall config: the normal allowlisted config (write_squid_conf + NXDOMAIN
# dnsmasq) then the allow-all config write_squid_allow_all_conf emits under
# DANGEROUSLY_SKIP_FIREWALL. The script exits non-zero if any assertion fails, so
# the orchestrator reports one pass/fail like [1/4]. Runs as root with default caps
# (dnsmasq binds :53) — same constraint as [1/4]: do NOT --cap-drop here.
status "[2/4] --dangerously-skip-firewall disengagement (allowlist blocks → allow-all passes, still logged)"
docker run --rm -i --user root --entrypoint bash "$IMAGE" -s <<'DISENGAGE_PROBE'
set -uo pipefail
FAILURES=0
status() { printf ':: %s\n' "$1"; }
pass() { printf 'PASS: %s\n' "$1"; }
fail() {
  printf 'FAIL: %s\n' "$1" >&2
  FAILURES=$((FAILURES + 1))
}

# TARGET is the domain we flip; CONTROL stays reachable under the allowlist so a
# dead harness/origin can't masquerade as a firewall block. MARKER distinguishes
# "reached the origin" from a squid error page.
MARKER="origin-reached-$$"
PROXY="http://127.0.0.1:3128"
TARGET="skipfw.test"
CONTROL="ctl.test"
ACCESS_LOG="/var/log/squid/access.log"
RO="/etc/squid/readonly-domains.txt"

# shellcheck source=/dev/null
source /usr/local/bin/firewall-lib.bash

# Loopback origin: 200 + MARKER for any path/method. One server on :80 because squid
# resolves every host to 127.0.0.1 and fetches port 80.
python3 - "$MARKER" <<'PY' &
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

marker = sys.argv[1].encode()


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(marker)))
        self.end_headers()
        self.wfile.write(marker)

    do_HEAD = do_GET

    def log_message(self, *a):
        pass


HTTPServer(("127.0.0.1", 80), H).serve_forever()
PY

# Base dnsmasq (static records only; the per-phase allowlist lives in conf-dir).
mkdir -p /etc/dnsmasq.d
cat >/etc/dnsmasq.conf <<'EOF'
no-resolv
no-hosts
listen-address=127.0.0.1
bind-interfaces
port=53
conf-dir=/etc/dnsmasq.d
EOF
echo "nameserver 127.0.0.1" >/etc/resolv.conf
prepare_squid_log_dir /var/log/squid || {
  fail "squid log dir not proxy-owned"
  exit 1
}

CODE="" BODY=""
probe_get() {
  local url="$1" bodyfile="/tmp/probe-body.$$"
  # pin-exempt: captures a proxy RESPONSE for assertion; nothing is installed or executed.
  CODE=$(curl -sS -o "$bodyfile" -w '%{http_code}' -x "$PROXY" "$url" 2>/dev/null) || CODE=000
  BODY=$(cat "$bodyfile" 2>/dev/null)
  rm -f "$bodyfile"
}
wait_until() {
  local tries="$1"
  shift
  local i
  for ((i = 0; i < tries; i++)); do
    "$@" >/dev/null 2>&1 && return 0
    sleep 0.5
  done
  return 1
}
start_dnsmasq() {
  printf '%s\n' "$1" >/etc/dnsmasq.d/allowlist.conf
  dnsmasq --test || return 1
  dnsmasq
}
stop_dnsmasq() {
  pkill -x dnsmasq 2>/dev/null || true
  wait_until 40 bash -c '! pgrep -x dnsmasq'
}
stop_squid() {
  squid -k shutdown 2>/dev/null || true
  wait_until 80 bash -c '! pgrep -x squid'
}

wait_until 40 curl -fsS -o /dev/null http://127.0.0.1:80/ || {
  fail "loopback origin never came up"
  exit 1
}

# ── Phase A: normal allowlisted firewall (NEGATIVE CONTROL) ──────────────────
# CONTROL resolves (proves the proxy+origin path works); TARGET is absent from the
# allowlist, so dnsmasq NXDOMAINs it and squid can't reach the origin.
status "(a) allowlisted firewall: $CONTROL reachable, $TARGET blocked"
start_dnsmasq "address=/#/
address=/$CONTROL/127.0.0.1" || {
  fail "dnsmasq (allowlist) failed to start"
  exit 1
}
write_ro_domains "$RO" placeholder.invalid
write_squid_conf 127.0.0.2 "$RO" >/etc/squid/squid.conf
write_squid_error_page /usr/share/squid/errors/en
squid -k parse || {
  fail "restrictive squid.conf failed 'squid -k parse'"
  exit 1
}
squid
wait_until 80 bash -c "curl -fsS -o /dev/null -x $PROXY http://$CONTROL/" || {
  fail "squid (allowlist) never came up"
  exit 1
}
probe_get "http://$CONTROL/"
if [[ "$CODE" == 200 && "$BODY" == *"$MARKER"* ]]; then
  pass "control $CONTROL reaches origin under the allowlist (harness is live, code=$CODE)"
else
  fail "control $CONTROL did NOT reach origin (code=$CODE) — harness/origin broken, the flip below would be meaningless"
fi
probe_get "http://$TARGET/"
if [[ "$CODE" == 200 || "$BODY" == *"$MARKER"* ]]; then
  fail "$TARGET reached the origin under the allowlist (code=$CODE) — it is NOT actually blocked, so disengagement proves nothing"
else
  pass "$TARGET blocked under the allowlist (code=$CODE, origin not reached)"
fi

# ── swap firewall config: allowlist → allow-all ──────────────────────────────
stop_squid
stop_dnsmasq
: >"$ACCESS_LOG" # truncate (squid is down) so the allow-all egress record is unambiguous

# ── Phase B: allow-all firewall (DISENGAGEMENT) ──────────────────────────────
# write_squid_allow_all_conf is the exact config the DANGEROUSLY_SKIP_FIREWALL
# branch of init-firewall.bash uses. A resolving dnsmasq record models the
# forwarding resolver that branch runs (no external network in CI); the firewall
# config is the ONLY thing that changed from Phase A.
status "(b) allow-all firewall: the same $TARGET request now succeeds and is still logged"
start_dnsmasq "address=/$TARGET/127.0.0.1" || {
  fail "dnsmasq (allow-all) failed to start"
  exit 1
}
write_squid_allow_all_conf 127.0.0.2 >/etc/squid/squid.conf
squid -k parse || {
  fail "allow-all squid.conf failed 'squid -k parse'"
  exit 1
}
squid
wait_until 80 bash -c "curl -fsS -o /dev/null -x $PROXY http://$TARGET/" || true
probe_get "http://$TARGET/"
if [[ "$CODE" == 200 && "$BODY" == *"$MARKER"* ]]; then
  pass "$TARGET now reaches the origin (code=$CODE) — the exact request the allowlist blocked"
else
  fail "$TARGET did NOT reach the origin under allow-all (code=$CODE, body=${BODY:0:120})"
fi
# SECURITY.md: the proxy stays in the egress path even with the allowlist off, so
# the audit record is not lost. Pin that boundary claim on the access log.
if wait_until 40 grep -q "$TARGET" "$ACCESS_LOG"; then
  pass "egress to $TARGET recorded in the squid access log (proxy still in path under allow-all)"
else
  fail "egress to $TARGET NOT in the squid access log — the audit record was lost under allow-all"
fi

if [[ $FAILURES -gt 0 ]]; then
  {
    echo "==> $FAILURES disengagement assertion(s) failed. Diagnostics:"
    echo "--- squid.conf ---"
    cat /etc/squid/squid.conf
    echo "--- dnsmasq allowlist ---"
    cat /etc/dnsmasq.d/allowlist.conf
    echo "--- access.log (tail) ---"
    tail -n 20 "$ACCESS_LOG" 2>/dev/null || echo "(no access.log)"
  } >&2
  exit 1
fi
echo "All skip-firewall disengagement assertions passed"
exit 0
DISENGAGE_PROBE
disengage_rc=$?
if ((disengage_rc == 0)); then
  pass "skip-firewall disengages: allowlist blocks the domain, allow-all reaches it (still logged)"
else
  fail "skip-firewall disengagement probe reported failures (see output above)"
fi

# ── 3. Secret scrubber: env-var scrub ────────────────────────────────────────
# A token-NAMED var is unset in the agent's (non-interactive bash) shell via the
# baked BASH_ENV=/etc/scrub-secrets.sh, so `echo $CLAUDE_CODE_OAUTH_TOKEN` yields
# nothing. The scrub keys on the NAME (*token*), so the value is an obvious
# placeholder. Runs as the default user (node = the agent's uid), the real path.
status "[3/4] Secret scrubber: env-var scrub in the agent shell"
FAKE_OAUTH="not-a-real-oauth-token-PLACEHOLDER-0000"
scrub_out=$(docker run --rm -e CLAUDE_CODE_OAUTH_TOKEN="$FAKE_OAUTH" \
  --entrypoint bash "$IMAGE" \
  -c 'printf "[%s]" "${CLAUDE_CODE_OAUTH_TOKEN-UNSET}"' 2>/dev/null) || scrub_out="<exec-failed>"
if [[ "$scrub_out" == *"$FAKE_OAUTH"* ]]; then
  fail "env scrub: CLAUDE_CODE_OAUTH_TOKEN value leaked to shell output ($scrub_out)"
elif [[ "$scrub_out" == "[UNSET]" ]]; then
  pass "env scrub: CLAUDE_CODE_OAUTH_TOKEN unset in agent shell (echo yields nothing)"
else
  fail "env scrub: unexpected output '$scrub_out' (expected [UNSET])"
fi

# ── 4. Secret scrubber: output redaction ─────────────────────────────────────
# A token that leaks into a command's output must be redacted before it reaches
# the transcript. Drive the real PostToolUse sanitizer (sanitize-output.mjs, which
# shells out to redact-secrets.py + detect-secrets, both baked into the image).
# The leaked value must look like a REAL credential — mixed case and digits — or
# the redactor's placeholder skip correctly ignores it (a repeated-char filler
# like AAAA… is documentation shape, not a secret). Assembled from halves at
# runtime so no contiguous token-shaped literal sits in this file for the
# repo's secret scanners to flag.
status "[4/4] Secret scrubber: output redaction via PostToolUse sanitizer"
NEEDLE="q9X2mN7pK4rT8wY1""cV5bZ3dF6gH0jL2e"
LEAK="token=$NEEDLE"
payload=$(printf '{"tool_name":"Bash","tool_response":"command leaked %s here"}' "$LEAK")
red=$(printf '%s' "$payload" | docker run --rm -i --entrypoint node "$IMAGE" \
  /opt/claude-guard/.claude/hooks/sanitize-output.mjs 2>/dev/null) || red="<exec-failed>"
# The sanitizer emits a hook response only when it CHANGED the output; no change
# means it exits silently (empty). So: non-empty (it acted) AND the leaked token is
# gone is the redaction property. A silent exit here would mean the token reached
# the transcript unredacted — a failure.
if [[ "$red" == "<exec-failed>" || -z "$red" ]]; then
  fail "output redaction: sanitizer emitted nothing — leaked token would reach the transcript"
elif [[ "$red" == *"$NEEDLE"* ]]; then
  fail "output redaction: leaked token survived in sanitizer output"
else
  pass "output redaction: leaked token redacted by sanitize-output.mjs"
fi

# ── Summary ──────────────────────────────────────────────────────────────────
echo ""
if [[ $FAILURES -eq 0 ]]; then
  status "All firewall + scrubber egress smoke checks passed"
  exit 0
fi
die "$FAILURES check(s) failed"
