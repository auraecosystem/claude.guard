# shellcheck shell=bash
# Collect-all check harness: run a batch of checks, record each result, and
# report every failure at once instead of aborting on the first. One expensive
# setup (e.g. a single devcontainer bring-up) then harvests ALL broken
# invariants per run, not just the first — turning a one-bug-per-run loop into a
# whole batch per run.
#
# RESTRICTION — DIAGNOSTIC USE ONLY. This belongs in health checks and smoke
# tests. Do NOT use it to soften a production code path (e.g. init-firewall.bash):
# a real launch must fail loud at the first error. Collecting failures is correct
# for *diagnosis*, wrong for *enforcement*.
#
# RESTRICTION — THE LOAD-BEARING set -e RULE (read before touching run_check):
# Each check runs in a standalone subshell `( set -e; "$@" )` whose status is
# read on the NEXT line via `status=$?`. It MUST NOT be rewritten as
# `( ... ) || status=$?`, `if ( ... ); then`, or placed in any &&/||/if/while/!
# context. Bash ignores `set -e` for the commands inside a compound command that
# itself runs in such a context, and whether an explicit inner `set -e` re-arms
# it is interpretation- and version-dependent. Reading `$?` on its own line —
# with a temporary `set +e` so the failing subshell can't abort the harness — is
# the only form that reliably gives each check real fail-fast semantics. Break
# this and a check will silently sail past its own internal errors and be
# recorded as a pass.
#
# Usage:
#   source bin/lib/check-harness.bash
#   my_check() { some_probe || { echo "why it failed"; return 1; }; }
#   run_check [--needs NAME]... <name> "<description>" my_check
#   harness_summary    # prints the PASS/FAIL/SKIP summary; returns 1 if any failed
#
# Inside a check function: use if/[[ ]]/&&/|| for INTENTIONAL conditionals (these
# naturally suppress set -e for that one test); let bare commands abort the check
# on unexpected failure; express a failed assertion as `return 1`. Prefer
# positive assertions (`test -f X`) over negative ones (`! test -e X`), which
# can't tell "absent (ok)" from "the probe itself errored (bad)" — gate those
# with --needs on a liveness check.

# Results keyed by check name: pass | fail | skip. A later check can gate on an
# earlier one's name via --needs.
declare -A HARNESS_RESULT=()
HARNESS_FAILURES=()
HARNESS_SKIPS=()
HARNESS_PASSES=0

run_check() {
  local needs=()
  while [[ "${1:-}" == --needs ]]; do
    needs+=("$2")
    shift 2
  done
  local name="$1" desc="$2"
  shift 2

  # Skip (not fail) when a prerequisite check didn't pass, so one dead
  # dependency yields a single root-cause failure, not a cascade of derivatives.
  local need
  if ((${#needs[@]})); then
    for need in "${needs[@]}"; do
      if [[ "${HARNESS_RESULT[$need]:-}" != pass ]]; then
        HARNESS_RESULT["$name"]=skip
        HARNESS_SKIPS+=("$desc — prerequisite '$need' did not pass")
        echo "SKIP: $desc (needs $need)"
        return 0
      fi
    done
  fi

  # See THE LOAD-BEARING set -e RULE above before changing these five lines.
  # had_e records the caller's set -e state so run_check restores it exactly,
  # never silently turning -e on for a caller that ran without it.
  local had_e status
  case $- in
  *e*) had_e=1 ;;
  *) had_e=0 ;;
  esac
  set +e
  (
    set -e
    "$@"
  )
  status=$?
  ((had_e)) && set -e

  if ((status == 0)); then
    HARNESS_RESULT["$name"]=pass
    HARNESS_PASSES=$((HARNESS_PASSES + 1))
    echo "PASS: $desc"
  else
    HARNESS_RESULT["$name"]=fail
    HARNESS_FAILURES+=("$desc (check exited $status)")
    echo "FAIL: $desc (check exited $status)" >&2
  fi
  return 0
}

# Print the batch summary. Returns 1 if any check failed, 0 otherwise. The caller
# decides what to do on failure (e.g. dump container logs before teardown).
harness_summary() {
  echo
  echo "==> Summary: ${HARNESS_PASSES} passed, ${#HARNESS_FAILURES[@]} failed, ${#HARNESS_SKIPS[@]} skipped"
  if ((${#HARNESS_SKIPS[@]})); then
    printf '  SKIP: %s\n' "${HARNESS_SKIPS[@]}"
  fi
  if ((${#HARNESS_FAILURES[@]})); then
    printf '  FAIL: %s\n' "${HARNESS_FAILURES[@]}" >&2
    return 1
  fi
  return 0
}
