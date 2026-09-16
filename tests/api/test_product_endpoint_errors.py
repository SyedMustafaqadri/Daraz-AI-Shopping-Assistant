"""Error-mapping tests for the product detail endpoint.

Every test overrides the product service to raise a specific exception
from the hierarchy and asserts that the global exception handler in
``api/exception_handlers.py`` maps it to the correct HTTP status.

Covered mappings:

    InvalidRequestError    -> 400
    ProductNotFoundError   -> 404
    UpstreamRateLimitError -> 429
    ScraperError           -> 502
    ParseError             -> 502
    ScraperTimeoutError    -> 504
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from daraz_ai_shopping_assistant.api.deps import get_product_service_dep
from daraz_ai_shopping_assistant.core.exceptions import (
    InvalidRequestError,
    ParseError,
    ProductNotFoundError,
    ScraperError,
    ScraperTimeoutError,
    UpstreamRateLimitError,
)
from daraz_ai_shopping_assistant.main import create_app
from daraz_ai_shopping_assistant.services.product_service import ProductService


@pytest.fixture()
def mock_product_service() -> MagicMock:
    """Return a mocked ProductService with async methods."""
    service = MagicMock(spec=ProductService)
    service.get_product = AsyncMock()
    service.get_recommendations = AsyncMock()
    return service

@pytest.fixture()
def client(mock_product_service: MagicMock) -> Iterator[TestClient]:
    """Return a TestClient with the product service mocked."""
    app = create_app()
    app.dependency_overrides[get_product_service_dep] = lambda: mock_product_service
    yield TestClient(app)
    app.dependency_overrides.clear()

# ---------------------------------------------------------------------- #
# Error mapping -- product endpoint
# ---------------------------------------------------------------------- #
def test_invalid_request_returns_400(
    client: TestClient, mock_product_service: MagicMock
) -> None:
    """InvalidRequestError from the service maps to 400."""
    mock_product_service.get_product.side_effect = InvalidRequestError("bad id")
    response = client.get("/api/v1/products/i1959941878")
    assert response.status_code == 400
    assert "bad id" in response.json()["detail"]

def test_not_found_returns_404(
    client: TestClient, mock_product_service: MagicMock
) -> None:
    """ProductNotFoundError maps to 404."""
    mock_product_service.get_product.side_effect = ProductNotFoundError(
        "Product 'i999' was not found on Daraz."
    )
    response = client.get("/api/v1/products/i999")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()

def test_parse_error_returns_502(
    client: TestClient, mock_product_service: MagicMock
) -> None:
    """ParseError (validation failure) maps to 502."""
    mock_product_service.get_product.side_effect = ParseError(
        "Product payload failed validation.", source="product"
    )
    response = client.get("/api/v1/products/i1959941878")
    assert response.status_code == 502

def test_scraper_error_returns_502(
    client: TestClient, mock_product_service: MagicMock
) -> None:
    """A generic ScraperError maps to 502."""
    mock_product_service.get_product.side_effect = ScraperError(
        "Firecrawl scrape failed", url="https://example.com"
    )
    response = client.get("/api/v1/products/i1959941878")
    assert response.status_code == 502

def test_rate_limit_returns_429(
    client: TestClient, mock_product_service: MagicMock
) -> None:
    """UpstreamRateLimitError maps to 429."""
    mock_product_service.get_product.side_effect = UpstreamRateLimitError("429")
    response = client.get("/api/v1/products/i1959941878")
    assert response.status_code == 429

def test_timeout_returns_504(
    client: TestClient, mock_product_service: MagicMock
) -> None:
    """ScraperTimeoutError maps to 504."""
    mock_product_service.get_product.side_effect = ScraperTimeoutError("timeout")
    response = client.get("/api/v1/products/i1959941878")
    assert response.status_code == 504

def test_error_response_does_not_leak_context(
    client: TestClient, mock_product_service: MagicMock
) -> None:
    """Internal context must not appear in the response body."""
    mock_product_service.get_product.side_effect = ScraperError(
        "boom",
        url="https://internal.example.com/secret",
        upstream_status=500,
    )
    response = client.get("/api/v1/products/i1959941878")
    assert set(response.json().keys()) == {"detail"}
    assert "secret" not in response.text
    assert "upstream_status" not in response.text

# ---------------------------------------------------------------------- #
# Error mapping -- recommendations endpoint
# ---------------------------------------------------------------------- #
def test_recommendations_not_found_returns_404(
    client: TestClient, mock_product_service: MagicMock
) -> None:
    """A missing product propagates to 404 on the recommendations endpoint."""
    mock_product_service.get_recommendations.side_effect = ProductNotFoundError(
        "not found"
    )
    response = client.get("/api/v1/products/i999/recommendations")
    assert response.status_code == 404

def test_recommendations_parse_error_returns_502(
    client: TestClient, mock_product_service: MagicMock
) -> None:
    """A ParseError on recommendations maps to 502."""
    mock_product_service.get_recommendations.side_effect = ParseError(
        "bad payload", source="product"
    )
    response = client.get("/api/v1/products/i1959941878/recommendations")
    assert response.status_code == 502
