#!/usr/bin/env bash
# Decide whether a gate/track job runs: diff the PR for path matches and scan
# commit titles for the trigger/heldout keywords; emit run/heldout outputs.
# Env: BASE_SHA, HEAD_SHA, PATHS_REGEX, TRIGGER_KEYWORD, HELDOUT_KEYWORD
set -eo pipefail
# No PR context (push to main, workflow_dispatch) means there is no
# base/head to diff — run everything, since post-merge and manual runs
# are not path-gated.
if [[ -z "$BASE_SHA" || -z "$HEAD_SHA" ]]; then
  echo "run=true" >>"$GITHUB_OUTPUT"
  echo "heldout=false" >>"$GITHUB_OUTPUT"
  exit 0
fi
# Capture git output into variables, then match against a here-string, rather
# than piping `git … | grep -q`. Under `set -o pipefail` that pipe is a trap:
# grep -q exits on its first match and closes the pipe, the still-writing git is
# killed by SIGPIPE (exit 141), and pipefail turns that into a non-zero pipeline
# — so a MATCH reads as no-match and the && guard never fires. The matching
# commit is the newest (git log's first line), so grep -q exits almost
# immediately and the kill is reliable on a slow runner. A here-string has no
# upstream process for grep's early exit to kill, so pipefail cannot misfire; a
# genuine git failure surfaces via set -e (a broken range is a bug, not a silent
# run=false).
changed="$(git diff --name-only "$BASE_SHA...$HEAD_SHA")"
subjects="$(git log --format='%s' "$BASE_SHA...$HEAD_SHA")"
run=false
if [[ -n "$PATHS_REGEX" ]] && grep -qE "$PATHS_REGEX" <<<"$changed"; then
  run=true
  echo "trigger: paths changed"
fi
if [[ -n "$TRIGGER_KEYWORD" ]] && grep -qiF "$TRIGGER_KEYWORD" <<<"$subjects"; then
  run=true
  echo "trigger: $TRIGGER_KEYWORD in a commit title"
fi
heldout=false
if [[ -n "$HELDOUT_KEYWORD" ]] && grep -qiF "$HELDOUT_KEYWORD" <<<"$subjects"; then
  heldout=true
  run=true
  echo "trigger: $HELDOUT_KEYWORD — gate will include the frozen held-out split"
fi
echo "run=$run" >>"$GITHUB_OUTPUT"
echo "heldout=$heldout" >>"$GITHUB_OUTPUT"
