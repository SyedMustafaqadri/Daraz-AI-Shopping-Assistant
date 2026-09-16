"""Unit tests for ChatService.

The compiled graph is mocked, so these tests never touch the LLM. They
verify that the service builds the initial state correctly, extracts the
reply from the final state, and surfaces errors.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from daraz_ai_shopping_assistant.agents.state import IntentType, ParsedIntent
from daraz_ai_shopping_assistant.services.chat_service import (
    ChatService,
    _extract_reply,
)


def _make_graph(final_state: dict[str, Any]) -> MagicMock:
    """Return a mock graph whose ainvoke yields ``final_state``."""
    graph = MagicMock()
    graph.ainvoke = AsyncMock(return_value=final_state)
    return graph

@pytest.mark.asyncio()
async def test_chat_returns_reply_from_final_ai_message() -> None:
    """The reply is taken from the last AIMessage in the state."""
    final_state = {
        "messages": [
            HumanMessage(content="hi"),
            AIMessage(content="Hello! How can I help?"),
        ],
        "intent": ParsedIntent(intent=IntentType.SMALL_TALK),
        "tool_result": None,
        "error": None,
    }
    service = ChatService(graph=_make_graph(final_state))

    response = await service.chat("hi")

    assert response.reply == "Hello! How can I help?"
    assert response.intent == "small_talk"
    assert response.data is None
    assert response.error is None

@pytest.mark.asyncio()
async def test_chat_returns_tool_result_when_present() -> None:
    """A populated tool_result is returned as data."""
    final_state = {
        "messages": [
            HumanMessage(content="mouse"),
            AIMessage(content="Found 3 products."),
        ],
        "intent": ParsedIntent(intent=IntentType.SEARCH, query="mouse"),
        "tool_result": {"search_query": "mouse", "products": []},
        "error": None,
    }
    service = ChatService(graph=_make_graph(final_state))

    response = await service.chat("mouse")

    assert response.data == {"search_query": "mouse", "products": []}
    assert response.intent == "search"

@pytest.mark.asyncio()
async def test_chat_propagates_error_field() -> None:
    """An error from the graph is surfaced on the response."""
    final_state = {
        "messages": [AIMessage(content="Sorry, something went wrong.")],
        "intent": ParsedIntent(intent=IntentType.SEARCH, query="x"),
        "tool_result": None,
        "error": "upstream timeout",
    }
    service = ChatService(graph=_make_graph(final_state))

    response = await service.chat("x")

    assert response.error == "upstream timeout"
    assert response.data is None

@pytest.mark.asyncio()
async def test_chat_handles_missing_intent() -> None:
    """A missing intent does not crash the service."""
    final_state = {
        "messages": [AIMessage(content="Hello.")],
        "intent": None,
        "tool_result": None,
        "error": None,
    }
    service = ChatService(graph=_make_graph(final_state))

    response = await service.chat("hi")

    assert response.intent is None

@pytest.mark.asyncio()
async def test_chat_uses_fallback_when_no_ai_message() -> None:
    """A state with no AIMessage yields a safe fallback reply."""
    final_state = {
        "messages": [HumanMessage(content="hi")],
        "intent": None,
        "tool_result": None,
        "error": None,
    }
    service = ChatService(graph=_make_graph(final_state))

    response = await service.chat("hi")

    assert response.reply  # non-empty
    assert "could not" in response.reply.lower() or "try again" in response.reply.lower()

def test_extract_reply_handles_string_content() -> None:
    """_extract_reply returns the string content of the last AIMessage."""
    state = {"messages": [AIMessage(content="hello")]}
    assert _extract_reply(state) == "hello"

def test_extract_reply_handles_list_content() -> None:
    """_extract_reply concatenates text blocks from list content."""
    message = AIMessage(content=[{"type": "text", "text": "hello "}, {"type": "text", "text": "world"}])
    state = {"messages": [message]}
    assert _extract_reply(state) == "hello world"

def test_extract_reply_handles_empty_state() -> None:
    """An empty state yields the fallback message."""
    state: dict[str, Any] = {"messages": []}
    reply = _extract_reply(state)
    assert reply  # non-empty
