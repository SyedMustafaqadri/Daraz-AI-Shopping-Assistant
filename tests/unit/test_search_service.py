"""Unit tests for :class:`SearchService`.

The scraper is mocked, so the service is tested end-to-end without any
network access. Tests cover:

    - the happy path (fetch -> parse -> validate -> SearchResult),
    - dropping invalid products with the PRODUCT_VALIDATION_FAILED event,
    - empty results (SEARCH_EMPTY event but a valid SearchResult),
    - rejecting empty queries,
    - rejecting contradictory price filters,
    - the scraped_at timestamp being timezone-aware,
    - module-level ``search_products`` convenience wrapper.
"""

from __future__ import annotations

import textwrap
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from daraz_ai_shopping_assistant.core.exceptions import InvalidRequestError
from daraz_ai_shopping_assistant.models.search import SearchResult
from daraz_ai_shopping_assistant.services.search_service import (
    SearchService,
    get_search_service,
    search_products,
)
from daraz_ai_shopping_assistant.utils.datetime import PKT

# ---------------------------------------------------------------------- #
# Test fixtures
# ---------------------------------------------------------------------- #
SEARCH_MARKDOWN = textwrap.dedent(
    """\
    # Gaming Mouse

    11084 items found for "Gaming Mouse"

    [![Product One](https://img.drz.lazcdn.com/x.jpg)](https://www.daraz.pk/products/one-i111.html)

    [Product One](https://www.daraz.pk/products/one-i111.html "Product One")

    Rs. 579

    27% OffCoins save Rs. 29

    184 sold

    (40)

    Punjab

    [![Product Two](https://img.drz.lazcdn.com/y.jpg)](https://www.daraz.pk/products/two-i222.html)

    [Product Two](https://www.daraz.pk/products/two-i222.html "Product Two")

    Rs. 599

    251 sold

    (26)

    Sindh

    - [1](https://www.daraz.pk/catalog/?q=Gaming%20Mouse)
    - [102](https://www.daraz.pk/catalog/?q=Gaming%20Mouse&page=102)
    """
)

EMPTY_MARKDOWN = "# Nothing\n\n0 items found.\n"

def _make_scraper(markdown: str) -> MagicMock:
    """Return a mock :class:`DarazScraper` that yields ``markdown``.

    Args:
        markdown: Markdown to return from ``fetch_search_markdown``.

    Returns:
        A MagicMock whose ``fetch_search_markdown`` is an async mock.
    """
    scraper = MagicMock()
    scraper.fetch_search_markdown = AsyncMock(return_value=markdown)
    return scraper

# ---------------------------------------------------------------------- #
# Happy path
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio()
async def test_search_returns_validated_result() -> None:
    """The service fetches, parses, validates, and returns a SearchResult."""
    service = SearchService(scraper=_make_scraper(SEARCH_MARKDOWN))

    result = await service.search("gaming mouse", max_price=800)

    assert isinstance(result, SearchResult)
    assert result.search_query == "gaming mouse"
    assert result.filters.max_price == 800
    assert result.filters.min_price is None
    assert result.total_items_found == 11084
    assert result.pagination.current_page == 1
    assert result.pagination.total_pages == 102
    assert result.pagination.items_per_page == 40
    assert len(result.products) == 2

    first, second = result.products
    assert first.id == "i111"
    assert first.price == 579.0
    assert first.discount_percentage == 27
    assert first.coins_save == 29
    assert first.sold_count == 184
    assert first.rating_count == 40
    assert first.location == "Punjab"

    assert second.id == "i222"
    assert second.price == 599.0
    assert second.discount_percentage is None
    assert second.location == "Sindh"

@pytest.mark.asyncio()
async def test_search_scraped_at_is_timezone_aware() -> None:
    """The scraped_at timestamp must be tz-aware and use the PKT offset."""
    service = SearchService(scraper=_make_scraper(SEARCH_MARKDOWN))
    result = await service.search("mouse")

    assert isinstance(result.scraped_at, datetime)
    assert result.scraped_at.tzinfo is PKT
    assert result.scraped_at.utcoffset() is not None

@pytest.mark.asyncio()
async def test_search_forwards_filters_and_page_to_scraper() -> None:
    """Filters and page are passed through to the scraper."""
    scraper = _make_scraper(SEARCH_MARKDOWN)
    service = SearchService(scraper=scraper)

    await service.search("mouse", min_price=100, max_price=800, page=2)

    scraper.fetch_search_markdown.assert_awaited_once_with(
        "mouse", min_price=100, max_price=800, page=2
    )

@pytest.mark.asyncio()
async def test_search_strips_query_whitespace() -> None:
    """Surrounding whitespace is stripped from the query before use."""
    scraper = _make_scraper(SEARCH_MARKDOWN)
    service = SearchService(scraper=scraper)

    result = await service.search("  gaming mouse  ")

    assert result.search_query == "gaming mouse"
    scraper.fetch_search_markdown.assert_awaited_once()
    called_query = scraper.fetch_search_markdown.await_args.args[0]
    assert called_query == "gaming mouse"

# ---------------------------------------------------------------------- #
# Empty results
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio()
async def test_search_empty_page_returns_empty_result() -> None:
    """A page with no products returns an empty (but valid) SearchResult."""
    service = SearchService(scraper=_make_scraper(EMPTY_MARKDOWN))
    result = await service.search("nothing")

    assert isinstance(result, SearchResult)
    assert result.products == []
    assert result.total_items_found == 0

@pytest.mark.asyncio()
async def test_search_empty_page_logs_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An empty result logs SEARCH_EMPTY at WARNING level."""
    service = SearchService(scraper=_make_scraper(EMPTY_MARKDOWN))

    with caplog.at_level(
        "WARNING", logger="daraz_ai_shopping_assistant.services.search_service"
    ):
        await service.search("nothing")

    messages = [r.message for r in caplog.records]
    assert "SEARCH_EMPTY" in messages

# ---------------------------------------------------------------------- #
# Validation failures
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio()
async def test_invalid_products_are_dropped_and_logged(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Products that fail Pydantic validation are dropped with a log event."""
    # Force the parser to return one good product and one bad (empty id).
    def fake_parser(markdown: str, *, current_page: int | None = None) -> dict:
        return {
            "total_items_found": 1,
            "pagination": {"current_page": current_page or 1, "total_pages": 1, "items_per_page": 40},
            "products": [
                {
                    "id": "i999",
                    "title": "Good",
                    "url": "https://www.daraz.pk/products/i999.html",
                    "price": 100.0,
                    "currency": "PKR",
                },
                {
                    "id": "",  # invalid: empty id fails min_length=1
                    "title": "Bad",
                    "url": "https://www.daraz.pk/products/bad.html",
                    "price": -1.0,  # invalid: negative price
                },
            ],
        }

    monkeypatch.setattr(
        "daraz_ai_shopping_assistant.services.search_service.parse_search_results",
        fake_parser,
    )

    service = SearchService(scraper=_make_scraper(SEARCH_MARKDOWN))

    with caplog.at_level(
        "WARNING", logger="daraz_ai_shopping_assistant.services.search_service"
    ):
        result = await service.search("mouse")

    assert len(result.products) == 1
    assert result.products[0].id == "i999"

    validation_failures = [
        r for r in caplog.records if r.message == "PRODUCT_VALIDATION_FAILED"
    ]
    assert len(validation_failures) == 1
    ctx = validation_failures[0].ctx  # type: ignore[attr-defined]
    assert ctx["index"] == 1

# ---------------------------------------------------------------------- #
# Input validation
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio()
async def test_empty_query_raises_invalid_request() -> None:
    """An empty or whitespace-only query is rejected before any network call."""
    scraper = _make_scraper(SEARCH_MARKDOWN)
    service = SearchService(scraper=scraper)

    with pytest.raises(InvalidRequestError):
        await service.search("   ")

    scraper.fetch_search_markdown.assert_not_awaited()

@pytest.mark.asyncio()
async def test_contradictory_price_filters_raise() -> None:
    """min_price > max_price is rejected by SearchFilters."""
    from pydantic import ValidationError

    service = SearchService(scraper=_make_scraper(SEARCH_MARKDOWN))

    with pytest.raises(ValidationError):
        await service.search("mouse", min_price=1000, max_price=500)

# ---------------------------------------------------------------------- #
# Logging events
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio()
async def test_search_emits_lifecycle_events(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """SEARCH_STARTED, PRODUCTS_EXTRACTED, and SEARCH_COMPLETED are emitted."""
    service = SearchService(scraper=_make_scraper(SEARCH_MARKDOWN))

    with caplog.at_level(
        "INFO", logger="daraz_ai_shopping_assistant.services.search_service"
    ):
        await service.search("mouse")

    messages = {r.message for r in caplog.records}
    assert "SEARCH_STARTED" in messages
    assert "PRODUCTS_EXTRACTED" in messages
    assert "SEARCH_COMPLETED" in messages

# ---------------------------------------------------------------------- #
# Module-level convenience wrapper
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio()
async def test_module_level_search_products_uses_singleton(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``search_products`` uses the process-wide default service."""
    fake_service = MagicMock()
    fake_service.search = AsyncMock(return_value="fake-result")

    monkeypatch.setattr(
        "daraz_ai_shopping_assistant.services.search_service._default_service",
        fake_service,
    )
    # Reset the singleton cache so get_search_service returns our fake.
    from daraz_ai_shopping_assistant.services import search_service as svc_module

    monkeypatch.setattr(svc_module, "_default_service", fake_service)

    result = await search_products("mouse", max_price=800)

    assert result == "fake-result"
    fake_service.search.assert_awaited_once_with(
        "mouse", min_price=None, max_price=800, page=1
    )

def test_get_search_service_returns_same_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_search_service caches its instance."""
    from daraz_ai_shopping_assistant.services import search_service as svc_module

    monkeypatch.setattr(svc_module, "_default_service", None)

    first = get_search_service()
    second = get_search_service()
    assert first is second

    # Clean up so other tests are not affected by the singleton.
    monkeypatch.setattr(svc_module, "_default_service", None)
