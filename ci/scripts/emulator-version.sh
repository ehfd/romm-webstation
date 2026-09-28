#!/bin/sh
# Print the upstream version the Dockerfile compiles for one emulator.
#
#   emulator-version.sh dolphin|eden|cemu
#
# build.yml runs this to name the cached emulator image before it builds
# anything; the Dockerfile runs it only when no version was passed in, which
# is a plain local build. One lookup for both, so they cannot disagree.
set -eu

here=$(dirname "$0")

case "${1:?emulator name}" in
  dolphin)
    # Dolphin stopped cutting GitHub releases; its release tags are YYMM.
    "${here}/gh-api.sh" "repos/dolphin-emu/dolphin/tags?per_page=50" \
      | jq -er '[.[].name | select(test("^[0-9]{4}[a-z]?$"))] | max'
    ;;
  cemu)
    "${here}/gh-api.sh" "repos/cemu-project/Cemu/releases/latest" | jq -er '.tag_name'
    ;;
  eden)
    curl -fsSL 'https://git.eden-emu.dev/api/v1/repos/eden-emu/eden/releases/latest' | jq -er '.tag_name'
    ;;
  *)
    echo "unknown emulator: $1" >&2
    exit 2
    ;;
esac
