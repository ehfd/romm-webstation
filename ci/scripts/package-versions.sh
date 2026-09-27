#!/usr/bin/env bash
# Produce the package_versions.txt for an image.
#
#   package-versions.sh IMAGE OUTFILE [SBOM_JSON]
#
# The first part is syft's package table, the same output linuxserver.io
# tracks. The second part is the image's own versions manifest, one line per
# emulator that the Dockerfile resolved to an upstream "latest" at build time.
# Those are AppImages and tarballs syft cannot see, and they are the things
# most likely to change between two builds of the same broker release. The
# package hash used in image tags is the md5 of this whole file.
set -euo pipefail

IMAGE=${1:?image}
OUT=${2:?outfile}
SBOM=${3:-}
SYFT_IMAGE=${SYFT_IMAGE:-ghcr.io/anchore/syft:latest}

workdir=$(mktemp -d)
trap 'rm -rf "${workdir}"' EXIT

outputs=(-o "table=/out/table.txt")
if [[ -n "${SBOM}" ]]; then
  outputs+=(-o "syft-json=/out/sbom.json")
fi

docker run --rm \
  -v /var/run/docker.sock:/var/run/docker.sock:ro \
  -v "${workdir}:/out" \
  -e SYFT_CHECK_FOR_APP_UPDATE=false \
  "${SYFT_IMAGE}" scan "docker:${IMAGE}" "${outputs[@]}" >/dev/null

manifest=$(docker run --rm --entrypoint bash "${IMAGE}" -c '
  set -e
  cd /usr/share/webstation/versions.d
  for f in *; do printf "%-40s %s\n" "$f" "$(cat "$f")"; done')

{
  cat "${workdir}/table.txt"
  echo
  echo "WEBSTATION VERSIONS MANIFEST"
  echo "${manifest}"
} > "${OUT}"

if [[ -n "${SBOM}" ]]; then
  cp "${workdir}/sbom.json" "${SBOM}"
fi

echo "package hash: $(md5sum "${OUT}" | cut -c1-8)  ($(wc -l < "${OUT}") lines)"
