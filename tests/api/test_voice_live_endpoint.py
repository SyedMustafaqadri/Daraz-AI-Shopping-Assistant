"""Tests for the ``/api/v1/voice/live/ws`` WebSocket endpoint.

The ``GOOGLE_API_KEY`` setting is patched onto the settings singleton so
the endpoint's key check passes, and the whole ``LiveVoiceSession`` is
replaced by a no-op subclass so no real Gemini Live connection is opened.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from daraz_ai_shopping_assistant.main import create_app
from daraz_ai_shopping_assistant.voice.live_session import LiveVoiceSession


class _NullLiveVoiceSession(LiveVoiceSession):
    """LiveVoiceSession that sends one ready frame and then idles.

    Substituted via monkeypatch so the endpoint's key check is the only
    thing under test.
    """

    async def run(self) -> None:  # type: ignore[override]
        """Send a ready frame, then wait for the client to disconnect."""
        await self._ws.send_text(json.dumps({"type": "ready"}))
        try:
            while True:
                msg = await self._ws.receive()
                if msg.get("type") == "websocket.disconnect":
                    return
        except Exception:
            return

@pytest.fixture()
def client() -> Iterator[TestClient]:
    """Return a TestClient for a fresh app."""
    app = create_app()
    with TestClient(app) as test_client:
        yield test_client

@contextmanager
def _patched_google_key(value: str) -> Iterator[None]:
    """Patch the Google API key on the settings singleton.

    Args:
        value: Value for ``settings.google_api_key``. Empty string means
            "missing key".

    Yields:
        Control while the key is patched.
    """
    from daraz_ai_shopping_assistant.core.config import settings

    with patch.multiple(settings, google_api_key=value):
        yield

def test_live_ws_rejects_when_key_missing(client: TestClient) -> None:
    """Without ``GOOGLE_API_KEY`` the endpoint sends an error and closes."""
    with (
        _patched_google_key(""),
        client.websocket_connect("/api/v1/voice/live/ws") as ws,
    ):
        raw = ws.receive_text()
    payload = json.loads(raw)
    assert payload["type"] == "error"
    assert "GOOGLE_API_KEY" in payload["message"]

def test_live_ws_opens_when_key_present(client: TestClient) -> None:
    """With a key present the session starts and sends a ready frame."""
    with (
        _patched_google_key("test-google-key"),
        patch(
            "daraz_ai_shopping_assistant.api.voice_live.LiveVoiceSession",
            _NullLiveVoiceSession,
        ),
        client.websocket_connect("/api/v1/voice/live/ws") as ws,
    ):
        raw = ws.receive_text()
    assert json.loads(raw) == {"type": "ready"}

def test_openapi_does_not_include_websockets(client: TestClient) -> None:
    """WebSocket routes are intentionally excluded from the OpenAPI schema."""
    schema = client.get("/openapi.json").json()
    assert "/api/v1/voice/live/ws" not in schema.get("paths", {})

def test_voice_live_router_is_registered(client: TestClient) -> None:
    """The Live voice router is present on the app's route table."""
    from daraz_ai_shopping_assistant.api.voice_live import router

    paths = {route.path for route in router.routes}  # type: ignore[attr-defined]
    assert "/voice/live/ws" in paths
