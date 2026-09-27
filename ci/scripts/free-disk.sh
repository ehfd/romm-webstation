#!/usr/bin/env bash
# Reclaim disk on a GitHub hosted runner before building or pulling the image.
#
# The runtime image is roughly ten gigabytes uncompressed and the build cache
# for the three compiled emulators is larger still, while the runner's root
# disk only guarantees about fourteen gigabytes free. Two things help:
#   1. delete the preinstalled toolchains nothing here uses
#   2. move docker's data root onto the runner's second disk at /mnt when it
#      has more room than the root disk
# Prints df before and after so the run log shows what we actually got.
set -euo pipefail

echo "::group::disk before"
df -h / /mnt 2>/dev/null || df -h /
echo "::endgroup::"

echo "::group::remove preinstalled toolchains"
sudo rm -rf \
  /usr/share/dotnet \
  /usr/local/lib/android \
  /opt/ghc \
  /usr/local/.ghcup \
  /opt/hostedtoolcache/CodeQL \
  /usr/local/share/boost \
  /usr/lib/jvm \
  /usr/share/swift \
  /usr/local/share/powershell \
  /usr/local/julia* \
  /opt/microsoft \
  /usr/share/miniconda \
  /opt/az \
  /usr/local/share/chromium \
  /usr/local/lib/heroku 2>/dev/null || true
sudo docker image prune -af >/dev/null 2>&1 || true
sudo apt-get clean >/dev/null 2>&1 || true
echo "::endgroup::"

if [[ -d /mnt ]]; then
  root_avail=$(df --output=avail -B1 / | tail -1)
  mnt_avail=$(df --output=avail -B1 /mnt | tail -1)
  if (( mnt_avail > root_avail )); then
    echo "::group::move docker data root to /mnt/docker"
    sudo systemctl stop docker.socket docker.service
    sudo mkdir -p /mnt/docker
    if [[ -f /etc/docker/daemon.json ]]; then
      sudo jq '. + {"data-root": "/mnt/docker"}' /etc/docker/daemon.json | sudo tee /etc/docker/daemon.json.new >/dev/null
      sudo mv /etc/docker/daemon.json.new /etc/docker/daemon.json
    else
      echo '{"data-root": "/mnt/docker"}' | sudo tee /etc/docker/daemon.json >/dev/null
    fi
    sudo systemctl start docker
    docker info --format 'docker root: {{.DockerRootDir}}'
    echo "::endgroup::"
  fi
fi

echo "::group::disk after"
df -h / /mnt 2>/dev/null || df -h /
echo "::endgroup::"
