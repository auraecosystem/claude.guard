# shellcheck shell=bash
# auto_mint_gh_token <claude-github-app-bin> — export a short-lived GitHub
# App installation token as GH_TOKEN when the user has run
# `claude-github-app install`. Honors GH_TOKEN (explicit beats implicit) and
# CLAUDE_NO_GH_TOKEN=1. Non-fatal: minting failures just leave GH_TOKEN unset
# so the calling wrapper can launch without GitHub credentials.
#
# Uses a grep probe for installation_id rather than jq — keeps the wrapper's
# hot path free of an external dep, and `claude-github-app token` itself
# re-validates the field if the grep passes.
auto_mint_gh_token() {
  [[ -n "${GH_TOKEN:-}" || "${CLAUDE_NO_GH_TOKEN:-}" == "1" ]] && return 0
  local bin="$1"
  [[ -x "$bin" ]] || return 0
  local meta="${XDG_CONFIG_HOME:-$HOME/.config}/claude/github-app/app.json"
  [[ -f "$meta" ]] || return 0
  grep -q '"installation_id"[[:space:]]*:[[:space:]]*[0-9]' "$meta" 2>/dev/null || return 0
  local minted
  if minted=$("$bin" token 2>/dev/null); then
    export GH_TOKEN="$minted"
  else
    echo "claude: warning — claude-github-app token failed; launching without GH_TOKEN." >&2
  fi
}
