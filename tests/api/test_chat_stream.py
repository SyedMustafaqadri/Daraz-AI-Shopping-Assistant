"""Tests for ``POST /api/v1/chat/stream``.

The chat service is mocked via FastAPI's dependency-override mechanism, so
these tests never touch the LLM or the network. They verify the SSE
framing, the terminal sentinel, and the error path.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from daraz_ai_shopping_assistant.api.deps import get_chat_service_dep
from daraz_ai_shopping_assistant.main import create_app
from daraz_ai_shopping_assistant.services.chat_service import ChatService


def _parse_sse(body: str) -> list[dict[str, object] | str]:
    """Extract the JSON payloads from an SSE response body.

    Args:
        body: The raw response text.

    Returns:
        A list of parsed JSON objects, plus the literal string ``[DONE]``
        when the terminal sentinel is present.
    """
    events: list[dict[str, object] | str] = []
    for line in body.splitlines():
        if not line.startswith("data: "):
            continue
        raw = line[len("data: ") :]
        if raw == "[DONE]":
            events.append("[DONE]")
            continue
        events.append(json.loads(raw))
    return events

@pytest.fixture()
def mock_chat_service() -> MagicMock:
    """Return a mocked ChatService with an async chat_stream method."""
    service = MagicMock(spec=ChatService)
    service.chat_stream = MagicMock()
    return service

@pytest.fixture()
def client(mock_chat_service: MagicMock) -> Iterator[TestClient]:
    """Return a TestClient with the chat service mocked."""
    app = create_app()
    app.dependency_overrides[get_chat_service_dep] = lambda: mock_chat_service
    yield TestClient(app)
    app.dependency_overrides.clear()

def test_stream_emits_tokens_and_done(
    client: TestClient, mock_chat_service: MagicMock
) -> None:
    """A streamed response produces token frames plus a done frame."""

    async def fake_stream(
        message: str, conversation_id: str | None = None
    ) -> AsyncIterator[dict[str, object]]:
        del message, conversation_id
        yield {"type": "token", "text": "Hello"}
        yield {"type": "token", "text": " world"}
        yield {
            "type": "done",
            "conversation_id": "conv-1",
            "intent": "small_talk",
            "error": None,
        }

    mock_chat_service.chat_stream = fake_stream

    response = client.post(
        "/api/v1/chat/stream",
        json={"message": "hi", "conversation_id": "conv-1"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _parse_sse(response.text)
    assert events == [
        {"type": "token", "text": "Hello"},
        {"type": "token", "text": " world"},
        {
            "type": "done",
            "conversation_id": "conv-1",
            "intent": "small_talk",
            "error": None,
        },
        "[DONE]",
    ]

def test_stream_always_terminates_with_done_sentinel(
    client: TestClient, mock_chat_service: MagicMock
) -> None:
    """An empty stream still ends with the [DONE] sentinel."""

    async def empty_stream(
        message: str, conversation_id: str | None = None
    ) -> AsyncIterator[dict[str, object]]:
        del message, conversation_id
        if False:
            yield {}

    mock_chat_service.chat_stream = empty_stream

    response = client.post("/api/v1/chat/stream", json={"message": "hi"})
    assert response.text.strip().endswith("data: [DONE]")

def test_stream_missing_conversation_id_is_accepted(
    client: TestClient, mock_chat_service: MagicMock
) -> None:
    """Omitting conversation_id is valid; the service will generate one."""

    async def fake_stream(
        message: str, conversation_id: str | None = None
    ) -> AsyncIterator[dict[str, object]]:
        del message
        yield {
            "type": "done",
            "conversation_id": conversation_id or "generated",
            "intent": "small_talk",
            "error": None,
        }

    mock_chat_service.chat_stream = fake_stream

    response = client.post("/api/v1/chat/stream", json={"message": "hi"})
    assert response.status_code == 200
    events = _parse_sse(response.text)
    assert isinstance(events[0], dict)
    assert events[0]["conversation_id"] == "generated"

def test_stream_empty_message_returns_422(client: TestClient) -> None:
    """An empty message is rejected before the service is called."""
    response = client.post("/api/v1/chat/stream", json={"message": ""})
    assert response.status_code == 422

def test_stream_reports_internal_error_as_sse_frame(
    client: TestClient, mock_chat_service: MagicMock
) -> None:
    """A raised exception becomes an SSE error frame, not a 500."""

    async def broken_stream(
        message: str, conversation_id: str | None = None
    ) -> AsyncIterator[dict[str, object]]:
        del message, conversation_id
        raise RuntimeError("boom")
        if False:
            yield {}

    mock_chat_service.chat_stream = broken_stream

    response = client.post("/api/v1/chat/stream", json={"message": "hi"})
    assert response.status_code == 200
    events = _parse_sse(response.text)
    assert events[0] == {"type": "error", "message": "Internal server error."}
    assert events[-1] == "[DONE]"

def test_openapi_declares_stream_endpoint(client: TestClient) -> None:
    """The streaming endpoint appears in the OpenAPI schema."""
    schema = client.get("/openapi.json").json()
    assert "/api/v1/chat/stream" in schema["paths"]
    assert "post" in schema["paths"]["/api/v1/chat/stream"]
