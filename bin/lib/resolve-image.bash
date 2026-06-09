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
