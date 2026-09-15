"""Unit tests for :mod:`daraz_ai_shopping_assistant.models.search`."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from daraz_ai_shopping_assistant.models.search import (
    Pagination,
    SearchFilters,
    SearchResult,
)

PKT = timezone(timedelta(hours=5))


def _product_payload() -> dict[str, object]:
    """Return a minimal valid product payload for embedding in SearchResult.

    Returns:
        A dict that should pass :class:`Product` validation.
    """
    return {
        "id": "i1",
        "title": "Mouse",
        "url": "https://www.daraz.pk/products/i1.html",
        "price": 100.0,
    }


# ---------------------------------------------------------------------- #
# SearchFilters
# ---------------------------------------------------------------------- #
def test_filters_both_none() -> None:
    """Both bounds may be None."""
    filters = SearchFilters()
    assert filters.min_price is None
    assert filters.max_price is None


def test_filters_only_max() -> None:
    """Only max_price may be set."""
    filters = SearchFilters(max_price=800.0)
    assert filters.min_price is None
    assert filters.max_price == 800.0


def test_filters_only_min() -> None:
    """Only min_price may be set."""
    filters = SearchFilters(min_price=100.0)
    assert filters.min_price == 100.0
    assert filters.max_price is None


def test_filters_min_greater_than_max_rejected() -> None:
    """min_price > max_price must be rejected."""
    with pytest.raises(ValidationError):
        SearchFilters(min_price=1000.0, max_price=500.0)


def test_filters_equal_bounds_allowed() -> None:
    """min_price == max_price is allowed."""
    filters = SearchFilters(min_price=500.0, max_price=500.0)
    assert filters.min_price == filters.max_price


def test_filters_negative_rejected() -> None:
    """Negative bounds must be rejected."""
    with pytest.raises(ValidationError):
        SearchFilters(max_price=-1.0)


# ---------------------------------------------------------------------- #
# Pagination
# ---------------------------------------------------------------------- #
def test_pagination_defaults() -> None:
    """Pagination defaults: page 1, no total_pages, 40 per page."""
    pagination = Pagination()
    assert pagination.current_page == 1
    assert pagination.total_pages is None
    assert pagination.items_per_page == 40


def test_pagination_page_must_be_positive() -> None:
    """current_page must be >= 1."""
    with pytest.raises(ValidationError):
        Pagination(current_page=0)


def test_pagination_full() -> None:
    """All fields supplied must round-trip."""
    pagination = Pagination(current_page=2, total_pages=102, items_per_page=40)
    assert pagination.current_page == 2
    assert pagination.total_pages == 102


# ---------------------------------------------------------------------- #
# SearchResult
# ---------------------------------------------------------------------- #
def test_search_result_minimal() -> None:
    """A SearchResult with a query, timestamp, and one product is valid."""
    now = datetime.now(tz=PKT)
    result = SearchResult(
        search_query="Gaming Mouse",
        scraped_at=now,
        products=[_product_payload()],  # type: ignore[list-item]
    )
    assert result.source == "Daraz.pk"
    assert result.search_query == "Gaming Mouse"
    assert result.total_items_found is None
    assert len(result.products) == 1
    assert isinstance(result.products[0].price, float)


def test_search_result_empty_products_allowed() -> None:
    """An empty products list is valid (zero matches)."""
    result = SearchResult(
        search_query="nothing",
        scraped_at=datetime.now(tz=PKT),
        products=[],
    )
    assert result.products == []


def test_search_result_rejects_naive_datetime() -> None:
    """A naive scraped_at must be rejected.

    The naive datetime is intentional — this test verifies that the model's
    timezone-aware validator rejects it.
    """
    with pytest.raises(ValidationError):
        SearchResult(
            search_query="x",
            scraped_at=datetime(2026, 9, 15, 10, 49, 6),
            products=[],
        )



def test_search_result_accepts_offset_datetime() -> None:
    """A datetime with a +05:00 offset must be accepted."""
    result = SearchResult(
        search_query="x",
        scraped_at=datetime.fromisoformat("2026-09-15T10:49:06+05:00"),
        products=[],
    )
    assert result.scraped_at.tzinfo is not None


def test_search_result_rejects_extra_fields() -> None:
    """Undeclared fields must be rejected."""
    with pytest.raises(ValidationError):
        SearchResult(
            search_query="x",
            scraped_at=datetime.now(tz=PKT),
            products=[],
            unexpected="nope",  # type: ignore[call-arg]
        )


def test_search_result_rejects_invalid_product() -> None:
    """A product without a price must fail validation through SearchResult."""
    with pytest.raises(ValidationError):
        SearchResult(
            search_query="x",
            scraped_at=datetime.now(tz=PKT),
            products=[{"id": "i1", "title": "t", "url": "https://x.pk/a"}],  # type: ignore[list-item]
        )
