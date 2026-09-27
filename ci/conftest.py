"""Smoke test harness for the romm-webstation image.

Every test runs against a real container started from the image named by
WEBSTATION_IMAGE. The container gets a known BROKER_SECRET, no GPU, and the
default SUBFOLDER, which is exactly what a GitHub hosted runner can offer.

Environment knobs (all optional except the image):

  WEBSTATION_IMAGE            image reference to test (required)
  WEBSTATION_SECRET           BROKER_SECRET handed to the container (default ci-secret)
  WEBSTATION_ARTIFACTS        where screenshots and logs land (default ci/artifacts)
  WEBSTATION_STARTUP_TIMEOUT  seconds to wait for /api/health (default 240)
  WEBSTATION_KEEP             set to 1 to leave the container running after the run
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest

SUBFOLDER = "/streaming/"
STARTUP_TIMEOUT = int(os.environ.get("WEBSTATION_STARTUP_TIMEOUT", "240"))
ARTIFACTS = Path(os.environ.get("WEBSTATION_ARTIFACTS", Path(__file__).parent / "artifacts"))


def _sh(*cmd: str, check: bool = True, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(list(cmd), capture_output=True, text=True, check=check, timeout=timeout)


@dataclass
class Station:
    """Handle on a running webstation container."""

    name: str
    image: str
    secret: str
    http_port: int
    https_port: int

    @property
    def base(self) -> str:
        return f"https://127.0.0.1:{self.https_port}{SUBFOLDER.rstrip('/')}"

    @property
    def ws_base(self) -> str:
        return f"wss://127.0.0.1:{self.https_port}{SUBFOLDER.rstrip('/')}"

    @property
    def secret_headers(self) -> dict[str, str]:
        return {"X-Broker-Secret": self.secret}

    def client(self) -> httpx.Client:
        return httpx.Client(base_url=self.base, verify=False, timeout=30)

    def exec(self, *cmd: str, check: bool = True, timeout: int = 60) -> subprocess.CompletedProcess:
        return _sh("docker", "exec", self.name, *cmd, check=check, timeout=timeout)

    def sh(self, script: str, check: bool = True, timeout: int = 60) -> str:
        return self.exec("bash", "-c", script, check=check, timeout=timeout).stdout

    def logs(self) -> str:
        r = _sh("docker", "logs", self.name, check=False)
        return r.stdout + r.stderr

    def health_ok(self) -> bool:
        try:
            with self.client() as c:
                return c.get("/api/health").json().get("status") == "ok"
        except (httpx.HTTPError, ValueError):
            return False


def start_container(
    image: str, secret: str | None, name: str, extra_env: dict[str, str] | None = None
) -> Station:
    env = {
        "PUID": "1000",
        "PGID": "1000",
        "TZ": "Etc/UTC",
        "SUBFOLDER": SUBFOLDER,
    }
    if secret is not None:
        env["BROKER_SECRET"] = secret
    env.update(extra_env or {})
    cmd = [
        "docker",
        "run",
        "-d",
        "--name",
        name,
        "--shm-size=1gb",
        "-p",
        "127.0.0.1::3000",
        "-p",
        "127.0.0.1::3001",
    ]
    for k, v in env.items():
        cmd += ["-e", f"{k}={v}"]
    cmd.append(image)
    _sh(*cmd)
    ports = json.loads(_sh("docker", "inspect", name, "-f", "{{json .NetworkSettings.Ports}}").stdout)
    http_port = int(ports["3000/tcp"][0]["HostPort"])
    https_port = int(ports["3001/tcp"][0]["HostPort"])
    return Station(
        name=name,
        image=image,
        secret=secret or "",
        http_port=http_port,
        https_port=https_port,
    )


def wait_for_health(station: Station, timeout: int) -> float:
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        state = _sh("docker", "inspect", station.name, "-f", "{{.State.Status}}", check=False).stdout.strip()
        if state not in ("running", "created"):
            raise RuntimeError(f"container {station.name} is {state}\n{station.logs()[-4000:]}")
        if station.health_ok():
            return time.monotonic() - started
        time.sleep(2)
    raise TimeoutError(f"broker health did not come up in {timeout}s\n{station.logs()[-6000:]}")


def stop_container(station: Station, log_name: str) -> None:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    (ARTIFACTS / log_name).write_text(station.logs())
    if os.environ.get("WEBSTATION_KEEP") == "1":
        return
    _sh("docker", "rm", "-f", station.name, check=False)


@pytest.fixture(scope="session")
def image() -> str:
    img = os.environ.get("WEBSTATION_IMAGE")
    if not img:
        pytest.exit("WEBSTATION_IMAGE is not set", returncode=2)
    return img


@pytest.fixture(scope="session")
def station(image: str) -> Station:
    """The shared container every test module talks to."""
    secret = os.environ.get("WEBSTATION_SECRET", "ci-secret")
    name = f"webstation-ci-{uuid.uuid4().hex[:8]}"
    st = start_container(image, secret, name)
    try:
        boot = wait_for_health(st, STARTUP_TIMEOUT)
        st.boot_seconds = boot  # type: ignore[attr-defined]
    except Exception:
        stop_container(st, "container.log")
        raise
    yield st
    stop_container(st, "container.log")


@pytest.fixture(scope="session")
def artifacts() -> Path:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    return ARTIFACTS


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "slow: starts an additional container")
