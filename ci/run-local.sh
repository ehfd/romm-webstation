#!/usr/bin/env bash
# Run the smoke suite against an image on this machine.
#
#   ci/run-local.sh [IMAGE] [pytest args...]
#
# Creates ci/.venv on first use. Screenshots and container logs land in
# ci/artifacts. Set WEBSTATION_KEEP=1 to leave the container running so you
# can poke at it afterwards.
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
IMAGE=${1:-ghcr.io/romm-streaming/romm-webstation:latest}
shift || true

VENV="${here}/.venv"
if [[ ! -x "${VENV}/bin/python" ]]; then
  python3 -m venv "${VENV}"
  "${VENV}/bin/pip" install -q -r "${here}/requirements.txt"
  "${VENV}/bin/python" -m playwright install chromium
fi

cd "${here}"
WEBSTATION_IMAGE="${IMAGE}" "${VENV}/bin/python" -m pytest "$@"
