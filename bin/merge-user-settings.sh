#!/bin/bash
# merge-user-settings.sh — Merge security defaults into ~/.claude/settings.json.
# Idempotent: safe to run on every setup.bash invocation.
#
# Usage: merge-user-settings.sh <install-dir>
#
# Strategy:
#   - env: union (security keys win on conflict)
#   - permissions.deny: array union (sorted for stable output)
#   - permissions.allow: array union (sorted for stable output)
#   - hooks: remove prior SCCD-managed entries, then append current ones
#   - sandbox: deep merge (security wins)
#   - Everything else (theme, editorMode, plugins, etc.): preserved
set -euo pipefail

INSTALL_DIR="${1:?usage: merge-user-settings.sh <install-dir>}"
SETTINGS="$HOME/.claude/settings.json"
SECURITY_TEMPLATE="$INSTALL_DIR/user-config/settings.json"

if [[ ! -f "$SECURITY_TEMPLATE" ]]; then
  echo "merge-user-settings: template not found: $SECURITY_TEMPLATE" >&2
  exit 1
fi

if ! command -v jq >/dev/null 2>&1; then
  echo "merge-user-settings: jq required but not found" >&2
  exit 1
fi

mkdir -p "$(dirname "$SETTINGS")"

existing='{}'
verb="created"
if [[ -f "$SETTINGS" ]]; then
  existing=$(cat "$SETTINGS")
  verb="merged security defaults into"
fi

security=$(cat "$SECURITY_TEMPLATE")

merged=$(jq -n \
  --argjson existing "$existing" \
  --argjson security "$security" \
  --arg sccd_dir "$INSTALL_DIR" \
  '
  # Start with all existing user settings
  $existing

  # Merge env (security keys added, existing preserved, add SCCD_DIR)
  | .env = ((.env // {}) + $security.env + {SCCD_DIR: $sccd_dir})

  # Union deny rules (sorted for stable output)
  | .permissions.deny = (
      [(.permissions.deny // [])[], ($security.permissions.deny // [])[]]
      | unique
    )

  # Union allow rules (sorted for stable output)
  | .permissions.allow = (
      [(.permissions.allow // [])[], ($security.permissions.allow // [])[]]
      | unique
    )

  # Merge sandbox (security values override on conflict)
  | .sandbox = ((.sandbox // {}) * ($security.sandbox // {}))
  | .sandbox.network.allowedDomains = (
      [(.sandbox.network.allowedDomains // [])[], ($security.sandbox.network.allowedDomains // [])[]]
      | unique
    )
  | .sandbox.filesystem.denyWrite = (
      [(.sandbox.filesystem.denyWrite // [])[], ($security.sandbox.filesystem.denyWrite // [])[]]
      | unique
    )
  | .sandbox.filesystem.denyRead = (
      [(.sandbox.filesystem.denyRead // [])[], ($security.sandbox.filesystem.denyRead // [])[]]
      | unique
    )

  # Hooks: remove prior SCCD-managed entries, then append current ones.
  # An entry is SCCD-managed if any sub-hook has:
  #   - a command containing "SCCD_DIR", or
  #   - a prompt starting with "You see ONE edit hunk"
  | .hooks = (.hooks // {})

  | reduce ($security.hooks | to_entries[]) as $evt (.;
      .hooks[$evt.key] = (
        [(.hooks[$evt.key] // [])[] |
          select((.hooks // []) | all(
            ((.command // "") | contains("SCCD_DIR") | not)
            and
            ((.prompt // "")[0:25] != "You see ONE edit hunk. Yo")
          ))
        ]
        + $evt.value
      )
    )
  ')

echo "$merged" | jq '.' >"$SETTINGS"
echo "merge-user-settings: $verb $SETTINGS"
