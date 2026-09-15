"""Unit tests for :class:`FirecrawlDarazScraper`.

The underlying :class:`FirecrawlAdapter` is fully mocked, so these tests
never touch the network. They verify that the scraper:

    - builds the correct Daraz URL for each page type,
    - chooses the correct adapter method (``scrape`` vs ``scrape_json``),
    - passes the product-extraction prompt and JSON schema correctly.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from daraz_ai_shopping_assistant.scrapers.daraz import (
    _PRODUCT_EXTRACTION_PROMPT,
    FirecrawlDarazScraper,
)


@pytest.fixture()
def mock_adapter() -> MagicMock:
    """Return a MagicMock adapter with async methods.

    Returns:
        A mock where ``scrape`` and ``scrape_json`` are async mocks.
    """
    adapter = MagicMock()
    adapter.scrape = AsyncMock(return_value="# fake markdown")
    adapter.scrape_json = AsyncMock(return_value={"id": "i1", "title": "t"})
    return adapter

# ---------------------------------------------------------------------- #
# fetch_search_markdown
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio()
async def test_fetch_search_markdown_calls_adapter_scrape(
    mock_adapter: MagicMock,
) -> None:
    """The search path uses the adapter's Markdown mode."""
    scraper = FirecrawlDarazScraper(adapter=mock_adapter)

    result = await scraper.fetch_search_markdown("gaming mouse", max_price=800)

    assert result == "# fake markdown"
    mock_adapter.scrape.assert_awaited_once()
    called_url = mock_adapter.scrape.await_args.args[0]
    assert called_url.startswith("https://www.daraz.pk/catalog/?")
    assert "q=gaming%20mouse" in called_url
    assert "price=-800" in called_url
    mock_adapter.scrape_json.assert_not_awaited()

@pytest.mark.asyncio()
async def test_fetch_search_markdown_includes_page_when_gt_one(
    mock_adapter: MagicMock,
) -> None:
    """Page > 1 appears in the URL passed to the adapter."""
    scraper = FirecrawlDarazScraper(adapter=mock_adapter)
    await scraper.fetch_search_markdown("mouse", page=3)
    called_url = mock_adapter.scrape.await_args.args[0]
    assert "page=3" in called_url

@pytest.mark.asyncio()
async def test_fetch_search_markdown_no_page_for_first_page(
    mock_adapter: MagicMock,
) -> None:
    """Page 1 is omitted from the URL (Daraz's default)."""
    scraper = FirecrawlDarazScraper(adapter=mock_adapter)
    await scraper.fetch_search_markdown("mouse", page=1)
    called_url = mock_adapter.scrape.await_args.args[0]
    assert "page=" not in called_url

# ---------------------------------------------------------------------- #
# fetch_product_payload
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio()
async def test_fetch_product_payload_calls_adapter_scrape_json(
    mock_adapter: MagicMock,
) -> None:
    """The product path uses the adapter's structured-extraction mode."""
    scraper = FirecrawlDarazScraper(adapter=mock_adapter)

    result = await scraper.fetch_product_payload("i1959941878")

    assert result == {"id": "i1", "title": "t"}
    mock_adapter.scrape_json.assert_awaited_once()
    kwargs = mock_adapter.scrape_json.await_args.kwargs
    called_url = mock_adapter.scrape_json.await_args.args[0]
    assert called_url == "https://www.daraz.pk/products/i1959941878.html"
    assert kwargs["prompt"] == _PRODUCT_EXTRACTION_PROMPT
    assert "schema" in kwargs
    # The schema must be the ProductDetails schema -- verify by shape.
    schema: dict[str, Any] = kwargs["schema"]
    assert schema.get("title") == "ProductDetails"
    mock_adapter.scrape.assert_not_awaited()

# ---------------------------------------------------------------------- #
# Construction
# ---------------------------------------------------------------------- #
def test_scraper_uses_supplied_adapter() -> None:
    """The supplied adapter is stored and used, not replaced."""
    adapter = MagicMock()
    adapter.scrape = AsyncMock()
    scraper = FirecrawlDarazScraper(adapter=adapter)
    assert scraper._adapter is adapter
