"""Unit tests for the agent tool functions.

The service layer is mocked, so these tests never touch the network and
never depend on the LLM. They verify that each tool:

    - calls the correct service entry point with the correct arguments,
    - returns a JSON-serialisable dict,
    - propagates typed scraper errors unchanged.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from daraz_ai_shopping_assistant.agents.tools import (
    get_product_tool,
    get_recommendations_tool,
    search_products_tool,
)
from daraz_ai_shopping_assistant.core.exceptions import (
    ProductNotFoundError,
    ScraperTimeoutError,
)
from daraz_ai_shopping_assistant.models.product import ProductDetails
from daraz_ai_shopping_assistant.models.recommendation import Recommendation
from daraz_ai_shopping_assistant.models.search import SearchResult
from daraz_ai_shopping_assistant.utils.datetime import pkt_now


def _search_result() -> SearchResult:
    """Return a minimal SearchResult for mocking."""
    return SearchResult(search_query="x", scraped_at=pkt_now(), products=[])

def _product_details() -> ProductDetails:
    """Return a minimal ProductDetails for mocking."""
    return ProductDetails(
        id="i1959941878",
        title="Mouse",
        url="https://www.daraz.pk/products/i1959941878.html",
        price=100.0,
    )

def _recommendations() -> list[Recommendation]:
    """Return a small recommendation list for mocking."""
    return [
        Recommendation(
            id="i999",
            title="Another Mouse",
            url="https://www.daraz.pk/products/i999.html",
        )
    ]

# ---------------------------------------------------------------------- #
# search_products_tool
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio()
async def test_search_tool_calls_service_with_args() -> None:
    """The tool forwards its arguments to the search service."""
    service = MagicMock()
    service.search = AsyncMock(return_value=_search_result())
    with patch(
        "daraz_ai_shopping_assistant.agents.tools.get_search_service",
        return_value=service,
    ):
        result = await search_products_tool(
            "mouse", min_price=100.0, max_price=800.0, page=2
        )
    service.search.assert_awaited_once_with(
        "mouse", min_price=100.0, max_price=800.0, page=2
    )
    assert result["search_query"] == "x"

@pytest.mark.asyncio()
async def test_search_tool_propagates_scraper_error() -> None:
    """A typed scraper error propagates unchanged."""
    service = MagicMock()
    service.search = AsyncMock(side_effect=ScraperTimeoutError("timeout"))
    with patch(
        "daraz_ai_shopping_assistant.agents.tools.get_search_service",
        return_value=service,
    ), pytest.raises(ScraperTimeoutError):
        await search_products_tool("mouse")

# ---------------------------------------------------------------------- #
# get_product_tool
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio()
async def test_get_product_tool_calls_service() -> None:
    """The tool forwards the product_id to the product service."""
    service = MagicMock()
    service.get_product = AsyncMock(return_value=_product_details())
    with patch(
        "daraz_ai_shopping_assistant.agents.tools.get_product_service",
        return_value=service,
    ):
        result = await get_product_tool("i1959941878")
    service.get_product.assert_awaited_once_with("i1959941878")
    assert result["id"] == "i1959941878"

@pytest.mark.asyncio()
async def test_get_product_tool_propagates_not_found() -> None:
    """A ProductNotFoundError propagates unchanged."""
    service = MagicMock()
    service.get_product = AsyncMock(
        side_effect=ProductNotFoundError("missing")
    )
    with patch(
        "daraz_ai_shopping_assistant.agents.tools.get_product_service",
        return_value=service,
    ), pytest.raises(ProductNotFoundError):
        await get_product_tool("i999")

# ---------------------------------------------------------------------- #
# get_recommendations_tool
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio()
async def test_recommendations_tool_wraps_list() -> None:
    """The tool wraps the recommendation list in a dict."""
    service = MagicMock()
    service.get_recommendations = AsyncMock(return_value=_recommendations())
    with patch(
        "daraz_ai_shopping_assistant.agents.tools.get_product_service",
        return_value=service,
    ):
        result = await get_recommendations_tool("i1959941878")
    assert result["product_id"] == "i1959941878"
    assert isinstance(result["recommendations"], list)
    assert len(result["recommendations"]) == 1
    assert result["recommendations"][0]["id"] == "i999"

@pytest.mark.asyncio()
async def test_recommendations_tool_empty_list() -> None:
    """An empty recommendation list is wrapped correctly."""
    service = MagicMock()
    service.get_recommendations = AsyncMock(return_value=[])
    with patch(
        "daraz_ai_shopping_assistant.agents.tools.get_product_service",
        return_value=service,
    ):
        result = await get_recommendations_tool("i1")
    assert result == {"product_id": "i1", "recommendations": []}
