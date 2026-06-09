# shellcheck shell=bash
# Resolve a prebuilt sandbox image for the current commit so `devcontainer up`
# can skip the local build. Sourced by bin/claude (pulls + pins) and
# bin/claude-doctor (read-only probe only).
#
# Implementation split into focused sub-modules:
#   ghcr-metadata.bash  — GHCR ref derivation and registry probe
#   cosign-verify.bash  — signature verification and SBOM diff
#   image-cache.bash    — local-image and verified-image cache
_RESOLVE_IMAGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=ghcr-metadata.bash disable=SC1091
source "$_RESOLVE_IMAGE_DIR/ghcr-metadata.bash"
# shellcheck source=cosign-verify.bash disable=SC1091
source "$_RESOLVE_IMAGE_DIR/cosign-verify.bash"
# shellcheck source=image-cache.bash disable=SC1091
source "$_RESOLVE_IMAGE_DIR/image-cache.bash"
unset _RESOLVE_IMAGE_DIR

# Export CLAUDE_GUARD_IMAGE_* + CLAUDE_GUARD_PULL_POLICY when a matching prebuilt set is
# available AND verifies; no-op (compose build defaults) otherwise.
resolve_prebuilt_image() {
  local repo="$1" refs_line state ref_main ref_monitor ref_ccr
  refs_line="$(_sccd_prebuilt_refs "$repo")"
  IFS=$'\t' read -r state ref_main ref_monitor ref_ccr <<<"$refs_line"

  case "$state" in
  candidate) ;;
  dirty)
    echo "claude: uncommitted changes to image inputs (.devcontainer/ or .claude/hooks/) — building the sandbox image locally." >&2
    return 0
    ;;
  *) return 0 ;; # disabled / no-git / no-remote: build locally, no message
  esac

  # _sccd_prebuilt_refs already proved this is a github.com remote; re-derive the
  # owner + commit it encoded so verification can pin to them. Also extract the
  # repo name to tighten the cosign identity regexp to this specific repo.
  local owner sha repo_name
  owner="$(_sccd_ghcr_owner "$repo")" || return 0
  repo_name="$(_sccd_ghcr_repo_name "$repo")" || repo_name=""
  sha="${ref_main##*:git-}"
  local -a refs=("$ref_main" "$ref_monitor" "$ref_ccr")
  local -a bases=("${_CLAUDE_GUARD_IMAGE_BASES[@]}")

  # Fast path: the verified bytes for this commit are already on disk, so skip
  # the registry manifest check, the pull, and cosign — zero network. The cache
  # is digest-keyed, so a swapped local image misses and falls through below.
  if _sccd_verified_cache_hit "$sha" "${refs[@]}"; then
    echo "claude: prebuilt sandbox image already verified for this commit — skipping pull." >&2
    _sccd_export_pinned "${refs[@]}"
    return 0
  fi

  # Fast path: a CLEAN local build for this commit is already on disk with the
  # image IDs record_local_build wrote after the last build — so the :local set
  # matches the current (candidate-clean) inputs and needs no rebuild. Export the
  # same no-build pin the prebuilt path uses (the launcher then strips compose's
  # build sections) but at the :local tags. A swapped/rebuilt image carries a
  # different ID, misses the cache, and falls through to a rebuild below.
  if _sccd_local_built_cache_hit "$sha" "${bases[@]}"; then
    echo "claude: local sandbox image already built for this commit — skipping rebuild." >&2
    _sccd_export_pinned "${bases[0]}:local" "${bases[1]}:local" "${bases[2]}:local"
    return 0
  fi

  # A local image build (the :local compose defaults) already exists — prefer it
  # over pulling the prebuilt. A clean-checkout build is bytes you produced
  # yourself, so it needs no cosign; returning without exporting CLAUDE_GUARD_* leaves
  # compose on its :local / pull_policy=build defaults, and the launch's
  # `docker compose build` reconciles the tag to the current (candidate-clean)
  # inputs — so it never runs stale bytes even if :local was built elsewhere.
  if _sccd_local_image_set_present; then
    echo "claude: local sandbox image present — using it instead of pulling the prebuilt." >&2
    return 0
  fi

  # Not cached locally: confirm the registry has the set before pulling.
  # publish-image.yaml pushes all three together, so the main image's presence
  # implies the set. Metadata only, no layer download.
  if ! docker manifest inspect "$ref_main" >/dev/null 2>&1; then
    echo "claude: no prebuilt image for this commit — building locally (CLAUDE_GUARD_NO_PREBUILT=1 to always build)." >&2
    return 0
  fi

  echo "claude: pulling prebuilt sandbox image (skips local build)..." >&2
  # Pull the three independent images concurrently (the slow network step), then
  # verify serially (cheap) once all are on disk.
  local ref pid ok=1
  local -a pids=()
  for ref in "${refs[@]}"; do
    docker pull "$ref" >/dev/null 2>&1 &
    pids+=("$!")
  done
  for pid in "${pids[@]}"; do wait "$pid" || ok=0; done
  [[ "$ok" == 1 ]] || {
    echo "claude: prebuilt image pull failed — building locally instead." >&2
    return 0
  }

  local i digest digest_ref cache_content=""
  for i in "${!refs[@]}"; do
    # Verify the exact bytes by digest (the tag is mutable, the digest is not).
    digest="$(_sccd_local_digest "${refs[i]}")" || {
      echo "claude: could not resolve digest for ${refs[i]} — building locally instead." >&2
      return 0
    }
    digest_ref="${refs[i]%%:*}@${digest}"
    _sccd_verify_image "$owner" "$sha" "$digest_ref" "$repo_name" || {
      echo "claude: prebuilt image failed cosign verification (${refs[i]}) — building locally instead." >&2
      return 0
    }
    _sccd_maybe_sbom_diff "$digest_ref" "${bases[i]}"
    cache_content+="${bases[i]} ${digest}"$'\n'
  done

  # Record the verified digests so the next launch on this commit hits the fast path.
  _sccd_cache_save verified-images "$sha" "$cache_content"
  _sccd_export_pinned "${refs[@]}"
}

# record_local_build <repo> — after a successful local-build launch, record the
# :local image set for this commit so the next launch on it takes
# resolve_prebuilt_image's local-built fast path and skips the rebuild. No-op
# unless the tree is candidate-clean: a dirty build's :local must not be trusted
# as the commit's image (its differing image ID also fails the hit check, so a
# stale record from an earlier clean build is safe). Best-effort; never fails the
# launch. Call it only when no prebuilt pin was set (i.e. a local build ran).
record_local_build() {
  local repo="$1" line state ref_main sha
  line="$(_sccd_prebuilt_refs "$repo")"
  IFS=$'\t' read -r state ref_main _ <<<"$line"
  [[ "$state" == "candidate" ]] || return 0
  sha="${ref_main##*:git-}"
  _sccd_local_built_cache_save "$sha" "${_CLAUDE_GUARD_IMAGE_BASES[@]}"
}

# prewarm_sandbox_image <repo> — get the sandbox images onto disk NOW (at install
# time) so the first `claude` launch doesn't pay the build/pull cost mid-session.
# Pulls the verified prebuilt set when one matches this commit (via
# resolve_prebuilt_image), else builds the compose images locally; compose build
# needs no running container, so this warms the image without starting the sandbox.
# Best-effort and non-fatal — a failure just defers the cost to first launch.
# Opt out with CLAUDE_GUARD_NO_PREWARM=1.
prewarm_sandbox_image() {
  local repo="$1"
  [[ "${CLAUDE_GUARD_NO_PREWARM:-}" == "1" ]] && return 0
  command -v docker >/dev/null 2>&1 || return 0

  # resolve_prebuilt_image exports CLAUDE_GUARD_PULL_POLICY only when it pulled and
  # verified a prebuilt set, so its presence means the images are already here.
  resolve_prebuilt_image "$repo"
  if [[ -n "${CLAUDE_GUARD_PULL_POLICY:-}" ]]; then
    echo "claude: prebuilt sandbox image ready — the first launch skips the build." >&2
    return 0
  fi

  local compose="$repo/.devcontainer/docker-compose.yml"
  [[ -f "$compose" ]] || return 0
  echo "claude: building the sandbox image locally so the first launch doesn't have to (this can take several minutes)..." >&2
  # Show meaningful BuildKit progress (step starts, CACHED/DONE/ERROR). Plain ERE,
  # NOT a `(?!...)` lookahead: lookahead is PCRE, which `grep -E` rejects — BSD grep
  # (macOS) aborts and `set -o pipefail` would propagate that and KILL the build.
  # Gate success on the BUILD's exit via PIPESTATUS, not grep's — a grep that matches
  # nothing exits 1 and is not a build failure.
  (
    docker compose -f "$compose" build --progress=plain 2>&1 |
      grep --line-buffered -E '^#[0-9]+ (\[|CACHED|DONE |ERROR)' >&2
    exit "${PIPESTATUS[0]}"
  ) || {
    # Loud, not a one-liner: a failed prewarm means there is NO sandbox image, which
    # 'claude-guard doctor' reports as DEGRADED/UNPROTECTED. Best-effort by contract,
    # so we warn and return success rather than abort setup — the launch retries the build.
    echo "claude: WARNING: prewarm build FAILED — no sandbox image was built." >&2
    echo "claude:   'claude-guard' will retry the build on first launch; if it keeps failing," >&2
    echo "claude:   run 'docker compose -f .devcontainer/docker-compose.yml build' to see the" >&2
    echo "claude:   error, or run 'claude-guard doctor' to check launch readiness." >&2
  }
}
