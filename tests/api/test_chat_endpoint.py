"""Tests for POST /api/v1/chat.

The chat service is mocked via FastAPI's dependency-override mechanism, so
these tests never touch the LLM or the network. They cover:

    - happy path with a reply and data,
    - request-body validation (empty message -> 422),
    - error mapping from typed exceptions,
    - the OpenAPI surface.
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from daraz_ai_shopping_assistant.api.deps import get_chat_service_dep
from daraz_ai_shopping_assistant.core.exceptions import (
    InvalidRequestError,
    ScraperError,
    ScraperTimeoutError,
)
from daraz_ai_shopping_assistant.main import create_app
from daraz_ai_shopping_assistant.schemas.chat import ChatResponse
from daraz_ai_shopping_assistant.services.chat_service import ChatService


@pytest.fixture()
def mock_chat_service() -> MagicMock:
    """Return a mocked ChatService with an async chat method."""
    service = MagicMock(spec=ChatService)
    service.chat = AsyncMock()
    return service

@pytest.fixture()
def client(mock_chat_service: MagicMock) -> Iterator[TestClient]:
    """Return a TestClient with the chat service mocked."""
    app = create_app()
    app.dependency_overrides[get_chat_service_dep] = lambda: mock_chat_service
    yield TestClient(app)
    app.dependency_overrides.clear()

def test_chat_happy_path(client: TestClient, mock_chat_service: MagicMock) -> None:
    """A valid message returns 200 with the assistant's reply."""
    mock_chat_service.chat.return_value = ChatResponse(
        reply="Here are the gaming mice I found.",
        intent="search",
        data={"search_query": "gaming mouse", "products": []},
        error=None,
    )

    response = client.post(
        "/api/v1/chat", json={"message": "find me a gaming mouse"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["reply"] == "Here are the gaming mice I found."
    assert body["intent"] == "search"
    assert body["data"]["search_query"] == "gaming mouse"
    assert body["error"] is None

    mock_chat_service.chat.assert_awaited_once_with("find me a gaming mouse")

def test_chat_small_talk(client: TestClient, mock_chat_service: MagicMock) -> None:
    """A small-talk reply has no data payload."""
    mock_chat_service.chat.return_value = ChatResponse(
        reply="Hello! How can I help you today?",
        intent="small_talk",
    )

    response = client.post("/api/v1/chat", json={"message": "hi"})

    assert response.status_code == 200
    body = response.json()
    assert body["intent"] == "small_talk"
    assert body["data"] is None

def test_chat_empty_message_returns_422(client: TestClient) -> None:
    """An empty message is rejected by request validation."""
    response = client.post("/api/v1/chat", json={"message": ""})
    assert response.status_code == 422

def test_chat_missing_message_returns_422(client: TestClient) -> None:
    """A missing message field is rejected."""
    response = client.post("/api/v1/chat", json={})
    assert response.status_code == 422

def test_chat_message_too_long_returns_422(client: TestClient) -> None:
    """A message above the length cap is rejected."""
    response = client.post("/api/v1/chat", json={"message": "x" * 2001})
    assert response.status_code == 422

def test_chat_extra_field_returns_422(client: TestClient) -> None:
    """An undeclared field is rejected."""
    response = client.post(
        "/api/v1/chat", json={"message": "hi", "unexpected": "no"}
    )
    assert response.status_code == 422

def test_chat_invalid_request_returns_400(
    client: TestClient, mock_chat_service: MagicMock
) -> None:
    """An InvalidRequestError from the service maps to 400."""
    mock_chat_service.chat.side_effect = InvalidRequestError("bad input")

    response = client.post("/api/v1/chat", json={"message": "hi"})

    assert response.status_code == 400
    assert "bad input" in response.json()["detail"]

def test_chat_scraper_error_returns_502(
    client: TestClient, mock_chat_service: MagicMock
) -> None:
    """A ScraperError from a tool maps to 502."""
    mock_chat_service.chat.side_effect = ScraperError("upstream down")

    response = client.post("/api/v1/chat", json={"message": "hi"})

    assert response.status_code == 502

def test_chat_timeout_returns_504(
    client: TestClient, mock_chat_service: MagicMock
) -> None:
    """A timeout maps to 504."""
    mock_chat_service.chat.side_effect = ScraperTimeoutError("timeout")

    response = client.post("/api/v1/chat", json={"message": "hi"})

    assert response.status_code == 504

def test_openapi_declares_chat_endpoint(client: TestClient) -> None:
    """The chat endpoint appears in the OpenAPI schema."""
    schema = client.get("/openapi.json").json()
    assert "/api/v1/chat" in schema["paths"]
    assert "post" in schema["paths"]["/api/v1/chat"]
