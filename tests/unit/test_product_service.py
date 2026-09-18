"""Unit tests for :class:`ProductService`.

The scraper is mocked, so these tests never touch the network. They cover:

    - the happy path (fetch -> validate -> return),
    - empty payload -> ProductNotFoundError,
    - invalid payload -> ParseError,
    - ID validation (empty, wrong format, whitespace),
    - typed scraper errors propagating unchanged,
    - lifecycle log events.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from daraz_ai_shopping_assistant.core.exceptions import (
    InvalidRequestError,
    ParseError,
    ProductNotFoundError,
    ScraperError,
    ScraperTimeoutError,
)
from daraz_ai_shopping_assistant.services.product_service import ProductService


# ---------------------------------------------------------------------- #
# Fixtures
# ---------------------------------------------------------------------- #
def _valid_payload() -> dict[str, object]:
    """Return a minimal payload that satisfies ``ProductDetails``.

    Returns:
        A dict that should pass validation.
    """
    return {
        "id": "i1959941878",
        "title": "RGB Gaming Mouse",
        "url": "https://www.daraz.pk/products/7-i1959941878.html",
        "image": "https://img.drz.lazcdn.com/static/pk/p/abc.jpg",
        "price": 579.0,
        "currency": "PKR",
        "discount_percentage": 27,
        "sold_count": 184,
        "rating_count": 40,
        "location": "Punjab",
        "description": "A great gaming mouse.",
        "specifications": {"Brand": "Logitech", "DPI": "8000"},
        "seller": {"name": "Logitech Store", "rating": 4.8, "positive_rate": 95.0},
        "shipping": {"free_shipping": True, "estimated_delivery": "2-4 days"},
        "availability": "In Stock",
        "variants": [{"name": "Black", "price": 579.0, "available": True}],
        "reviews": [{"rating": 5.0, "comment": "Great!", "author": "Ali"}],
    }

def _make_scraper(payload: dict[str, object] | None) -> MagicMock:
    """Return a mock scraper that yields the given payload.

    Args:
        payload: The value to return from ``fetch_product_payload``.

    Returns:
        A MagicMock with ``fetch_product_payload`` as an AsyncMock.
    """
    scraper = MagicMock()
    scraper.fetch_product_payload = AsyncMock(return_value=payload)
    return scraper

# ---------------------------------------------------------------------- #
# Happy path
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio()
async def test_get_product_happy_path() -> None:
    """A valid payload returns a validated ProductDetails."""
    scraper = _make_scraper(_valid_payload())
    service = ProductService(scraper=scraper)

    product = await service.get_product("i1959941878")

    assert product.id == "i1959941878"
    assert product.title == "RGB Gaming Mouse"
    assert product.price == 579.0
    assert product.discount_percentage == 27
    assert product.seller.name == "Logitech Store"
    assert product.shipping.free_shipping is True
    assert product.availability == "In Stock"
    assert len(product.variants) == 1
    assert len(product.reviews) == 1

    scraper.fetch_product_payload.assert_awaited_once_with("i1959941878")

@pytest.mark.asyncio()
async def test_get_product_strips_whitespace_on_id() -> None:
    """Surrounding whitespace is stripped before the scraper is called."""
    scraper = _make_scraper(_valid_payload())
    service = ProductService(scraper=scraper)

    await service.get_product("  i1959941878  ")

    scraper.fetch_product_payload.assert_awaited_once_with("i1959941878")

@pytest.mark.asyncio()
async def test_get_product_preserves_null_fields() -> None:
    """Fields missing from the payload remain None after validation."""
    scraper = _make_scraper(
        {
            "id": "i111",
            "title": "Sparse Product",
            "url": "https://www.daraz.pk/products/i111.html",
            "price": 100.0,
        }
    )
    service = ProductService(scraper=scraper)

    product = await service.get_product("i111")

    assert product.image is None
    assert product.discount_percentage is None
    assert product.description is None
    assert product.availability is None

# ---------------------------------------------------------------------- #
# Not found
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio()
async def test_get_product_empty_payload_raises_not_found() -> None:
    """An empty dict from the scraper maps to ProductNotFoundError."""
    service = ProductService(scraper=_make_scraper({}))

    with pytest.raises(ProductNotFoundError) as excinfo:
        await service.get_product("i1959941878")

    assert "i1959941878" in str(excinfo.value)

@pytest.mark.asyncio()
async def test_get_product_none_payload_raises_not_found() -> None:
    """A None payload maps to ProductNotFoundError."""
    service = ProductService(scraper=_make_scraper(None))

    with pytest.raises(ProductNotFoundError):
        await service.get_product("i1959941878")

# ---------------------------------------------------------------------- #
# Validation failures
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio()
async def test_get_product_invalid_payload_raises_parse_error() -> None:
    """A payload that fails Pydantic validation maps to ParseError."""
    bad_payload = {
        "id": "",  # invalid: empty id
        "title": "Broken",
        "url": "not-a-url",  # invalid: must start with http(s)://
        "price": -1.0,  # invalid: negative price
    }
    service = ProductService(scraper=_make_scraper(bad_payload))

    with pytest.raises(ParseError) as excinfo:
        await service.get_product("i1959941878")

    assert excinfo.value.source == "product"
    assert "i1959941878" in str(excinfo.value)

@pytest.mark.asyncio()
async def test_get_product_validation_failure_logs_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A validation failure emits PRODUCT_VALIDATION_FAILED at WARNING."""
    bad_payload = {"id": "i1", "title": "t", "url": "bad", "price": -1.0}
    service = ProductService(scraper=_make_scraper(bad_payload))

    with caplog.at_level(
        "WARNING", logger="daraz_ai_shopping_assistant.services.product_service"
    ), pytest.raises(ParseError):
        await service.get_product("i1")

    messages = [r.message for r in caplog.records]
    assert "PRODUCT_VALIDATION_FAILED" in messages

# ---------------------------------------------------------------------- #
# ID validation
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio()
@pytest.mark.parametrize(
    "bad_id",
    ["", "   ", "not-an-id", "1959941878", "iABC", "i-123", "abc123"],
    ids=["empty", "whitespace", "words", "no-prefix", "letters", "hyphen", "no-i"],
)
async def test_get_product_invalid_id_raises_invalid_request(bad_id: str) -> None:
    """Invalid product IDs are rejected before any scraper call."""
    scraper = _make_scraper(_valid_payload())
    service = ProductService(scraper=scraper)

    with pytest.raises(InvalidRequestError):
        await service.get_product(bad_id)

    scraper.fetch_product_payload.assert_not_awaited()

# ---------------------------------------------------------------------- #
# Scraper errors
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio()
async def test_get_product_propagates_typed_scraper_error() -> None:
    """A typed scraper error propagates unchanged (no wrapping)."""
    scraper = MagicMock()
    scraper.fetch_product_payload = AsyncMock(
        side_effect=ScraperTimeoutError("timed out", url="https://example.com")
    )
    service = ProductService(scraper=scraper)

    with pytest.raises(ScraperTimeoutError):
        await service.get_product("i1959941878")

@pytest.mark.asyncio()
async def test_get_product_propagates_generic_scraper_error() -> None:
    """A generic ScraperError also propagates unchanged."""
    scraper = MagicMock()
    scraper.fetch_product_payload = AsyncMock(
        side_effect=ScraperError("bad gateway", url="https://example.com")
    )
    service = ProductService(scraper=scraper)

    with pytest.raises(ScraperError):
        await service.get_product("i1959941878")

# ---------------------------------------------------------------------- #
# Logging
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio()
async def test_get_product_emits_lifecycle_events(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """PRODUCT_FETCH_STARTED and PRODUCT_FETCH_COMPLETED are emitted."""
    service = ProductService(scraper=_make_scraper(_valid_payload()))

    with caplog.at_level(
        "INFO", logger="daraz_ai_shopping_assistant.services.product_service"
    ):
        await service.get_product("i1959941878")

    messages = {r.message for r in caplog.records}
    assert "PRODUCT_FETCH_STARTED" in messages
    assert "PRODUCT_FETCH_COMPLETED" in messages
