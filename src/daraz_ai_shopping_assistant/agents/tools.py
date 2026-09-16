"""Tool functions for the agent pipeline.

Each tool wraps an existing service-layer entry point and returns a
JSON-serialisable dict (or list). Tools never call scrapers or Firecrawl
directly -- that boundary is enforced by the service layer.

The functions are plain async callables rather than LangChain ``@tool``
objects because this graph routes to them explicitly based on the parsed
intent, not via the LLM's tool-calling machinery. This keeps the graph
deterministic and testable: swap in a mock service, and the tool works
without any LLM in the loop.
"""

from __future__ import annotations

from typing import Any

from daraz_ai_shopping_assistant.core.logging import get_logger
from daraz_ai_shopping_assistant.services.product_service import (
    get_product_service,
)
from daraz_ai_shopping_assistant.services.search_service import (
    get_search_service,
)

logger = get_logger(__name__)

async def search_products_tool(
    query: str,
    *,
    min_price: float | None = None,
    max_price: float | None = None,
    page: int = 1,
) -> dict[str, Any]:
    """Search Daraz for products matching a query.

    Args:
        query: Free-text search query.
        min_price: Minimum price in PKR, or ``None``.
        max_price: Maximum price in PKR, or ``None``.
        page: 1-indexed page number.

    Returns:
        A JSON-serialisable dict shaped like ``SearchResult``.

    Raises:
        DarazScraperError: On any upstream failure. Propagated unchanged so
            the graph node can convert it to a user-visible error string.
    """
    logger.info(
        "AGENT_TOOL_SEARCH",
        extra={"ctx": {"query": query, "min_price": min_price, "max_price": max_price, "page": page}},
    )
    service = get_search_service()
    result = await service.search(
        query, min_price=min_price, max_price=max_price, page=page
    )
    return result.model_dump(mode="json")

async def get_product_tool(product_id: str) -> dict[str, Any]:
    """Fetch a single Daraz product's details by id.

    Args:
        product_id: Daraz product identifier (e.g. ``"i927677133"``).

    Returns:
        A JSON-serialisable dict shaped like ``ProductDetails``.

    Raises:
        DarazScraperError: On any upstream failure, or when the product is
            not found.
    """
    logger.info("AGENT_TOOL_GET_PRODUCT", extra={"ctx": {"product_id": product_id}})
    service = get_product_service()
    product = await service.get_product(product_id)
    return product.model_dump(mode="json")

async def get_recommendations_tool(product_id: str) -> dict[str, Any]:
    """Fetch Daraz's recommendations for a product.

    Args:
        product_id: Daraz product identifier.

    Returns:
        A dict with a single ``recommendations`` key whose value is a list
        of JSON-serialisable recommendation dicts. Wrapping in a dict keeps
        the tool's return type uniform with the others.

    Raises:
        DarazScraperError: On any upstream failure, or when the product is
            not found.
    """
    logger.info(
        "AGENT_TOOL_GET_RECOMMENDATIONS", extra={"ctx": {"product_id": product_id}}
    )
    service = get_product_service()
    recommendations = await service.get_recommendations(product_id)
    return {
        "product_id": product_id,
        "recommendations": [r.model_dump(mode="json") for r in recommendations],
    }

__all__ = [
    "get_product_tool",
    "get_recommendations_tool",
    "search_products_tool",
]
