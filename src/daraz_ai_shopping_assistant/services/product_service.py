"""Product detail orchestration service.

This service wires the product-detail endpoint to the scraper's structured
extraction path (ADR-001):

    scraper.fetch_product_payload(id)    -> untrusted dict
    ProductDetails.model_validate(dict)  -> trusted ProductDetails
    ProductDetails                       -> response envelope

The service is the ONLY layer that:

    - decides what constitutes an empty / missing product payload,
    - converts a Pydantic ValidationError into a typed ParseError,
    - normalises small LLM quirks on the way out (see _normalise_product_id),
    - emits the PRODUCT_FETCH_* lifecycle events,
    - reads from and writes to the local scrape store,
    - exposes the recommendations list from the product payload.

Unlike the search service, this service does not need pagination or
filters. It also does not need a separate endpoint for recommendations --
Daraz renders the recommendation carousel on the same product page, so
that data is already inside the product payload.
"""

from __future__ import annotations

import re
import time

from pydantic import ValidationError

from daraz_ai_shopping_assistant.core.config import settings
from daraz_ai_shopping_assistant.core.exceptions import (
    DarazScraperError,
    InvalidRequestError,
    ParseError,
    ProductNotFoundError,
)
from daraz_ai_shopping_assistant.core.logging import get_logger
from daraz_ai_shopping_assistant.models.product import ProductDetails
from daraz_ai_shopping_assistant.models.recommendation import Recommendation
from daraz_ai_shopping_assistant.scrapers.base import DarazScraper
from daraz_ai_shopping_assistant.scrapers.daraz import FirecrawlDarazScraper
from daraz_ai_shopping_assistant.storage.json_store import ScrapeStore

logger = get_logger(__name__)

#: Daraz product identifiers look like ``i1959941878`` -- a lowercase "i"
#: followed by digits. Rejecting anything else at the service boundary
#: means garbage in the path never reaches Firecrawl.
_PRODUCT_ID_PATTERN: re.Pattern[str] = re.compile(r"^i\d+$")

#: Matches a bare numeric Daraz id (no "i" prefix). Used by the id
#: normaliser to repair the common LLM extraction bug where the prefix is
#: dropped.
_BARE_NUMERIC_ID_PATTERN: re.Pattern[str] = re.compile(r"^\d+$")

class ProductService:
    """Fetch, validate, and return a single Daraz product.

    Attributes:
        _scraper: The ``DarazScraper`` used to fetch the structured payload.
        _store: Optional :class:`ScrapeStore` for payload persistence.
            When ``None``, every request scrapes fresh.
    """

    def __init__(
        self,
        scraper: DarazScraper | None = None,
        *,
        store: ScrapeStore | None = None,
    ) -> None:
        """Initialise the service.

        Args:
            scraper: Optional scraper to use. When ``None``, a default
                ``FirecrawlDarazScraper`` is created. Tests inject a mock.
            store: Optional scrape store for local persistence. When
                ``None``, the service scrapes on every call.
        """
        self._scraper: DarazScraper = (
            scraper if scraper is not None else FirecrawlDarazScraper()
        )
        self._store: ScrapeStore | None = store

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    async def get_product(self, product_id: str) -> ProductDetails:
        """Fetch and validate a single Daraz product.

        Args:
            product_id: Daraz product identifier, e.g. ``"i1959941878"``.

        Returns:
            A validated ``ProductDetails`` instance.

        Raises:
            InvalidRequestError: When ``product_id`` is empty or does not
                match the Daraz ID pattern. Maps to HTTP 400.
            ProductNotFoundError: When the product page returns an empty
                payload (Daraz served a "not found" page). Maps to 404.
            ParseError: When the structured payload fails Pydantic
                validation. Maps to 502.
            DarazScraperError: For upstream fetch failures (timeout, rate
                limit, generic scraper error). Mapped by the API layer.
        """
        normalized_id = self._validate_product_id(product_id)
        cache_key = f"product:{normalized_id}"

        # 1. Store lookup. A hit short-circuits the whole scrape path.
        if self._store is not None:
            cached = self._store.get(cache_key)
            if cached is not None:
                logger.info(
                    "PRODUCT_STORE_HIT",
                    extra={"ctx": {"product_id": normalized_id, "key": cache_key}},
                )
                return ProductDetails.model_validate(cached)

        logger.info(
            "PRODUCT_FETCH_STARTED",
            extra={"ctx": {"product_id": normalized_id}},
        )
        started_at = time.monotonic()

        # 2. Fetch the structured payload via the scraper. Typed errors
        #    from the scraper propagate unchanged -- the API layer already
        #    knows how to map them to HTTP status codes.
        try:
            payload = await self._scraper.fetch_product_payload(normalized_id)
        except DarazScraperError:
            raise
        except Exception as exc:
            logger.exception(
                "PRODUCT_FETCH_UNEXPECTED_ERROR",
                extra={"ctx": {"product_id": normalized_id}},
            )
            raise ParseError(
                f"Unexpected error fetching product {normalized_id!r}.",
                source="product",
                context={"product_id": normalized_id, "error": type(exc).__name__},
            ) from exc

        # 3. An empty payload means Daraz served us a "not found" page.
        if not payload:
            raise ProductNotFoundError(
                f"Product {normalized_id!r} was not found on Daraz.",
                context={"product_id": normalized_id},
            )

        # 4. Validate the payload through Pydantic. A ValidationError means
        #    either the LLM extraction drifted or Daraz changed the page.
        #    Either way it is an upstream problem, so it maps to 502.
        try:
            product = ProductDetails.model_validate(payload)
        except ValidationError as exc:
            logger.warning(
                "PRODUCT_VALIDATION_FAILED",
                extra={
                    "ctx": {
                        "product_id": normalized_id,
                        "error_count": len(exc.errors()),
                        "first_error": _first_error_summary(exc),
                    }
                },
            )
            raise ParseError(
                f"Product {normalized_id!r} payload failed validation.",
                source="product",
                context={
                    "product_id": normalized_id,
                    "error_count": len(exc.errors()),
                },
            ) from exc

        # 5. Normalise small LLM quirks. The id field in particular is
        #    reliable in input but frequently loses its "i" prefix during
        #    extraction.
        product = _normalise_product_id(product, requested_id=normalized_id)

        duration_ms = int((time.monotonic() - started_at) * 1000)
        logger.info(
            "PRODUCT_FETCH_COMPLETED",
            extra={
                "ctx": {
                    "product_id": normalized_id,
                    "recommendations": len(product.recommendations),
                    "duration_ms": duration_ms,
                }
            },
        )

        # 6. Persist for future requests.
        if self._store is not None:
            await self._store.set(
                cache_key,
                kind="product",
                payload=product.model_dump(mode="json"),
                ttl_seconds=settings.scrape_store_product_ttl_seconds,
            )

        return product

    async def get_recommendations(self, product_id: str) -> list[Recommendation]:
        """Return Daraz's recommendations for a product.

        Recommendations are a nested field of the product payload -- Daraz
        renders the carousel on the same page. No second Firecrawl call is
        made (see Specification.md Section 15).

        Args:
            product_id: Daraz product identifier.

        Returns:
            The recommendation list, possibly empty.

        Raises:
            Same exceptions as ``get_product``.
        """
        product = await self.get_product(product_id)
        logger.info(
            "RECOMMENDATIONS_EXTRACTED",
            extra={
                "ctx": {
                    "product_id": product_id,
                    "count": len(product.recommendations),
                }
            },
        )
        return list(product.recommendations)

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    @staticmethod
    def _validate_product_id(product_id: str) -> str:
        """Normalise and validate a Daraz product ID.

        Args:
            product_id: Raw input from the path parameter.

        Returns:
            The stripped, validated product ID.

        Raises:
            InvalidRequestError: When the ID is empty or does not match the
                Daraz product ID format.
        """
        if not isinstance(product_id, str):
            raise InvalidRequestError(
                "product_id must be a string.",
                context={"type": type(product_id).__name__},
            )
        normalized = product_id.strip()
        if not normalized:
            raise InvalidRequestError("product_id must not be empty.")
        if not _PRODUCT_ID_PATTERN.match(normalized):
            raise InvalidRequestError(
                f"Invalid Daraz product ID: {normalized!r}. "
                "Expected format: 'i' followed by digits (e.g. 'i1959941878').",
                context={"product_id": normalized},
            )
        return normalized

# ---------------------------------------------------------------------- #
# Normalisation helpers
# ---------------------------------------------------------------------- #
def _normalise_product_id(
    product: ProductDetails,
    *,
    requested_id: str,
) -> ProductDetails:
    """Repair common LLM extraction bugs on the product id field.

    The LLM occasionally strips the leading "i" prefix from a Daraz
    identifier -- "i927677133" becomes "927677133". The requested id (from
    the URL path) is authoritative, so it is used as the source of truth
    whenever the payload disagrees in a way that looks like this specific
    bug. Anything else (a genuinely different id) is left alone so the
    caller can see what the LLM returned.

    Args:
        product: The validated ``ProductDetails`` instance.
        requested_id: The id the caller asked for (already validated).

    Returns:
        The product, with a normalised id when the prefix repair applied.
    """
    if product.id == requested_id:
        return product

    # Prefix-repair case: LLM returned a bare numeric id.
    if _BARE_NUMERIC_ID_PATTERN.match(product.id):
        logger.warning(
            "PRODUCT_ID_MISSING_PREFIX",
            extra={
                "ctx": {
                    "requested_id": requested_id,
                    "llm_id": product.id,
                }
            },
        )
        return product.model_copy(update={"id": f"i{product.id}"})

    # Anything else is a genuine mismatch worth surfacing.
    logger.warning(
        "PRODUCT_ID_MISMATCH",
        extra={
            "ctx": {
                "requested_id": requested_id,
                "llm_id": product.id,
            }
        },
    )
    return product

def _first_error_summary(exc: ValidationError) -> str:
    """Return a one-line summary of the first Pydantic error.

    Args:
        exc: A ``pydantic.ValidationError``.

    Returns:
        A short string like ``"price: Input should be >= 0"``.
    """
    errors = exc.errors()
    if not errors:
        return "unknown"
    first = errors[0]
    loc = ".".join(str(part) for part in first.get("loc", ()))
    msg = str(first.get("msg", "validation error"))
    return f"{loc}: {msg}"

# ---------------------------------------------------------------------- #
# Module-level convenience wrapper
# ---------------------------------------------------------------------- #
_default_service: ProductService | None = None

def get_product_service(store: ScrapeStore | None = None) -> ProductService:
    """Return a lazily-constructed, process-wide ``ProductService``.

    Mirrors the pattern used by ``search_service.get_search_service``.
    Tests should instantiate ``ProductService`` directly with an injected
    scraper rather than using this singleton.

    The first call wins the ``store`` argument. Startup warms the
    singleton with the app's store.

    Args:
        store: Optional scrape store. Only used on the first call.

    Returns:
        The shared ``ProductService`` instance.
    """
    global _default_service
    if _default_service is None:
        _default_service = ProductService(store=store)
    return _default_service

__all__ = ["ProductService", "get_product_service"]
