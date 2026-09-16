"""Tests for the product detail and recommendations endpoints (happy path).

The product service is fully mocked via FastAPI's dependency-override
mechanism, so these tests never touch the network. Error-mapping coverage
lives in ``tests/api/test_product_endpoint_errors.py``; this file covers:

    - happy path for both endpoints,
    - path-parameter validation (422 on bad ID format),
    - route-registration order: ``/products/search`` must resolve to the
      search endpoint, not to ``/products/{product_id}``,
    - the OpenAPI surface.
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from daraz_ai_shopping_assistant.api.deps import (
    get_product_service_dep,
    get_search_service_dep,
)
from daraz_ai_shopping_assistant.main import create_app
from daraz_ai_shopping_assistant.models.product import ProductDetails
from daraz_ai_shopping_assistant.models.recommendation import Recommendation
from daraz_ai_shopping_assistant.models.search import SearchResult
from daraz_ai_shopping_assistant.services.product_service import ProductService
from daraz_ai_shopping_assistant.services.search_service import SearchService
from daraz_ai_shopping_assistant.utils.datetime import pkt_now


# ---------------------------------------------------------------------- #
# Fixtures
# ---------------------------------------------------------------------- #
def _product_payload() -> dict[str, object]:
    """Return a minimal valid ProductDetails payload."""
    return {
        "id": "i1959941878",
        "title": "RGB Gaming Mouse",
        "url": "https://www.daraz.pk/products/7-i1959941878.html",
        "price": 579.0,
        "discount_percentage": 27,
        "location": "Punjab",
        "recommendations": [
            {
                "id": "i999",
                "title": "Another Mouse",
                "url": "https://www.daraz.pk/products/i999.html",
                "price": 799.0,
            }
        ],
    }

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
# Happy path
# ---------------------------------------------------------------------- #
def test_get_product_returns_serialised_details(
    client: TestClient, mock_product_service: MagicMock
) -> None:
    """A valid product ID returns 200 with the details payload."""
    mock_product_service.get_product.return_value = ProductDetails.model_validate(
        _product_payload()
    )
    response = client.get("/api/v1/products/i1959941878")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "i1959941878"
    assert body["title"] == "RGB Gaming Mouse"
    assert body["price"] == 579.0
    assert body["discount_percentage"] == 27
    assert body["location"] == "Punjab"
    assert len(body["recommendations"]) == 1
    assert body["recommendations"][0]["id"] == "i999"
    mock_product_service.get_product.assert_awaited_once_with("i1959941878")

def test_get_recommendations_returns_list(
    client: TestClient, mock_product_service: MagicMock
) -> None:
    """A valid product ID returns the recommendations list."""
    mock_product_service.get_recommendations.return_value = [
        Recommendation.model_validate(
            {
                "id": "i999",
                "title": "Another Mouse",
                "url": "https://www.daraz.pk/products/i999.html",
                "price": 799.0,
            }
        )
    ]
    response = client.get("/api/v1/products/i1959941878/recommendations")
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    assert len(body) == 1
    assert body[0]["id"] == "i999"
    mock_product_service.get_recommendations.assert_awaited_once_with("i1959941878")

def test_get_recommendations_empty_list(
    client: TestClient, mock_product_service: MagicMock
) -> None:
    """An empty recommendation list returns 200 with []."""
    mock_product_service.get_recommendations.return_value = []
    response = client.get("/api/v1/products/i1959941878/recommendations")
    assert response.status_code == 200
    assert response.json() == []

# ---------------------------------------------------------------------- #
# Route ordering
# ---------------------------------------------------------------------- #
def test_search_route_resolves_to_search_endpoint(
    mock_product_service: MagicMock,
) -> None:
    """``/products/search`` must hit the search endpoint, not the product one.

    Without correct router-inclusion order in ``api/__init__.py``, Starlette
    would match ``/products/search`` against ``/products/{product_id}`` and
    return 422 because 'search' fails the ``^i\\d+$`` pattern.
    """
    app = create_app()
    app.dependency_overrides[get_product_service_dep] = lambda: mock_product_service
    mock_search_service = MagicMock(spec=SearchService)
    mock_search_service.search = AsyncMock(
        return_value=SearchResult(
            search_query="x", scraped_at=pkt_now(), products=[]
        )
    )
    app.dependency_overrides[get_search_service_dep] = lambda: mock_search_service

    with TestClient(app) as test_client:
        response = test_client.get("/api/v1/products/search", params={"q": "x"})

    assert response.status_code == 200
    assert "search_query" in response.json()
    mock_product_service.get_product.assert_not_awaited()
    mock_search_service.search.assert_awaited_once()
    app.dependency_overrides.clear()

# ---------------------------------------------------------------------- #
# Path parameter validation
# ---------------------------------------------------------------------- #
def test_get_product_invalid_id_returns_422(client: TestClient) -> None:
    """A product ID not matching the pattern is rejected by FastAPI."""
    response = client.get("/api/v1/products/not-an-id")
    assert response.status_code == 422

def test_get_product_letters_in_id_returns_422(client: TestClient) -> None:
    """Letters after the leading 'i' are rejected."""
    response = client.get("/api/v1/products/iABC123")
    assert response.status_code == 422

def test_get_product_no_prefix_returns_422(client: TestClient) -> None:
    """A numeric-only ID is rejected (missing the 'i' prefix)."""
    response = client.get("/api/v1/products/1959941878")
    assert response.status_code == 422

# ---------------------------------------------------------------------- #
# OpenAPI surface
# ---------------------------------------------------------------------- #
def test_openapi_declares_both_endpoints(client: TestClient) -> None:
    """Both product endpoints appear in the OpenAPI schema."""
    schema = client.get("/openapi.json").json()
    paths = schema["paths"]
    assert "/api/v1/products/{product_id}" in paths
    assert "/api/v1/products/{product_id}/recommendations" in paths
