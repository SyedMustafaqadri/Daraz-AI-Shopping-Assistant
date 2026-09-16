"""Tests for ``GET /api/v1/products/search``.

The search service is fully mocked via FastAPI's dependency-override
mechanism, so these tests never touch the network. They verify:

    - the happy path returns a serialised SearchResult,
    - query parameters reach the service verbatim,
    - FastAPI validates parameter constraints (422 on bad input),
    - application errors map to the correct HTTP status codes,
    - the request does not leak internal exception details.
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from daraz_ai_shopping_assistant.api.deps import get_search_service_dep
from daraz_ai_shopping_assistant.core.exceptions import (
    InvalidRequestError,
    ScraperError,
    ScraperTimeoutError,
    UpstreamRateLimitError,
)
from daraz_ai_shopping_assistant.main import create_app
from daraz_ai_shopping_assistant.models.product import Product
from daraz_ai_shopping_assistant.models.search import (
    Pagination,
    SearchFilters,
    SearchResult,
)
from daraz_ai_shopping_assistant.services.search_service import SearchService
from daraz_ai_shopping_assistant.utils.datetime import pkt_now


# ---------------------------------------------------------------------- #
# Fixtures
# ---------------------------------------------------------------------- #
@pytest.fixture()
def mock_service() -> MagicMock:
    """Return a mocked :class:`SearchService` with async ``search``.

    Returns:
        A MagicMock where ``search`` is an AsyncMock.
    """
    service = MagicMock(spec=SearchService)
    service.search = AsyncMock()
    return service

@pytest.fixture()
def client(mock_service: MagicMock) -> Iterator[TestClient]:
    """Return a TestClient wired to a fresh app with the service mocked.

    Args:
        mock_service: The mocked service to inject.

    Yields:
        A :class:`TestClient` bound to the app.
    """
    app = create_app()
    app.dependency_overrides[get_search_service_dep] = lambda: mock_service
    yield TestClient(app)
    app.dependency_overrides.clear()

def _sample_result(query: str = "gaming mouse") -> SearchResult:
    """Build a minimal, valid :class:`SearchResult` for the mock to return.

    Args:
        query: The search query to embed.

    Returns:
        A fully-populated :class:`SearchResult`.
    """
    return SearchResult(
        search_query=query,
        filters=SearchFilters(max_price=800.0),
        total_items_found=11084,
        scraped_at=pkt_now(),
        products=[
            Product(
                id="i1959941878",
                title="RGB Gaming Mouse",
                url="https://www.daraz.pk/products/7-i1959941878.html",
                price=579.0,
                discount_percentage=27,
                location="Punjab",
            )
        ],
        pagination=Pagination(current_page=1, total_pages=102, items_per_page=40),
    )

# ---------------------------------------------------------------------- #
# Happy path
# ---------------------------------------------------------------------- #
def test_search_returns_serialised_result(
    client: TestClient, mock_service: MagicMock
) -> None:
    """A valid query returns 200 and a fully-populated response body."""
    mock_service.search.return_value = _sample_result()

    response = client.get(
        "/api/v1/products/search",
        params={"q": "gaming mouse", "max_price": 800},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "Daraz.pk"
    assert body["search_query"] == "gaming mouse"
    assert body["filters"] == {"min_price": None, "max_price": 800.0}
    assert body["total_items_found"] == 11084
    assert body["pagination"]["total_pages"] == 102
    assert len(body["products"]) == 1
    assert body["products"][0]["id"] == "i1959941878"
    assert body["products"][0]["price"] == 579.0

def test_search_forwards_parameters_to_service(
    client: TestClient, mock_service: MagicMock
) -> None:
    """All four query parameters are forwarded to the service verbatim."""
    mock_service.search.return_value = _sample_result()

    client.get(
        "/api/v1/products/search",
        params={"q": "mouse", "min_price": 100, "max_price": 800, "page": 2},
    )

    mock_service.search.assert_awaited_once_with(
        "mouse", min_price=100.0, max_price=800.0, page=2
    )

def test_search_uses_defaults_when_optional_params_omitted(
    client: TestClient, mock_service: MagicMock
) -> None:
    """Missing optional params reach the service as None / 1."""
    mock_service.search.return_value = _sample_result()

    client.get("/api/v1/products/search", params={"q": "mouse"})

    mock_service.search.assert_awaited_once_with(
        "mouse", min_price=None, max_price=None, page=1
    )

# ---------------------------------------------------------------------- #
# FastAPI parameter validation (422)
# ---------------------------------------------------------------------- #
def test_search_missing_q_returns_422(client: TestClient) -> None:
    """Omitting the required query parameter yields 422."""
    response = client.get("/api/v1/products/search")
    assert response.status_code == 422

def test_search_blank_q_returns_422(client: TestClient) -> None:
    """An empty q is rejected before reaching the service."""
    response = client.get("/api/v1/products/search", params={"q": ""})
    assert response.status_code == 422

def test_search_negative_min_price_returns_422(client: TestClient) -> None:
    """A negative price bound is rejected by parameter validation."""
    response = client.get(
        "/api/v1/products/search", params={"q": "mouse", "min_price": -1}
    )
    assert response.status_code == 422

def test_search_zero_page_returns_422(client: TestClient) -> None:
    """A page number below 1 is rejected."""
    response = client.get(
        "/api/v1/products/search", params={"q": "mouse", "page": 0}
    )
    assert response.status_code == 422

# ---------------------------------------------------------------------- #
# Application-level error mapping
# ---------------------------------------------------------------------- #
def test_search_invalid_request_returns_400(
    client: TestClient, mock_service: MagicMock
) -> None:
    """InvalidRequestError from the service maps to HTTP 400."""
    mock_service.search.side_effect = InvalidRequestError("Search query must not be empty.")

    response = client.get("/api/v1/products/search", params={"q": "x"})

    assert response.status_code == 400
    assert "must not be empty" in response.json()["detail"]

def test_search_scraper_error_returns_502(
    client: TestClient, mock_service: MagicMock
) -> None:
    """A generic upstream failure maps to HTTP 502."""
    mock_service.search.side_effect = ScraperError(
        "Firecrawl scrape failed", url="https://www.daraz.pk/catalog/"
    )

    response = client.get("/api/v1/products/search", params={"q": "x"})

    assert response.status_code == 502
    assert "Firecrawl scrape failed" in response.json()["detail"]

def test_search_rate_limit_returns_429(
    client: TestClient, mock_service: MagicMock
) -> None:
    """A rate-limit failure maps to HTTP 429."""
    mock_service.search.side_effect = UpstreamRateLimitError("too many requests")

    response = client.get("/api/v1/products/search", params={"q": "x"})

    assert response.status_code == 429

def test_search_timeout_returns_504(
    client: TestClient, mock_service: MagicMock
) -> None:
    """A timeout maps to HTTP 504."""
    mock_service.search.side_effect = ScraperTimeoutError("timed out")

    response = client.get("/api/v1/products/search", params={"q": "x"})

    assert response.status_code == 504

def test_error_response_never_leaks_context(
    client: TestClient, mock_service: MagicMock
) -> None:
    """Internal context must not be returned to the client."""
    mock_service.search.side_effect = ScraperError(
        "boom",
        url="https://secret.example.com/internal",
        upstream_status=500,
    )

    response = client.get("/api/v1/products/search", params={"q": "x"})

    body = response.json()
    assert set(body.keys()) == {"detail"}
    assert "secret.example.com" not in response.text
    assert "upstream_status" not in response.text
