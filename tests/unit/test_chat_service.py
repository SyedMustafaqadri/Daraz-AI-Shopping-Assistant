"""Unit tests for ChatService.

The compiled graph is mocked, so these tests never touch the LLM. They
verify that the service builds the initial state correctly, extracts the
reply from the final state, curates the recommended_products list, and
surfaces errors.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from daraz_ai_shopping_assistant.agents.state import IntentType, ParsedIntent
from daraz_ai_shopping_assistant.services.chat_service import (
    ChatService,
    _curate_recommended_products,
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
    assert response.recommended_products == []
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
    assert response.recommended_products == []

@pytest.mark.asyncio()
async def test_chat_curates_search_recommendations() -> None:
    """A search result curates the first five products."""
    products = [
        {"id": f"i{i}", "title": f"Product {i}", "price": 100 + i}
        for i in range(10)
    ]
    final_state = {
        "messages": [HumanMessage(content="mouse"), AIMessage(content="Here.")],
        "intent": ParsedIntent(intent=IntentType.SEARCH, query="mouse"),
        "tool_result": {"search_query": "mouse", "products": products},
        "error": None,
    }
    service = ChatService(graph=_make_graph(final_state))

    response = await service.chat("mouse")

    assert len(response.recommended_products) == 5
    assert response.recommended_products[0]["id"] == "i0"
    assert response.recommended_products[4]["id"] == "i4"

@pytest.mark.asyncio()
async def test_chat_curates_get_product_as_single_item_list() -> None:
    """A get_product result becomes a one-item list."""
    product = {"id": "i1959941878", "title": "Mouse", "price": 579.0}
    final_state = {
        "messages": [HumanMessage(content="details"), AIMessage(content="Here.")],
        "intent": ParsedIntent(
            intent=IntentType.GET_PRODUCT, product_id="i1959941878"
        ),
        "tool_result": product,
        "error": None,
    }
    service = ChatService(graph=_make_graph(final_state))

    response = await service.chat("details")

    assert response.recommended_products == [product]

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
    assert response.recommended_products == []

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
    assert response.recommended_products == []

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

    assert response.reply
    assert "could not" in response.reply.lower() or "try again" in response.reply.lower()

@pytest.mark.asyncio()
async def test_chat_voice_stream_normalizes_model_and_mapping_intents() -> None:
    """Voice streaming accepts both LangGraph intent serialization shapes."""
    intent = ParsedIntent(intent=IntentType.SEARCH, query="mouse")

    async def events() -> Any:
        yield {
            "event": "on_chain_end",
            "metadata": {"langgraph_node": "parse_intent"},
            "data": {"output": intent},
        }

    graph = MagicMock()
    graph.astream_events = MagicMock(return_value=events())
    graph.aget_state = AsyncMock(
        return_value=SimpleNamespace(
            values={
                "intent": {
                    "intent": "search",
                    "query": "mouse",
                    "product_id": None,
                    "min_price": None,
                    "max_price": None,
                    "page": 1,
                },
                "tool_result": None,
                "error": None,
            }
        )
    )
    service = ChatService(graph=graph)

    output = [event async for event in service.chat_voice_stream("mouse")]

    assert output[0] == {
        "type": "intent_parsed",
        "conversation_id": output[0]["conversation_id"],
        "intent": "search",
        "query": "mouse",
    }
    assert output[-1]["type"] == "done"
    assert output[-1]["intent"] == "search"

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
    assert reply

# ---------------------------------------------------------------------- #
# _curate_recommended_products
# ---------------------------------------------------------------------- #
def test_curate_returns_empty_for_none_intent() -> None:
    """No intent means no recommendations."""
    assert _curate_recommended_products(None, {"products": [{"id": "i1"}]}) == []

def test_curate_returns_empty_for_none_tool_result() -> None:
    """No tool result means no recommendations."""
    intent = ParsedIntent(intent=IntentType.SEARCH, query="mouse")
    assert _curate_recommended_products(intent, None) == []

def test_curate_search_limits_to_five() -> None:
    """The search branch caps at five products."""
    products = [{"id": f"i{i}"} for i in range(20)]
    intent = ParsedIntent(intent=IntentType.SEARCH, query="mouse")
    result = _curate_recommended_products(intent, {"products": products})
    assert len(result) == 5

def test_curate_search_handles_empty_product_list() -> None:
    """An empty product list yields an empty recommendation list."""
    intent = ParsedIntent(intent=IntentType.SEARCH, query="mouse")
    assert _curate_recommended_products(intent, {"products": []}) == []

def test_curate_search_skips_non_dict_entries() -> None:
    """Defensive: non-dict entries are dropped."""
    intent = ParsedIntent(intent=IntentType.SEARCH, query="mouse")
    result = _curate_recommended_products(
        intent, {"products": [{"id": "i1"}, "not-a-dict", None, {"id": "i2"}]}
    )
    assert len(result) == 2

def test_curate_get_product_single_item() -> None:
    """A get_product result becomes a one-item list."""
    intent = ParsedIntent(intent=IntentType.GET_PRODUCT, product_id="i1")
    result = _curate_recommended_products(intent, {"id": "i1", "title": "x"})
    assert result == [{"id": "i1", "title": "x"}]

def test_curate_get_product_missing_id_returns_empty() -> None:
    """A get_product payload without an id yields an empty list."""
    intent = ParsedIntent(intent=IntentType.GET_PRODUCT, product_id="i1")
    assert _curate_recommended_products(intent, {"title": "x"}) == []

def test_curate_get_recommendations_limits_to_five() -> None:
    """The recommendations branch caps at five items."""
    recs = [{"id": f"i{i}"} for i in range(20)]
    intent = ParsedIntent(intent=IntentType.GET_RECOMMENDATIONS, product_id="i1")
    result = _curate_recommended_products(intent, {"recommendations": recs})
    assert len(result) == 5

def test_curate_small_talk_returns_empty() -> None:
    """Small talk has no products to recommend."""
    intent = ParsedIntent(intent=IntentType.SMALL_TALK)
    assert _curate_recommended_products(intent, {"anything": "goes"}) == []
