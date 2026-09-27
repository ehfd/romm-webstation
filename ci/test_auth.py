"""Token and secret enforcement on every surface the container exposes.

Nothing here is HTTP basic auth. The broker guards its lifecycle routes with
the X-Broker-Secret header, the room and the selkies stream with per session
tokens, and the selkies control plane with a master token that must never be
reachable from outside.
"""

from __future__ import annotations

import asyncio
import ssl

import pytest
import websockets
from conftest import Station

# Bodies and query strings are well formed on purpose: FastAPI validates the
# request shape before the handler runs its secret check, so a malformed
# request would answer 422 and never exercise the auth path.
LIFECYCLE_ROUTES = [
    ("POST", "/api/session/activate", {"emulator": "desktop"}),
    ("POST", "/api/session/join", {"user": {"id": 2, "username": "x"}}),
    ("POST", "/api/session/exit", None),
    ("POST", "/api/session/save-state", {}),
    ("POST", "/api/session/load-state", {}),
    ("POST", "/api/session/swap-disc", {"path": "/romm/library/disc2.chd"}),
    ("GET", "/api/session/exports", None),
    ("GET", "/api/session/memory-card?emulator=pcsx2", None),
]


def _ssl() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


async def ws_first_message(url: str, timeout: float = 8) -> tuple[str, str]:
    """Return ("rejected", detail), ("closed", code) or ("message", text)."""
    try:
        async with websockets.connect(url, ssl=_ssl(), open_timeout=10) as ws:
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout)
                return "message", str(msg)
            except TimeoutError:
                return "message", ""
            except websockets.ConnectionClosed as e:
                return "closed", str(e.code)
    except websockets.InvalidStatus as e:
        return "rejected", str(e.response.status_code)
    except Exception as e:  # noqa: BLE001
        return "rejected", type(e).__name__


def ws_probe(url: str) -> tuple[str, str]:
    return asyncio.run(ws_first_message(url))


@pytest.mark.parametrize("method,path,body", LIFECYCLE_ROUTES, ids=[r[1] for r in LIFECYCLE_ROUTES])
def test_lifecycle_routes_require_secret(station: Station, method: str, path: str, body) -> None:
    with station.client() as c:
        no_secret = c.request(method, path, json=body)
        wrong = c.request(method, path, json=body, headers={"X-Broker-Secret": "not-the-secret"})
    assert no_secret.status_code == 403, f"{path} without secret: {no_secret.status_code} {no_secret.text}"
    assert wrong.status_code == 403, f"{path} wrong secret: {wrong.status_code} {wrong.text}"


def test_status_does_not_leak_without_secret(station: Station) -> None:
    """Status is either closed (current broker) or minimal; never a 5xx."""
    with station.client() as c:
        r = c.get("/api/session/status")
    assert r.status_code in (200, 403), r.text
    if r.status_code == 200:
        assert set(r.json()) <= {"active"}, "unauthenticated status leaks session detail"


def test_health_is_open(station: Station) -> None:
    with station.client() as c:
        assert c.get("/api/health").status_code == 200


def test_context_rejects_bogus_token(station: Station) -> None:
    with station.client() as c:
        r = c.get("/api/session/context", params={"token": "bogus"})
    assert r.status_code in (401, 409), r.text


def test_selkies_token_endpoint_closed(station: Station) -> None:
    """The master token API is what mints stream tokens. Only the broker may reach it."""
    with station.client() as c:
        get = c.get("/stream/api/tokens")
        post = c.post("/stream/api/tokens", json={})
        bearer = c.post("/stream/api/tokens", json={}, headers={"Authorization": "Bearer bogus"})
    assert get.status_code in (401, 403, 404, 405), get.text
    assert post.status_code in (401, 403), post.text
    assert bearer.status_code in (401, 403), bearer.text


def test_stream_websocket_rejects_missing_token(station: Station) -> None:
    kind, detail = ws_probe(f"{station.ws_base}/stream/api/websockets")
    assert kind == "rejected", f"stream websocket accepted a connection with no token: {kind} {detail}"


def test_stream_websocket_rejects_bogus_token(station: Station) -> None:
    """With no session there is no token map; selkies must at least never authenticate."""
    kind, detail = ws_probe(f"{station.ws_base}/stream/api/websockets?token=bogus")
    assert not detail.startswith("AUTH_SUCCESS"), f"stream websocket authenticated a bogus token: {detail!r}"
    assert kind != "message" or detail == "", f"stream websocket sent data to a bogus token: {detail!r}"


@pytest.mark.parametrize("query", ["", "?token=bogus"], ids=["no-token", "bogus-token"])
def test_room_websocket_rejects(station: Station, query: str) -> None:
    kind, detail = ws_probe(f"{station.ws_base}/ws/room{query}")
    assert kind in ("rejected", "closed"), f"room websocket accepted {query!r}: {detail!r}"
