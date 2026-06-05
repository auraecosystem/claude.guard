#!/usr/bin/env bash
# Unit tests for bin/lib/check-harness.bash — the collect-all check harness.
# Focus: the load-bearing set -e isolation contract (each check gets real
# fail-fast semantics; a failure in one check never aborts the batch or the
# caller) and --needs phase gating.
#
# A failing check runs in a SUBSHELL, so it cannot mutate parent variables —
# tests that need to observe whether a check's body ran use a temp file, which a
# subshell CAN create, instead of a variable.
#
# Run locally / in CI:  bash tests/test-check-harness.bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=../bin/lib/check-harness.bash disable=SC1091
source "$REPO_ROOT/bin/lib/check-harness.bash"

FAILED=0
assert_eq() {
  local got="$1" want="$2" msg="$3"
  if [[ "$got" == "$want" ]]; then
    echo "ok: $msg"
  else
    echo "FAIL: $msg (got '$got', want '$want')" >&2
    FAILED=1
  fi
}
assert_absent() {
  local path="$1" msg="$2"
  if [[ -e "$path" ]]; then
    echo "FAIL: $msg (file '$path' exists)" >&2
    FAILED=1
  else
    echo "ok: $msg"
  fi
}

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

# 1. A passing check is recorded pass.
ck_pass() { return 0; }
run_check pass_check "passing check" ck_pass >/dev/null
assert_eq "${HARNESS_RESULT[pass_check]}" pass "passing check recorded pass"

# 2. A check returning nonzero is recorded fail and the batch keeps going.
ck_fail() {
  echo "boom"
  return 1
}
run_check fail_check "failing check" ck_fail >/dev/null 2>&1
assert_eq "${HARNESS_RESULT[fail_check]}" fail "failing check recorded fail"

# 3. set -e isolation: an UNEXPECTED command failure mid-check aborts the check
#    (the command after `false` must NOT run) and is recorded as a failure — not
#    a silent pass. This is the contract the whole harness rests on.
ck_midfail() {
  false
  : >"$TMP/ran_past_false"
  return 0
}
run_check midfail_check "mid-function failure" ck_midfail >/dev/null 2>&1
assert_eq "${HARNESS_RESULT[midfail_check]}" fail "unexpected error recorded fail (not pass)"
assert_absent "$TMP/ran_past_false" "set -e aborted the check at the failing command"

# 4. run_check must not abort the caller even though checks failed under the
#    caller's own set -e, and must leave set -e exactly as it found it.
case $- in
*e*) estate=on ;;
*) estate=off ;;
esac
assert_eq "$estate" on "caller's set -e preserved after run_check"

# 5. --needs gating: a check whose prerequisite failed is SKIPPED, body not run.
ck_records_run() {
  : >"$TMP/skip_body_ran"
  return 0
}
run_check --needs fail_check skipped_check "gated on a failure" ck_records_run >/dev/null
assert_eq "${HARNESS_RESULT[skipped_check]}" skip "gated check skipped when prereq failed"
assert_absent "$TMP/skip_body_ran" "skipped check body did not run"

# 6. --needs passes through when every prerequisite passed.
run_check --needs pass_check gated_ok "gated on a pass" ck_pass >/dev/null
assert_eq "${HARNESS_RESULT[gated_ok]}" pass "gated check runs when prereq passed"

# 7. multiple --needs: any unmet prerequisite skips.
run_check --needs pass_check --needs fail_check multi_gated "two prereqs, one failed" ck_pass >/dev/null
assert_eq "${HARNESS_RESULT[multi_gated]}" skip "multi-needs skips when any prereq failed"

# 8. harness_summary returns nonzero iff there were failures.
sum_rc=0
harness_summary >/dev/null 2>&1 || sum_rc=$?
assert_eq "$sum_rc" 1 "summary returns 1 when failures present"

if ((FAILED)); then
  echo "check-harness tests FAILED" >&2
  exit 1
fi
echo "all check-harness tests passed"
