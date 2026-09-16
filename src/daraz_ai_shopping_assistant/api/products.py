"""Product detail and recommendations endpoints.

Exposes:

    - ``GET /api/v1/products/{product_id}`` -- full product details.
    - ``GET /api/v1/products/{product_id}/recommendations`` -- the
      recommendations list from the same product payload.

Both handlers are thin: they validate the path parameter (via FastAPI's
``Path`` pattern), delegate to ``ProductService``, and return the response
model. All business logic lives in ``services.product_service``.

Route registration order matters: ``search.py`` registers
``/products/search`` as a fixed path. When ``api_router`` includes the
routers, ``search`` MUST come first so that the literal ``search`` segment
is matched before the parameterised ``{product_id}`` route. See
``api/__init__.py``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path

from daraz_ai_shopping_assistant.api.deps import get_product_service_dep
from daraz_ai_shopping_assistant.models.recommendation import Recommendation
from daraz_ai_shopping_assistant.schemas.product import ProductDetailsResponse
from daraz_ai_shopping_assistant.services.product_service import ProductService

router = APIRouter(prefix="/products", tags=["products"])

# Shared path-parameter type: Daraz product IDs are "i" followed by digits.
_ProductId = Annotated[
    str,
    Path(
        ...,
        min_length=2,
        max_length=32,
        pattern=r"^i\d+$",
        description=(
            "Daraz product identifier. Must start with 'i' followed by "
            "digits, e.g. 'i1959941878'."
        ),
        examples=["i1959941878"],
    ),
]

@router.get(
    "/{product_id}",
    response_model=ProductDetailsResponse,
    summary="Get product details",
    description=(
        "Fetch a single Daraz product by ID. Uses Firecrawl's structured "
        "extraction path with the ProductDetails JSON Schema, then "
        "validates the payload through Pydantic before returning it."
    ),
    responses={
        400: {"description": "Invalid product ID format."},
        404: {"description": "Product not found on Daraz."},
        429: {"description": "Rate-limited by Firecrawl or Daraz."},
        502: {"description": "Upstream scraping or extraction failure."},
        504: {"description": "Upstream timeout."},
    },
)
async def get_product_endpoint(
    product_id: _ProductId,
    service: Annotated[ProductService, Depends(get_product_service_dep)],
) -> ProductDetailsResponse:
    """Fetch and return a single product's details.

    Args:
        product_id: Path parameter, validated by FastAPI.
        service: Injected product service (see ``api.deps``).

    Returns:
        A fully-validated ``ProductDetails``.
    """
    return await service.get_product(product_id)

@router.get(
    "/{product_id}/recommendations",
    response_model=list[Recommendation],
    summary="Get product recommendations",
    description=(
        "Return the products Daraz recommends on the given product's page. "
        "These are sourced verbatim from Daraz -- the backend never "
        "generates or fabricates recommendations. Returns an empty list "
        "when the carousel is absent."
    ),
    responses={
        400: {"description": "Invalid product ID format."},
        404: {"description": "Product not found on Daraz."},
        429: {"description": "Rate-limited by Firecrawl or Daraz."},
        502: {"description": "Upstream scraping or extraction failure."},
        504: {"description": "Upstream timeout."},
    },
)
async def get_recommendations_endpoint(
    product_id: _ProductId,
    service: Annotated[ProductService, Depends(get_product_service_dep)],
) -> list[Recommendation]:
    """Return Daraz's recommendations for a product.

    Args:
        product_id: Path parameter, validated by FastAPI.
        service: Injected product service.

    Returns:
        A list of ``Recommendation`` objects, possibly empty.
    """
    return await service.get_recommendations(product_id)

__all__ = ["router"]
