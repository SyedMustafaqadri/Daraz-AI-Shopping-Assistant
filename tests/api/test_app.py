"""Tests for the FastAPI application shell.

Covers the health check, the root landing payload, the OpenAPI surface,
and the catch-all exception handler. Does not exercise any business
endpoint beyond a dependency-override smoke test.
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from daraz_ai_shopping_assistant.api.deps import get_search_service_dep
from daraz_ai_shopping_assistant.main import create_app
from daraz_ai_shopping_assistant.models.product import Product
from daraz_ai_shopping_assistant.models.search import (
    Pagination,
    SearchFilters,
    SearchResult,
)
from daraz_ai_shopping_assistant.services.search_service import SearchService
from daraz_ai_shopping_assistant.utils.datetime import pkt_now


def _sample_result() -> SearchResult:
    """Return a minimal valid SearchResult for mocking.

    Returns:
        A fully populated SearchResult.
    """
    return SearchResult(
        search_query="x",
        filters=SearchFilters(),
        total_items_found=1,
        scraped_at=pkt_now(),
        products=[
            Product(
                id="i1",
                title="Mouse",
                url="https://www.daraz.pk/products/i1.html",
                price=100.0,
            )
        ],
        pagination=Pagination(current_page=1, total_pages=1, items_per_page=40),
    )

@pytest.fixture()
def client() -> Iterator[TestClient]:
    """Return a TestClient for a fresh app instance.

    Yields:
        A TestClient bound to the app.
    """
    app = create_app()
    yield TestClient(app)
    app.dependency_overrides.clear()

# ---------------------------------------------------------------------- #
# Health
# ---------------------------------------------------------------------- #
def test_health_returns_ok(client: TestClient) -> None:
    """GET /health returns 200 with the documented payload."""
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "app" in body
    assert "env" in body
    assert "version" in body

# ---------------------------------------------------------------------- #
# Root
# ---------------------------------------------------------------------- #
def test_root_returns_landing_payload(client: TestClient) -> None:
    """GET / returns a small landing payload, not a 404."""
    response = client.get("/")
    assert response.status_code == 200
    body = response.json()
    assert "service" in body
    assert body["docs"] == "/docs"

# ---------------------------------------------------------------------- #
# OpenAPI
# ---------------------------------------------------------------------- #
def test_openapi_schema_is_available_in_dev(client: TestClient) -> None:
    """The OpenAPI document is served in dev mode."""
    response = client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert schema["info"]["title"]
    assert "/api/v1/products/search" in schema["paths"]

def test_openapi_declares_search_endpoint(client: TestClient) -> None:
    """The search endpoint is present with the documented parameters."""
    schema = client.get("/openapi.json").json()
    search_op = schema["paths"]["/api/v1/products/search"]["get"]
    param_names = {p["name"] for p in search_op["parameters"]}
    assert {"q", "min_price", "max_price", "page"} <= param_names

# ---------------------------------------------------------------------- #
# Catch-all exception handler
# ---------------------------------------------------------------------- #
def test_unhandled_exception_returns_500_without_leaking_details() -> None:
    """A non-DarazScraperError exception yields a generic 500 response."""
    app = create_app()

    @app.get("/_test/boom")
    async def _boom() -> None:  # pragma: no cover - exercised via HTTP
        raise RuntimeError("secret internal detail")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/_test/boom")

    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error."}
    assert "secret" not in response.text

# ---------------------------------------------------------------------- #
# Dependency override smoke test
# ---------------------------------------------------------------------- #
def test_dependency_override_is_honoured() -> None:
    """A dependency override replaces the production service in tests.

    Ensures the override is actually consulted (the mock is awaited) and
    that the app serialises the mock's return value without touching the
    network. If the override were ignored, the route would construct a real
    Firecrawl adapter and attempt an HTTP call.
    """
    app = create_app()

    fake_service = MagicMock(spec=SearchService)
    fake_service.search = AsyncMock(return_value=_sample_result())
    app.dependency_overrides[get_search_service_dep] = lambda: fake_service

    with TestClient(app) as client:
        response = client.get("/api/v1/products/search", params={"q": "x"})

    assert response.status_code == 200
    assert response.json()["search_query"] == "x"

    fake_service.search.assert_awaited_once_with(
        "x", min_price=None, max_price=None, page=1
    )

    app.dependency_overrides.clear()  # cleanup to avoid polluting other tests
