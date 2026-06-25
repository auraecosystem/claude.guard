#!/usr/bin/env bash
# smoke-install-linux-pkg.sh <deb|rpm> — install the package built by
# build-nfpm-packages.sh (downloaded into packaging/nfpm/dist) with the native
# package manager so its declared deps resolve, then run the shared smoke.
set -euo pipefail

fmt="${1:?usage: smoke-install-linux-pkg.sh <deb|rpm>}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
DIST_DIR="$REPO_ROOT/packaging/nfpm/dist"

shopt -s nullglob
case "$fmt" in
deb)
  pkgs=("$DIST_DIR"/*.deb)
  [[ ${#pkgs[@]} -gt 0 ]] || {
    echo "FAIL: no .deb found in $DIST_DIR" >&2
    exit 1
  }
  sudo apt-get update
  sudo apt-get install -y "${pkgs[0]}"
  ;;
rpm)
  pkgs=("$DIST_DIR"/*.rpm)
  [[ ${#pkgs[@]} -gt 0 ]] || {
    echo "FAIL: no .rpm found in $DIST_DIR" >&2
    exit 1
  }
  # The rpm leg runs as root inside a Fedora container, so dnf needs no sudo.
  dnf install -y "${pkgs[0]}"
  ;;
*)
  echo "FAIL: unknown format '$fmt' (want deb or rpm)" >&2
  exit 1
  ;;
esac

bash "$SCRIPT_DIR/smoke-assert-claude-guard.sh"
