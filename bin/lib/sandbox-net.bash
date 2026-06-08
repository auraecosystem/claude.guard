# shellcheck shell=bash
# Per-session sandbox /24 so multiple claude-guard sessions run concurrently.
#
# The sandbox network pins a static subnet and a static firewall IP that the app's
# DNS, the squid proxy, the monitor bind, and init-firewall's iptables all
# hardwire. Two sessions defaulting to the same 172.30.0.0/24 therefore collide in
# Docker's IPAM ("invalid pool request: Pool overlaps with other one on this
# address space"). We vary only the third octet within 172.30.0.0/18 —
# 172.30.0.0/24 .. 172.30.<MAX>.0/24 — which stays clear of the other 172.x blocks
# Docker's default address pools draw from. Octet 0 reproduces the compose
# fallbacks, so a bare `docker compose up` (no launcher) is unchanged.
#
# The launcher exports SANDBOX_SUBNET + SANDBOX_IP from here before `devcontainer
# up`; compose interpolates them (with the octet-0 values as `:-` fallbacks).

SANDBOX_NET_SECOND_OCTET=30
SANDBOX_NET_MAX_THIRD_OCTET=63 # 64 concurrent sessions; raise to widen.

# _sandbox_subnet K — the /24 assigned to session octet K.
_sandbox_subnet() { printf '172.%s.%s.0/24' "$SANDBOX_NET_SECOND_OCTET" "$1"; }
# _sandbox_ip K — the firewall's address inside that /24.
_sandbox_ip() { printf '172.%s.%s.2' "$SANDBOX_NET_SECOND_OCTET" "$1"; }

# _is_our_subnet SUBNET — true when SUBNET is one of the /24s we allocate.
_is_our_subnet() {
  local octet
  for ((octet = 0; octet <= SANDBOX_NET_MAX_THIRD_OCTET; octet++)); do
    [[ "$1" == "$(_sandbox_subnet "$octet")" ]] && return 0
  done
  return 1
}

# _sandbox_subnets_in_use — every subnet currently held by a Docker network, one
# per line (a network may declare several). Empty when there are none or docker is
# unavailable.
_sandbox_subnets_in_use() {
  local -a ids
  mapfile -t ids < <(docker network ls -q 2>/dev/null)
  ((${#ids[@]})) || return 0
  docker network inspect "${ids[@]}" \
    --format '{{range .IPAM.Config}}{{println .Subnet}}{{end}}' 2>/dev/null
}

# prune_stale_sandbox_networks — remove any of OUR /24 networks that has no live
# endpoints, reclaiming a dead session's octet (and clearing a persistent
# workspace's leftover so `docker compose up` can recreate it on the freshly
# allocated subnet instead of erroring on a config mismatch). A network with
# attached containers (a live session) fails removal and is left untouched. Run
# before allocation so a just-freed octet is available.
prune_stale_sandbox_networks() {
  local -a ids
  mapfile -t ids < <(docker network ls -q --filter "driver=bridge" 2>/dev/null)
  ((${#ids[@]})) || return 0
  local id net
  while read -r id net; do
    _is_our_subnet "$net" || continue
    docker network rm "$id" >/dev/null 2>&1 || true
  done < <(docker network inspect "${ids[@]}" \
    --format '{{.ID}} {{range .IPAM.Config}}{{.Subnet}}{{end}}' 2>/dev/null)
}

# export_sandbox_subnet — claim the first free 172.30.K.0/24 for this session and
# export SANDBOX_SUBNET + SANDBOX_IP (the firewall at .2). Fails loud (the whole
# launch aborts) when every slot is taken, rather than letting `devcontainer up`
# hit an opaque pool-overlap error.
export_sandbox_subnet() {
  local in_use octet subnet
  in_use="$(_sandbox_subnets_in_use)"
  for ((octet = 0; octet <= SANDBOX_NET_MAX_THIRD_OCTET; octet++)); do
    subnet="$(_sandbox_subnet "$octet")"
    grep -qxF "$subnet" <<<"$in_use" && continue
    export SANDBOX_SUBNET="$subnet"
    SANDBOX_IP="$(_sandbox_ip "$octet")"
    export SANDBOX_IP
    return 0
  done
  cg_error "claude-guard: all $((SANDBOX_NET_MAX_THIRD_OCTET + 1)) sandbox subnets ($(_sandbox_subnet 0) .. $(_sandbox_subnet "$SANDBOX_NET_MAX_THIRD_OCTET")) are in use; close a session, or raise SANDBOX_NET_MAX_THIRD_OCTET in bin/lib/sandbox-net.bash."
  exit 1
}
