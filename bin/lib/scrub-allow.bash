# shellcheck shell=bash
# scrub-allow.bash — carry the user's SCRUB_SECRETS_ALLOW override into the
# container so the in-container credential scrub (.devcontainer/profiles/
# scrub-secrets.sh) spares the named vars. Sourced by the `claude` wrapper.
#
# The scrub runs inside the container on every `bash -c` (via BASH_ENV). It only
# spares a var when (a) SCRUB_SECRETS_ALLOW lists its name AND (b) the var is
# actually present in the container. So this forwards BOTH the allowlist and the
# named vars' values from the wrapper's environment. The named vars are
# forwarded ONLY because the user explicitly declared them non-secret — never
# list secret-bearing vars in SCRUB_SECRETS_ALLOW.

# scrub_allow_exec_flags — print the `docker exec` -e flags, one token per line,
# so a caller can read them into an array. Empty when SCRUB_SECRETS_ALLOW is
# unset. Uses bare `-e NAME` (no =value) so docker pulls the value from this
# process's environment and it never lands in the container's argv (visible via
# ps). Names are split on spaces or colons, matching the scrub script.
scrub_allow_exec_flags() {
  [[ -n "${SCRUB_SECRETS_ALLOW:-}" ]] || return 0
  printf '%s\n' -e SCRUB_SECRETS_ALLOW
  local _name
  for _name in ${SCRUB_SECRETS_ALLOW//:/ }; do
    [[ -n "${!_name+x}" ]] && printf '%s\n' -e "$_name"
  done
  return 0
}
