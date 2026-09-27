#!/usr/bin/env bash
# Delete an untagged GHCR package version by manifest digest.
#
#   ghcr-delete-digest.sh OWNER PACKAGE sha256:...
#
# Used to drop a build candidate that failed its tests, so failed builds do
# not pile up in the package. Needs GH_TOKEN with packages write on a package
# owned by the workflow's repository. Refuses to delete a version that has
# tags, since that would take a published image with it.
set -euo pipefail

OWNER=${1:?owner}
PACKAGE=${2:?package}
DIGEST=${3:?digest}

owner_type=$(gh api "users/${OWNER}" --jq .type)
case "${owner_type}" in
  Organization) base="orgs/${OWNER}" ;;
  *) base="users/${OWNER}" ;;
esac

version=$(gh api --paginate "${base}/packages/container/${PACKAGE}/versions" \
  --jq ".[] | select(.name == \"${DIGEST}\") | {id, tags: .metadata.container.tags}" | head -1)

if [[ -z "${version}" ]]; then
  echo "no package version found for ${DIGEST}"
  exit 0
fi

id=$(jq -r .id <<< "${version}")
tags=$(jq -r '.tags | join(",")' <<< "${version}")
if [[ -n "${tags}" ]]; then
  echo "refusing to delete ${DIGEST}: it carries tags ${tags}"
  exit 0
fi

gh api -X DELETE "${base}/packages/container/${PACKAGE}/versions/${id}"
echo "deleted untagged version ${id} (${DIGEST})"
