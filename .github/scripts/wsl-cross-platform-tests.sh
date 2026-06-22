#!/usr/bin/env bash
# Run the @pytest.mark.cross_platform host-test selection inside a WSL2 distro on
# a Windows runner. WSL2 is a third host environment the bash host tooling
# (setup.bash, the bin/claude* wrappers) ships into, distinct from native Linux
# and macOS, and it has two materially different filesystem layouts a user can
# put the repo on:
#
#   1. native ext4   — the distro's own filesystem; real Linux semantics.
#   2. DrvFs (/mnt/c) — the Windows drive seen over the 9P/DrvFs bridge, where
#                       case-insensitivity, symlink emulation and chmod/metadata
#                       diverge from a real Linux FS.
#
# We exercise both. The marker is the single source of truth for what runs — the
# same selection the ubuntu/macOS legs run — so there is no WSL-specific test set
# to keep in sync. setup-wsl runs this with cwd = the GitHub workspace, which
# lives on the Windows drive, so $(pwd) is the DrvFs checkout.
set -euo pipefail

drvfs_repo="$PWD"
native_repo="$HOME/claude-guard"

# setup-wsl runs this with cwd = the GitHub workspace (the DrvFs checkout). Fail
# loud if that assumption breaks rather than letting `uv run` die cryptically on
# a missing pyproject downstream.
if [[ ! -f "${drvfs_repo}/pyproject.toml" ]]; then
  echo "error: expected the repo checkout at cwd (${drvfs_repo}), but no pyproject.toml is there" >&2
  exit 1
fi

# Ensure uv is on PATH, installing the standalone binary if the image lacks it.
# uv fetches its own Python, so the distro needs no system python/toolchain.
ensure_uv() {
  # shellcheck source=/dev/null
  [[ -f "$HOME/.local/bin/env" ]] && source "$HOME/.local/bin/env"
  command -v uv >/dev/null 2>&1 && return
  curl -LsSf https://astral.sh/uv/install.sh | sh
  # shellcheck source=/dev/null
  source "$HOME/.local/bin/env"
}

# Run the cross_platform selection from $dir; extra args pass through to pytest
# (used to pin --basetemp onto the filesystem under test).
run_selection() {
  local dir="$1" label="$2"
  shift 2
  echo "::group::cross_platform host tests — ${label} (${dir})"
  (cd "$dir" && uv run --extra dev pytest -m cross_platform -v --tb=short "$@")
  echo "::endgroup::"
}

ensure_uv

# Native ext4 first (fast; warms the uv cache that the DrvFs run reuses). A clean
# copy without any .venv — a uv venv is path-specific, so it can't be shared
# across the two checkouts.
rm -rf "$native_repo"
cp -a "$drvfs_repo" "$native_repo"
run_selection "$native_repo" "native ext4"

# DrvFs: pin --basetemp onto the Windows drive so the tmp_path fixtures the tests
# build their trees in actually land on DrvFs. Without this, pytest's default
# basetemp (/tmp, ext4) would make this run a duplicate of the native one. Place
# it beside the checkout (still on DrvFs) rather than inside it, so the run never
# litters the working tree.
drvfs_tmp="$(dirname "$drvfs_repo")/cg-pytest-drvfs-tmp"
run_selection "$drvfs_repo" "DrvFs (Windows drive)" --basetemp="$drvfs_tmp"
