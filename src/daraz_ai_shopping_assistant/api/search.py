"""Product search endpoint.

Exposes ``GET /api/v1/products/search``. This handler is intentionally
thin: it validates query parameters (via FastAPI's ``Query``), delegates
to the search service, and returns the response model. All business logic
lives in ``services.search_service``.

Business logic, parsing, validation of individual products, and error
mapping are the service layer's responsibility. This module owns only
the HTTP contract.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from daraz_ai_shopping_assistant.api.deps import get_search_service_dep
from daraz_ai_shopping_assistant.schemas.search import SearchResponse
from daraz_ai_shopping_assistant.services.search_service import SearchService

router = APIRouter(prefix="/products", tags=["products"])

@router.get(
    "/search",
    response_model=SearchResponse,
    summary="Search Daraz products",
    description=(
        "Search Daraz.pk for products matching a free-text query, "
        "optionally bounded by price. Returns a page of validated products "
        "plus pagination metadata as reported by Daraz."
    ),
    responses={
        400: {"description": "Invalid request (empty query, contradictory price filters)."},
        429: {"description": "Rate-limited by Firecrawl or Daraz."},
        502: {"description": "Upstream scraping failure."},
        504: {"description": "Upstream timeout."},
    },
)
async def search_products_endpoint(
    service: Annotated[SearchService, Depends(get_search_service_dep)],
    q: str = Query(
        ...,
        min_length=1,
        max_length=200,
        description="Free-text search query, e.g. 'gaming mouse'.",
        examples=["gaming mouse"],
    ),
    min_price: float | None = Query(
        None,
        ge=0,
        description="Minimum price in PKR. Omit for no lower bound.",
    ),
    max_price: float | None = Query(
        None,
        ge=0,
        description="Maximum price in PKR. Omit for no upper bound.",
    ),
    page: int = Query(
        1,
        ge=1,
        le=200,
        description="1-indexed page number. Daraz reports up to 100+ pages.",
    ),
) -> SearchResponse:
    """Search Daraz products and return a validated response.

    Args:
        service: Injected search service (see ``api.deps``).
        q: Free-text query.
        min_price: Optional lower bound in PKR.
        max_price: Optional upper bound in PKR.
        page: 1-indexed page number.

    Returns:
        A fully-validated ``SearchResult``.

    Raises:
        InvalidRequestError: When ``q`` is empty after stripping, or the
            price range is contradictory. Mapped to 400.
        ScraperError: On upstream failure. Mapped to 502 (or a more
            specific code for timeouts and rate limits).
    """
    return await service.search(
        q,
        min_price=min_price,
        max_price=max_price,
        page=page,
    )

__all__ = ["router"]
