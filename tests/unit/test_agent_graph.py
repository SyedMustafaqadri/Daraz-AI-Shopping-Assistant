"""Unit tests for the LangGraph agent pipeline.

The LLM is fully mocked via a hand-written fake, and the tool functions
are patched on the graph module. No network calls, no LLM calls, no
Firecrawl credits. Tests verify routing, error handling, and state shape.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage

from daraz_ai_shopping_assistant.agents.graph import build_graph
from daraz_ai_shopping_assistant.agents.state import IntentType, ParsedIntent


# ---------------------------------------------------------------------- #
# Fake LLM
# ---------------------------------------------------------------------- #
class _FakeStructuredRunnable:
    """Stands in for ``llm.with_structured_output(Model)``."""

    def __init__(self, parsed: ParsedIntent | BaseException) -> None:
        self._parsed = parsed

    async def ainvoke(self, _messages: Any) -> ParsedIntent:
        if isinstance(self._parsed, BaseException):
            raise self._parsed
        return self._parsed

class FakeLLM:
    """Minimal BaseChatModel-compatible fake for graph tests.

    Two call paths matter:

        - ``with_structured_output(Model)`` -- returns a runnable that
          produces the ParsedIntent the test wants the graph to see.
        - ``ainvoke(messages)`` -- returns the AIMessage that becomes the
          assistant's final reply.
    """

    def __init__(
        self,
        parsed_intent: ParsedIntent | BaseException,
        reply_text: str = "Here is your answer.",
    ) -> None:
        self._parsed_intent = parsed_intent
        self._reply_text = reply_text

    def with_structured_output(self, _schema: Any) -> _FakeStructuredRunnable:
        return _FakeStructuredRunnable(self._parsed_intent)

    async def ainvoke(self, _messages: Any) -> AIMessage:
        return AIMessage(content=self._reply_text)

# ---------------------------------------------------------------------- #
# Search routing
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio()
async def test_search_intent_routes_to_search_node() -> None:
    """A 'search' intent calls the search tool and reaches the response."""
    llm = FakeLLM(
        ParsedIntent(intent=IntentType.SEARCH, query="gaming mouse"),
        reply_text="Here are the gaming mice I found.",
    )
    graph = build_graph(llm=llm)

    with patch(
        "daraz_ai_shopping_assistant.agents.graph.search_products_tool",
        new=AsyncMock(return_value={"search_query": "gaming mouse", "products": []}),
    ) as mock_search:
        final = await graph.ainvoke(
            {
                "messages": [__import__("langchain_core.messages", fromlist=["HumanMessage"]).HumanMessage(content="find me a gaming mouse")],
                "intent": None,
                "tool_result": None,
                "error": None,
            }
        )

    mock_search.assert_awaited_once()
    assert final["intent"].intent is IntentType.SEARCH
    assert final["tool_result"] == {"search_query": "gaming mouse", "products": []}
    assert final["error"] is None
    assert any(
        isinstance(m, AIMessage) and "gaming mice" in str(m.content)
        for m in final["messages"]
    )

# ---------------------------------------------------------------------- #
# Product routing
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio()
async def test_get_product_intent_routes_to_product_node() -> None:
    """A 'get_product' intent calls the product tool."""
    llm = FakeLLM(
        ParsedIntent(intent=IntentType.GET_PRODUCT, product_id="i1959941878"),
        reply_text="Here are the details.",
    )
    graph = build_graph(llm=llm)

    with patch(
        "daraz_ai_shopping_assistant.agents.graph.get_product_tool",
        new=AsyncMock(return_value={"id": "i1959941878", "title": "Mouse"}),
    ) as mock_product:
        final = await graph.ainvoke(
            {
                "messages": [__import__("langchain_core.messages", fromlist=["HumanMessage"]).HumanMessage(content="details for i1959941878")],
                "intent": None,
                "tool_result": None,
                "error": None,
            }
        )

    mock_product.assert_awaited_once_with("i1959941878")
    assert final["tool_result"]["id"] == "i1959941878"

# ---------------------------------------------------------------------- #
# Recommendations routing
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio()
async def test_get_recommendations_intent_routes_to_recs_node() -> None:
    """A 'get_recommendations' intent calls the recommendation tool."""
    llm = FakeLLM(
        ParsedIntent(intent=IntentType.GET_RECOMMENDATIONS, product_id="i1"),
        reply_text="Similar products below.",
    )
    graph = build_graph(llm=llm)

    with patch(
        "daraz_ai_shopping_assistant.agents.graph.get_recommendations_tool",
        new=AsyncMock(return_value={"product_id": "i1", "recommendations": []}),
    ) as mock_recs:
        final = await graph.ainvoke(
            {
                "messages": [__import__("langchain_core.messages", fromlist=["HumanMessage"]).HumanMessage(content="similar to i1")],
                "intent": None,
                "tool_result": None,
                "error": None,
            }
        )

    mock_recs.assert_awaited_once_with("i1")
    assert final["tool_result"]["product_id"] == "i1"

# ---------------------------------------------------------------------- #
# Small talk bypasses tools
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio()
async def test_small_talk_skips_tools() -> None:
    """A 'small_talk' intent goes straight to the response node."""
    llm = FakeLLM(
        ParsedIntent(intent=IntentType.SMALL_TALK),
        reply_text="Hello! How can I help?",
    )
    graph = build_graph(llm=llm)

    with (
        patch(
            "daraz_ai_shopping_assistant.agents.graph.search_products_tool",
            new=AsyncMock(),
        ) as mock_search,
        patch(
            "daraz_ai_shopping_assistant.agents.graph.get_product_tool",
            new=AsyncMock(),
        ) as mock_product,
        patch(
            "daraz_ai_shopping_assistant.agents.graph.get_recommendations_tool",
            new=AsyncMock(),
        ) as mock_recs,
    ):
        final = await graph.ainvoke(
            {
                "messages": [__import__("langchain_core.messages", fromlist=["HumanMessage"]).HumanMessage(content="hi")],
                "intent": None,
                "tool_result": None,
                "error": None,
            }
        )

    mock_search.assert_not_awaited()
    mock_product.assert_not_awaited()
    mock_recs.assert_not_awaited()
    assert final["tool_result"] is None

# ---------------------------------------------------------------------- #
# Error handling
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio()
async def test_tool_error_recorded_in_state() -> None:
    """A scraper error is caught and recorded in the state."""
    from daraz_ai_shopping_assistant.core.exceptions import ScraperError

    llm = FakeLLM(
        ParsedIntent(intent=IntentType.SEARCH, query="mouse"),
        reply_text="Sorry, something went wrong.",
    )
    graph = build_graph(llm=llm)

    with patch(
        "daraz_ai_shopping_assistant.agents.graph.search_products_tool",
        new=AsyncMock(side_effect=ScraperError("upstream is down")),
    ):
        final = await graph.ainvoke(
            {
                "messages": [__import__("langchain_core.messages", fromlist=["HumanMessage"]).HumanMessage(content="mouse")],
                "intent": None,
                "tool_result": None,
                "error": None,
            }
        )

    assert final["error"] == "upstream is down"
    assert final["tool_result"] is None

@pytest.mark.asyncio()
async def test_intent_parse_failure_falls_back_to_small_talk() -> None:
    """When the LLM fails to produce a ParsedIntent, we degrade gracefully."""
    llm = FakeLLM(
        ValueError("model refused"),
        reply_text="Sorry, I did not catch that.",
    )
    graph = build_graph(llm=llm)

    final = await graph.ainvoke(
        {
            "messages": [__import__("langchain_core.messages", fromlist=["HumanMessage"]).HumanMessage(content="???")],
            "intent": None,
            "tool_result": None,
            "error": None,
        }
    )

    assert final["intent"].intent is IntentType.SMALL_TALK

@pytest.mark.asyncio()
async def test_missing_product_id_records_error() -> None:
    """An intent missing its required product_id records an error."""
    llm = FakeLLM(
        ParsedIntent(intent=IntentType.GET_PRODUCT),  # no product_id
        reply_text="Please provide a product ID.",
    )
    graph = build_graph(llm=llm)

    final = await graph.ainvoke(
        {
            "messages": [__import__("langchain_core.messages", fromlist=["HumanMessage"]).HumanMessage(content="show details")],
            "intent": None,
            "tool_result": None,
            "error": None,
        }
    )

    assert final["error"] is not None
    assert "product" in final["error"].lower()
