"""Unit tests for voice-session barge-in gating."""

from __future__ import annotations

from typing import Any, cast

import pytest
from fastapi import WebSocket
from starlette.websockets import WebSocketState

from daraz_ai_shopping_assistant.voice.session import VoiceSession, _Turn


class _FakeWebSocket:
    """Minimal WebSocket surface used by VoiceSession send helpers."""

    client_state = WebSocketState.CONNECTED

    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send_text(self, data: str) -> None:
        self.messages.append(data)


def _session(websocket: _FakeWebSocket) -> VoiceSession:
    """Create a session without opening vendor connections."""
    return VoiceSession(
        websocket=cast(WebSocket, websocket),
        stt=cast(Any, object()),
        chat=cast(Any, object()),
    )


@pytest.mark.asyncio()
async def test_short_interim_after_speech_started_does_not_cancel() -> None:
    """A short VAD follow-up is ignored as likely ambient noise."""
    websocket = _FakeWebSocket()
    session = _session(websocket)
    turn = _Turn(id=1)
    session._active_turn = turn

    await session._on_stt_event({"type": "speech_started"})
    await session._on_stt_event({"type": "interim", "text": "hi"})

    assert session._active_turn is turn
    assert not turn.cancelled
    assert websocket.messages == ['{"type": "transcript_interim", "text": "hi"}']


@pytest.mark.asyncio()
async def test_substantial_interim_after_speech_started_cancels() -> None:
    """A substantial transcript confirms an intentional interruption."""
    websocket = _FakeWebSocket()
    session = _session(websocket)
    turn = _Turn(id=1)
    session._active_turn = turn

    await session._on_stt_event({"type": "speech_started"})
    await session._on_stt_event({"type": "interim", "text": "stop please"})

    assert '{"type": "stop_playback"}' in websocket.messages


@pytest.mark.asyncio()
async def test_delayed_interim_without_vad_candidate_does_not_cancel() -> None:
    """Late interim text from the prior utterance cannot cancel a reply."""
    websocket = _FakeWebSocket()
    session = _session(websocket)
    turn = _Turn(id=1)
    session._active_turn = turn

    await session._on_stt_event({"type": "interim", "text": "Hello?"})

    assert session._active_turn is turn
    assert not turn.cancelled
    assert websocket.messages == ['{"type": "transcript_interim", "text": "Hello?"}']
