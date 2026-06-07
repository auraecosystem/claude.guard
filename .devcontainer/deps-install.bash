# shellcheck shell=bash
# Lockfile-keyed dependency install for the hardener init container. Sourced by
# entrypoint.bash (and unit-tested standalone, like guard-dir.bash).
#
# node_modules lives on the bind-mounted workspace, so it PERSISTS across sessions.
# Re-running `pnpm install` every launch then only re-verifies an unchanged tree —
# thousands of stat()/readlink() calls that each cross the gVisor gofer boundary and
# make the hardener (the one-shot the app gates on) the long pole of every launch.
# When the install inputs (package.json + lockfile) are byte-identical to the last
# successful install recorded in node_modules, the tree is already correct, so the
# install is skipped entirely.

# Stamp recording the fingerprint of the last successful install. Kept under
# node_modules so it is dropped whenever node_modules is, and node-owned like the
# rest of the tree (never root, to honor entrypoint.bash's no-root-on-host rule).
_deps_stamp_path() { printf '%s/node_modules/.claude-guard-deps-stamp' "$1"; }

# Fingerprint the inputs pnpm reads to plan an install: package.json + the lockfile.
# Any change to either (added dep, bumped version, regenerated lock) changes the hash
# and forces a real install. A workspace without pnpm-lock.yaml just hashes package.json.
deps_fingerprint() {
  # `|| true` so a missing lockfile (cat exits non-zero) doesn't fail the pipeline
  # under `set -o pipefail` — entrypoint.bash sources this with `set -e`, where that
  # failure would otherwise abort the launch from an assignment like fp="$(...)".
  { cat "$1/package.json" "$1/pnpm-lock.yaml" 2>/dev/null || true; } | sha256sum | cut -d' ' -f1
}

# True when node_modules exists AND its stamp matches the current fingerprint — i.e.
# the installed tree already reflects package.json + the lockfile, so an install would
# be a no-op. Any miss (no node_modules, no stamp, changed inputs) returns non-zero so
# the caller installs.
deps_up_to_date() {
  local dir="$1" stamp
  stamp="$(_deps_stamp_path "$dir")"
  [[ -d "$dir/node_modules" && -r "$stamp" ]] || return 1
  [[ "$(cat "$stamp")" == "$(deps_fingerprint "$dir")" ]]
}

# Record the current fingerprint so the next launch can skip an unchanged install.
# Written as the node user so it is node-owned (node owns node_modules). Best-effort:
# a failed stamp just means the next launch re-installs, never a failed launch.
deps_mark_installed() {
  local dir="$1" fp
  fp="$(deps_fingerprint "$dir")"
  su node -c "printf '%s' '$fp' > '$(_deps_stamp_path "$dir")'" 2>/dev/null || true
}

# True when an HTTP(S) proxy is configured — i.e. this container can reach the
# registry through the firewall's squid. The hardener is network-isolated unless the
# launcher wires it the proxy, so this gates whether an online install is even possible.
_deps_have_proxy() { [[ -n "${HTTPS_PROXY:-${https_proxy:-${HTTP_PROXY:-${http_proxy:-}}}}" ]]; }

# Install deps in $dir as the node user (so node_modules stays node-owned — no root
# leak onto the host), skipping when the lockfile-keyed stamp is already current.
#
# Verify OFFLINE first: an already-complete node_modules is confirmed instantly with no
# network, and an incomplete one fails FAST instead of hanging on sockets the firewall
# drops (e.g. a macOS-installed tree missing the lockfile's linux-only optional binaries).
# Only when offline verification fails AND a proxy is configured do we attempt an online
# install to fetch the missing packages. Stamps only after a fully successful install, so
# a partial/failed install never records a false "up to date". stderr stays visible.
# Returns 0 on skip/success, non-zero when the tree is incomplete and cannot be repaired.
install_deps() {
  local dir="$1"
  if deps_up_to_date "$dir"; then
    echo "Dependencies in $dir already current (lockfile unchanged) — skipping install."
    return 0
  fi
  echo "Verifying dependencies in $dir (offline)..."
  if su node -c "cd '$dir' && pnpm install --frozen-lockfile --offline --silent" 2>/dev/null; then
    deps_mark_installed "$dir"
    return 0
  fi
  if ! _deps_have_proxy; then
    echo "ERROR: node_modules in $dir does not satisfy the lockfile and this container has no registry access to fetch the rest." >&2
    echo "       Run 'pnpm install' on the host (or relaunch with the hardener granted proxy egress) so the tree is complete before launch." >&2
    return 1
  fi
  echo "Installing dependencies in $dir (as node, via proxy)..."
  su node -c "cd '$dir' && pnpm install --silent" || return $?
  deps_mark_installed "$dir"
}
