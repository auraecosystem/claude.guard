#!/bin/bash
# Shared helpers for Claude Code hook scripts

PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$(pwd)}"
cd "$PROJECT_DIR" || exit 1

exists() { command -v "$1" &>/dev/null; }

# Detect execution environment. Returns one of: sandbox, devcontainer, host.
# Prefer filesystem sentinels (require root to create) over env vars alone.
detect_env() {
  if [[ "${IS_SANDBOX:-}" == "yes" ]]; then
    printf 'sandbox'
  elif [[ -f /run/hardening-complete ]]; then
    printf 'devcontainer'
  elif [[ "${DEVCONTAINER:-}" == "true" ]]; then
    printf 'devcontainer'
  else
    printf 'host'
  fi
}

has_script() {
  [[ -f package.json ]] || return 1
  local val
  val=$(jq -r --arg name "$1" '.scripts[$name] // empty' package.json 2>/dev/null)
  [[ -n "$val" && "$val" != *"ERROR: Configure"* ]]
}
