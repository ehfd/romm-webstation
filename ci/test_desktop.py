"""Activate a real desktop session, land on it in a browser, and leave cleanly.

This is the closest thing to what a RomM user does: the broker launches the
session, the room page embeds the selkies stream, frames arrive, and exit
tears it all down. The screenshot is kept as a build artifact so a human can
eyeball what the image actually rendered.
"""

from __future__ import annotations

import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from conftest import Station
from PIL import Image, ImageStat
from test_auth import ws_probe

STREAM_START_TIMEOUT = 90
SETTLE_SECONDS = 6
MIN_STDDEV = 8.0


@pytest.fixture(scope="module")
def desktop(station: Station) -> dict:
    with station.client() as c:
        r = c.post(
            "/api/session/activate",
            headers=station.secret_headers,
            json={
                "emulator": "desktop",
                "user": {"id": 1, "username": "ci", "display_name": "CI"},
            },
        )
    assert r.status_code == 200, f"activate failed: {r.status_code} {r.text}"
    body = r.json()
    token = parse_qs(urlparse(body["url"]).query)["token"][0]
    yield {"response": body, "token": token, "url": f"{station.base}/?token={token}"}
    with station.client() as c:
        c.post("/api/session/exit", headers=station.secret_headers)


def test_activate_returns_room_url(desktop: dict) -> None:
    body = desktop["response"]
    assert body["status"] == "launching"
    assert body["session_id"]
    assert "token=" in body["url"]


def test_status_reports_desktop(station: Station, desktop: dict) -> None:
    deadline = time.monotonic() + 30
    while True:
        with station.client() as c:
            s = c.get("/api/session/status", headers=station.secret_headers).json()
        if s.get("active") and s.get("emulator_alive"):
            break
        assert time.monotonic() < deadline, f"session never became active: {s}"
        time.sleep(1)
    assert s["emulator"] == "desktop"
    assert s["session_id"] == desktop["response"]["session_id"]
    assert s["user"]["username"] == "ci"


def test_second_activate_conflicts(station: Station, desktop: dict) -> None:
    with station.client() as c:
        r = c.post(
            "/api/session/activate",
            headers=station.secret_headers,
            json={"emulator": "desktop"},
        )
    assert r.status_code == 409, r.text


def test_context_honours_token(station: Station, desktop: dict) -> None:
    with station.client() as c:
        bad = c.get("/api/session/context", params={"token": "bogus"})
        good = c.get("/api/session/context", params={"token": desktop["token"]})
    assert bad.status_code == 401, bad.text
    assert good.status_code == 200, good.text
    ctx = good.json()
    assert ctx["userRole"] == "controller"
    src = urlparse(ctx["iframeSrc"])
    carried = parse_qs(src.query).get("token", []) + parse_qs(src.fragment).get("token", [])
    assert src.path == "stream/" and carried == [desktop["token"]], ctx["iframeSrc"]


def test_stream_websocket_accepts_session_token(station: Station, desktop: dict) -> None:
    kind, detail = ws_probe(f"{station.ws_base}/stream/api/websockets?token={desktop['token']}")
    assert kind == "message" and detail.startswith("AUTH_SUCCESS"), f"{kind}: {detail!r}"


def test_stream_websocket_closes_bogus_token_during_session(station: Station, desktop: dict) -> None:
    """Once a session holds a token map, a wrong token must be closed with the auth failure code."""
    kind, detail = ws_probe(f"{station.ws_base}/stream/api/websockets?token=bogus")
    assert kind in ("rejected", "closed"), f"bogus token was not closed: {kind} {detail!r}"
    if kind == "closed":
        assert detail == "4001", f"unexpected close code {detail}"


def test_room_websocket_accepts_session_token(station: Station, desktop: dict) -> None:
    kind, detail = ws_probe(f"{station.ws_base}/ws/room?token={desktop['token']}")
    assert kind == "message", f"{kind}: {detail!r}"


def test_room_renders_desktop(station: Station, desktop: dict, artifacts: Path) -> None:
    """Open the room in headless Chromium, wait for frames, screenshot, and check it is a picture."""
    from playwright.sync_api import sync_playwright

    console: list[str] = []
    ws_urls: list[str] = []
    shot = artifacts / "desktop.png"
    with station.client() as c:
        iframe_src = c.get("/api/session/context", params={"token": desktop["token"]}).json()["iframeSrc"]
    with sync_playwright() as p:
        browser = p.chromium.launch(
            args=[
                "--ignore-certificate-errors",
                "--autoplay-policy=no-user-gesture-required",
                "--use-gl=swiftshader",
                "--enable-unsafe-swiftshader",
            ]
        )
        page = browser.new_context(
            ignore_https_errors=True, viewport={"width": 1280, "height": 800}
        ).new_page()
        page.on("console", lambda m: console.append(f"[{m.type}] {m.text}"))
        page.on("pageerror", lambda e: console.append(f"[pageerror] {e}"))
        page.on("websocket", lambda w: ws_urls.append(w.url))
        page.goto(desktop["url"], wait_until="load", timeout=60_000)

        page.wait_for_selector("iframe#session-frame", timeout=30_000)
        deadline = time.monotonic() + STREAM_START_TIMEOUT
        while time.monotonic() < deadline and not any("Stream started" in line for line in console):
            page.wait_for_timeout(1000)
        page.wait_for_timeout(SETTLE_SECONDS * 1000)
        page.screenshot(path=str(shot))
        browser.close()

    (artifacts / "desktop-console.log").write_text("\n".join(console))
    stream = [u for u in ws_urls if "/stream/api/websockets" in u]
    if "#token=" in iframe_src:
        # selkies offers a fragment token as a WebSocket subprotocol, so no URL carries it
        assert stream and not any("token=" in u for u in stream), f"stream websockets: {ws_urls}"
    else:
        assert any("?token=" in u for u in stream), f"no stream websocket opened: {ws_urls}"
    assert any("Stream started" in line for line in console), "selkies never reported the stream starting"

    im = Image.open(shot).convert("L")
    std = ImageStat.Stat(im).stddev[0]
    assert std > MIN_STDDEV, f"screenshot looks blank (stddev {std:.1f}); see {shot}"


def test_exit_tears_down(station: Station, desktop: dict) -> None:
    with station.client() as c:
        r = c.post("/api/session/exit", headers=station.secret_headers)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "exited"
        status = c.get("/api/session/status", headers=station.secret_headers).json()
        context = c.get("/api/session/context", params={"token": desktop["token"]})
    assert status == {"active": False}
    assert context.status_code in (401, 409)
