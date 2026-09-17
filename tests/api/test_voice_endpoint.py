"""Tests for the /api/v1/voice/ws WebSocket endpoint.

The vendor keys are patched onto settings so the endpoint's key check
passes, and the whole `VoiceSession` is replaced by a no-op subclass so
no real Deepgram or ElevenLabs connection is opened.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from daraz_ai_shopping_assistant.main import create_app
from daraz_ai_shopping_assistant.voice.session import VoiceSession


class _NullVoiceSession(VoiceSession):
    """VoiceSession that sends one ready frame and then idles.

    Substituted via monkeypatch so the endpoint's key check is the only
    thing under test.
    """

    async def run(self) -> None:  # type: ignore[override]
        """Send a ready frame, then return immediately."""
        await self._ws.send_text(json.dumps({"type": "ready"}))
        # Keep the connection open until the client disconnects.
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
def _patched_keys(
    *, deepgram: str = "dg-test", elevenlabs: str = "el-test"
) -> Iterator[None]:
    """Patch the two vendor keys on the settings singleton.

    Args:
        deepgram: Value for ``settings.deepgram_api_key``.
        elevenlabs: Value for ``settings.elevenlabs_api_key``.

    Yields:
        Control while the keys are patched.
    """
    from daraz_ai_shopping_assistant.core.config import settings

    with patch.multiple(
        settings,
        deepgram_api_key=deepgram,
        elevenlabs_api_key=elevenlabs,
    ):
        yield

def test_voice_ws_rejects_when_keys_missing(client: TestClient) -> None:
    """Without vendor keys the endpoint sends an error and closes."""
    with (
        _patched_keys(deepgram="", elevenlabs=""),
        client.websocket_connect("/api/v1/voice/ws") as ws,
    ):
        raw = ws.receive_text()
    payload = json.loads(raw)
    assert payload["type"] == "error"
    assert "DEEPGRAM_API_KEY" in payload["message"]
    assert "ELEVENLABS_API_KEY" in payload["message"]

def test_voice_ws_reports_missing_key_by_name(client: TestClient) -> None:
    """Only the missing key name is listed."""
    with (
        _patched_keys(deepgram="dg-test", elevenlabs=""),
        client.websocket_connect("/api/v1/voice/ws") as ws,
    ):
        raw = ws.receive_text()
    payload = json.loads(raw)
    assert payload["type"] == "error"
    assert "ELEVENLABS_API_KEY" in payload["message"]
    assert "DEEPGRAM_API_KEY" not in payload["message"]

def test_voice_ws_opens_when_keys_present(client: TestClient) -> None:
    """With keys present the session starts and sends a ready frame."""
    with (
        _patched_keys(),
        patch(
            "daraz_ai_shopping_assistant.api.voice.VoiceSession",
            _NullVoiceSession,
        ),
        client.websocket_connect("/api/v1/voice/ws") as ws,
    ):
        raw = ws.receive_text()
    assert json.loads(raw) == {"type": "ready"}
