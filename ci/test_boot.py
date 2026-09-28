"""The container boots, every service comes up, and the emulators are all there."""

from __future__ import annotations

import re
import shlex

import pytest
from conftest import Station

# Services the romm overlay and the selkies base must both bring up. If one of
# these is missing from s6's active list the desktop or the broker is not
# actually running, whatever the health endpoint says.
EXPECTED_SERVICES = {
    "init-romm-config",
    "svc-broker",
    "svc-de",
    "svc-dbus",
    "svc-nginx",
    "svc-pulseaudio",
    "svc-selkies",
}

# Emulators the Dockerfile compiles or unpacks from an upstream "latest"
# release. Their versions must be recorded in the image, or the package hash
# in the tag would not move when they update.
EXPECTED_VERSION_ENTRIES = {
    "azahar",
    "cemu",
    "dolphin",
    "dosbox-staging",
    "duckstation",
    "eden",
    "es-de",
    "flips",
    "flycast",
    "freedoom",
    "gzdoom",
    "melonds",
    "modrinth",
    "romm-broker",
    "rpcs3",
    "shadps4-pkg-extractor",
    "shadps4-qtlauncher",
    "xemu",
    "xenia-edge",
}


def test_health(station: Station) -> None:
    with station.client() as c:
        r = c.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_status_reports_no_session(station: Station) -> None:
    with station.client() as c:
        r = c.get("/api/session/status", headers=station.secret_headers)
    assert r.status_code == 200
    assert r.json() == {"active": False}


def test_all_services_active(station: Station) -> None:
    active = set(station.sh("s6-rc -a list").split())
    missing = EXPECTED_SERVICES - active
    assert not missing, f"services not active: {sorted(missing)}"


def test_broker_started_exactly_once(station: Station) -> None:
    """A second uvicorn start means svc-broker crashed and s6 restarted it."""
    starts = station.logs().count("Started server process")
    assert starts == 1, f"broker started {starts} times, expected once"


def test_desktop_launchers_resolve(station: Station) -> None:
    """Every .desktop shipped in /defaults must point at a binary that exists.

    This is the cheapest way to catch an upstream release that renamed or
    dropped its AppImage: the download step would still succeed on an empty
    or wrong file and the launcher would silently do nothing.
    """
    execs = station.sh(r"grep -h '^Exec=' /defaults/desktop/*.desktop | sed 's/^Exec=//'").splitlines()
    assert execs, "no launchers found under /defaults/desktop"
    missing = []
    for line in execs:
        words = shlex.split(line)
        # Skip an `env` prefix and its VAR=value assignments to get to the binary.
        if words and words[0] == "env":
            words = [w for w in words[1:] if "=" not in w]
        binary = words[0]
        r = station.exec("bash", "-lc", f"command -v {shlex.quote(binary)}", check=False)
        if r.returncode != 0:
            missing.append(binary)
    assert not missing, f"launcher binaries missing: {missing}"


def test_versions_manifest_complete(station: Station) -> None:
    r = station.exec("ls", "/usr/share/webstation/versions.d", check=False)
    if r.returncode != 0:
        pytest.skip("image predates the versions manifest")
    present = set(r.stdout.split())
    missing = EXPECTED_VERSION_ENTRIES - present
    assert not missing, f"versions manifest is missing: {sorted(missing)}"
    empty = [n for n in present if not station.sh(f"cat /usr/share/webstation/versions.d/{n}").strip()]
    assert not empty, f"versions manifest entries are empty: {empty}"


def test_build_version_file(station: Station) -> None:
    r = station.exec("cat", "/build_version", check=False)
    if r.returncode != 0:
        pytest.skip("image predates /build_version")
    assert re.search(r"romm-broker:\s+\S+", r.stdout), r.stdout


def test_broker_package_installed(station: Station) -> None:
    out = station.sh("pip show webstation-broker 2>/dev/null | awk '/^Version:/ {print $2}'")
    assert re.fullmatch(r"\d+\.\d+\.\d+", out.strip()), f"unexpected broker version {out!r}"


# Built in their own images and cached by upstream version (build.yml), so
# they can be older than the runtime image they are copied into. A library
# the base dropped or bumped a soname on shows up here, not at launch.
COMPILED_EMULATORS = ("/usr/local/bin/dolphin-emu", "/usr/bin/eden", "/usr/bin/Cemu")


@pytest.mark.parametrize("binary", COMPILED_EMULATORS)
def test_compiled_emulator_links(station: Station, binary: str) -> None:
    r = station.exec("ldd", binary, check=False)
    assert r.returncode == 0, f"ldd {binary} failed: {r.stdout}{r.stderr}"
    unresolved = [line.strip() for line in r.stdout.splitlines() if "not found" in line]
    assert not unresolved, f"{binary} has unresolved libraries: {unresolved}"
