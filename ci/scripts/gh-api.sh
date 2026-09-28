#!/bin/sh
# GET a GitHub REST API path from inside a Dockerfile RUN step.
#
#   gh-api.sh repos/OWNER/REPO/releases/latest
#
# Hosted runners share their egress IPs, and anonymous API calls are limited
# to 60 an hour per IP, so an unauthenticated build fails at random partway
# through with an empty jq input. CI passes the workflow token as the BuildKit
# secret github_token; a local build without it falls back to anonymous.
# The script is bind mounted, never copied, so it leaves nothing in a layer.
# Outside a build (build.yml resolving emulator versions) it reads
# GITHUB_TOKEN from the environment instead.
set -eu

path=${1:?api path}
secret=/run/secrets/github_token

token=${GITHUB_TOKEN:-}
if [ -s "${secret}" ]; then
  token=$(cat "${secret}")
fi

if [ -n "${token}" ]; then
  exec curl -fsSL \
    -H "Authorization: Bearer ${token}" \
    -H "Accept: application/vnd.github+json" \
    "https://api.github.com/${path}"
fi
exec curl -fsSL \
  -H "Accept: application/vnd.github+json" \
  "https://api.github.com/${path}"
