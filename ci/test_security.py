"""Basic security posture of a running container.

Nothing in here is a vulnerability scan; that runs separately against the
SBOM. These are the invariants a broken build or a bad default would violate.
"""

from __future__ import annotations

import time
import uuid

import pytest
from conftest import Station, start_container, stop_container

# selkies-desktop only runs while a desktop session is active, so it is not listed.
UNPRIVILEGED_PROCESSES = {"webstation-brok", "selkies", "labwc", "pulseaudio"}
LOOPBACK_ONLY_PORTS = {8000, 8082, 5000}
PUBLIC_PORTS = {3000, 3001}


def test_services_run_as_abc(station: Station) -> None:
    rows = station.sh("ps -eo user:20,comm --no-headers").splitlines()
    owners: dict[str, set[str]] = {}
    for row in rows:
        parts = row.split(None, 1)
        if len(parts) == 2:
            owners.setdefault(parts[1].strip(), set()).add(parts[0])
    for proc in UNPRIVILEGED_PROCESSES:
        assert proc in owners, f"{proc} is not running"
        assert owners[proc] == {"abc"}, f"{proc} runs as {owners[proc]}, expected abc"


def test_internal_ports_bound_to_loopback(station: Station) -> None:
    rows = station.sh("ss -ltnH").splitlines()
    wildcard_ports = set()
    seen = set()
    for row in rows:
        local = row.split()[3]
        host, _, port = local.rpartition(":")
        port = int(port)
        seen.add(port)
        if host in ("0.0.0.0", "*", "[::]", "::"):
            wildcard_ports.add(port)
    assert LOOPBACK_ONLY_PORTS <= seen, f"expected internal listeners missing: {LOOPBACK_ONLY_PORTS - seen}"
    assert not (wildcard_ports & LOOPBACK_ONLY_PORTS), (
        f"internal ports bound to all interfaces: {sorted(wildcard_ports & LOOPBACK_ONLY_PORTS)}"
    )
    assert wildcard_ports <= PUBLIC_PORTS, (
        f"unexpected public listeners: {sorted(wildcard_ports - PUBLIC_PORTS)}"
    )


def test_dev_mode_is_off(station: Station) -> None:
    r = station.exec("test", "-e", "/run/s6/container_environment/BROKER_DEV_MODE", check=False)
    assert r.returncode != 0, "BROKER_DEV_MODE is set in the container environment"
    assert "dev mode" not in station.logs().lower()


def test_master_token_not_exposed(station: Station) -> None:
    token = station.sh("cat /run/s6/container_environment/SELKIES_MASTER_TOKEN").strip()
    assert len(token) >= 32, "master token missing or too short"
    with station.client() as c:
        bodies = [
            c.get("/").text,
            c.get("/stream/").text,
            c.get("/api/health").text,
            c.get("/openapi.json").text,
            c.get("/api/session/status", headers=station.secret_headers).text,
        ]
    assert not any(token in b for b in bodies), "SELKIES_MASTER_TOKEN appears in an HTTP response"


def test_broker_secret_not_in_logs(station: Station) -> None:
    assert station.secret not in station.logs(), "BROKER_SECRET is printed in the container log"


@pytest.mark.slow
def test_refuses_to_start_without_secret(image: str) -> None:
    """Without BROKER_SECRET the broker must refuse rather than start open."""
    st = start_container(image, secret=None, name=f"webstation-ci-nosecret-{uuid.uuid4().hex[:8]}")
    try:
        deadline = time.monotonic() + 180
        refused = False
        while time.monotonic() < deadline:
            log = st.logs()
            if "BROKER_SECRET" in log:
                refused = True
                break
            time.sleep(3)
        assert refused, f"broker never complained about a missing BROKER_SECRET\n{st.logs()[-3000:]}"
        assert not st.health_ok(), "broker answered health with no BROKER_SECRET set"
    finally:
        stop_container(st, "container-nosecret.log")
