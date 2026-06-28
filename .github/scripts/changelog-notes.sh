#!/usr/bin/env bash
# changelog-notes.sh VERSION [CHANGELOG] — print the released-version section of
# a Keep-a-Changelog file (the body under `## [VERSION] - DATE`, up to the next
# `## ` header), for use as GitHub Release notes. Errors if the section is
# missing or empty: a release without its CHANGELOG section means the release
# flow skipped the curation step, which should fail loudly, not publish blank
# notes.
set -euo pipefail

if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo "usage: changelog-notes.sh VERSION [CHANGELOG]" >&2
  exit 2
fi
version="$1"
changelog="${2:-CHANGELOG.md}"

if [[ ! -r "$changelog" ]]; then
  echo "Error: cannot read $changelog" >&2
  exit 1
fi

# Command substitution strips trailing blank lines; the sed strips leading ones.
notes=$(awk -v ver="$version" '
  # Literal match on the header prefix; version dots must not act as regex dots.
  index($0, "## [" ver "]") == 1 { found = 1; next }
  found && /^## / { exit }
  found { print }
' "$changelog" | sed -e '/./,$!d')

if [[ -z "$notes" ]]; then
  echo "Error: no CHANGELOG section found for version $version in $changelog" >&2
  exit 1
fi

# GitHub rejects a release body over 125,000 characters (HTTP 422), so a large
# release — many rolled-up fragments, e.g. the first cut after a manual gap —
# would fail `gh release create` outright. Cap the notes well under the limit
# and append a pointer to the full CHANGELOG rather than fail the release. Cut
# on a line boundary so a category heading or bullet is never split mid-line.
max_chars=120000
footer=$(printf '\n\n_Release notes truncated — see CHANGELOG.md for the complete v%s changelog._' "$version")
if ((${#notes} > max_chars)); then
  head=${notes:0:max_chars-${#footer}}
  notes="${head%$'\n'*}${footer}"
fi

printf '%s\n' "$notes"
