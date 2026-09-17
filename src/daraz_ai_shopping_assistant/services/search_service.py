"""Search orchestration service.

This is where the pieces meet:

    Scraper.fetch_search_markdown(...)   -> raw Markdown
    parse_search_results(markdown)       -> untrusted dicts
    Product.model_validate(dict)         -> trusted Product models
    SearchResult(...)                    -> response envelope

The service is the ONLY layer that:

    - attaches a timestamp to a response,
    - constructs the filter and pagination envelopes,
    - decides what to do when a product fails validation (drop + log),
    - emits the SEARCH_* lifecycle events,
    - reads from and writes to the local scrape store.

Everything above this layer (API routes, LangGraph tools) calls the service.
Everything below it (scraper, parser, adapter) knows nothing about HTTP or
response envelopes.
"""

from __future__ import annotations

import time
from typing import Any

from pydantic import ValidationError

from daraz_ai_shopping_assistant.core.config import settings
from daraz_ai_shopping_assistant.core.logging import get_logger
from daraz_ai_shopping_assistant.models.product import Product
from daraz_ai_shopping_assistant.models.search import (
    Pagination,
    SearchFilters,
    SearchResult,
)
from daraz_ai_shopping_assistant.parsers.search_parser import parse_search_results
from daraz_ai_shopping_assistant.scrapers.base import DarazScraper
from daraz_ai_shopping_assistant.scrapers.daraz import FirecrawlDarazScraper
from daraz_ai_shopping_assistant.storage.json_store import ScrapeStore
from daraz_ai_shopping_assistant.utils.datetime import pkt_now

logger = get_logger(__name__)

class SearchService:
    """Orchestrates a Daraz product search end-to-end.

    Attributes:
        _scraper: The :class:`DarazScraper` used to fetch raw content.
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
                :class:`FirecrawlDarazScraper` is created. Tests inject a
                mock here to avoid hitting the network.
            store: Optional scrape store for local persistence. When
                ``None``, the service scrapes on every call.
        """
        self._scraper: DarazScraper = scraper if scraper is not None else FirecrawlDarazScraper()
        self._store: ScrapeStore | None = store

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    async def search(
        self,
        query: str,
        *,
        min_price: float | None = None,
        max_price: float | None = None,
        page: int = 1,
    ) -> SearchResult:
        """Execute a Daraz product search and return a validated response.

        Args:
            query: Free-text search query (e.g., ``"gaming mouse"``).
            min_price: Lower bound in PKR, or ``None`` for unbounded.
            max_price: Upper bound in PKR, or ``None`` for unbounded.
            page: 1-indexed page number. Defaults to 1.

        Returns:
            A fully-validated :class:`SearchResult`.

        Raises:
            InvalidRequestError: When ``query`` is empty or filters are
                contradictory.
            ScraperError: When the upstream fetch fails.
        """
        # Import locally to keep the module's import surface small and to
        # avoid pulling core.exceptions into every import path.
        from daraz_ai_shopping_assistant.core.exceptions import InvalidRequestError

        normalized_query = query.strip()
        if not normalized_query:
            raise InvalidRequestError("Search query must not be empty.")

        # Constructing SearchFilters validates the price range eagerly so
        # that bad input fails before we pay for a network call.
        filters = SearchFilters(min_price=min_price, max_price=max_price)

        cache_key = _build_search_key(
            normalized_query,
            min_price=min_price,
            max_price=max_price,
            page=page,
        )

        # 1. Store lookup. A hit short-circuits the whole scrape path.
        if self._store is not None:
            cached = self._store.get(cache_key)
            if cached is not None:
                logger.info(
                    "SEARCH_STORE_HIT",
                    extra={"ctx": {"query": normalized_query, "page": page, "key": cache_key}},
                )
                return SearchResult.model_validate(cached)

        started_at = time.monotonic()
        logger.info(
            "SEARCH_STARTED",
            extra={
                "ctx": {
                    "query": normalized_query,
                    "min_price": min_price,
                    "max_price": max_price,
                    "page": page,
                }
            },
        )

        # 2. Fetch raw Markdown from Daraz via the scraper.
        markdown = await self._scraper.fetch_search_markdown(
            normalized_query,
            min_price=min_price,
            max_price=max_price,
            page=page,
        )

        # 3. Parse into untrusted dicts.
        parsed = parse_search_results(markdown, current_page=page)

        raw_products = parsed.get("products", [])
        assert isinstance(raw_products, list), "parser contract violated: products is not a list"
        logger.info(
            "PRODUCTS_EXTRACTED",
            extra={
                "ctx": {
                    "query": normalized_query,
                    "page": page,
                    "raw_count": len(raw_products),
                }
            },
        )

        # 4. Validate each product; drop invalid ones.
        valid_products = self._validate_products(raw_products, query=normalized_query)

        # 5. Assemble the response envelope.
        pagination = _coerce_pagination(parsed.get("pagination"))
        result = SearchResult(
            search_query=normalized_query,
            filters=filters,
            total_items_found=_coerce_optional_int(parsed.get("total_items_found")),
            scraped_at=pkt_now(),
            products=valid_products,
            pagination=pagination,
        )

        duration_ms = int((time.monotonic() - started_at) * 1000)
        logger.info(
            "SEARCH_COMPLETED",
            extra={
                "ctx": {
                    "query": normalized_query,
                    "page": page,
                    "valid_count": len(valid_products),
                    "duration_ms": duration_ms,
                }
            },
        )

        if not valid_products:
            logger.warning(
                "SEARCH_EMPTY",
                extra={
                    "ctx": {
                        "query": normalized_query,
                        "page": page,
                        "raw_count": len(raw_products),
                    }
                },
            )

        # 6. Persist for future requests. Only successful, non-empty pages
        #    are stored -- a transient empty page should not become the
        #    cached answer for the next six hours.
        if self._store is not None and valid_products:
            await self._store.set(
                cache_key,
                kind="search",
                payload=result.model_dump(mode="json"),
                ttl_seconds=settings.scrape_store_search_ttl_seconds,
            )

        return result

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    @staticmethod
    def _validate_products(
        raw_products: list[dict[str, Any]],
        *,
        query: str,
    ) -> list[Product]:
        """Validate raw parser output into ``Product`` models.

        Invalid entries are dropped (not raised) and logged as
        ``PRODUCT_VALIDATION_FAILED`` with enough context to debug the
        underlying parser or LLM output without re-running the scrape.

        Args:
            raw_products: Untrusted dicts from the parser.
            query: The originating query, for logging context.

        Returns:
            A list of validated :class:`Product` instances.
        """
        valid: list[Product] = []
        for index, raw in enumerate(raw_products):
            try:
                valid.append(Product.model_validate(raw))
            except ValidationError as exc:
                logger.warning(
                    "PRODUCT_VALIDATION_FAILED",
                    extra={
                        "ctx": {
                            "query": query,
                            "index": index,
                            "product_id": raw.get("id") if isinstance(raw, dict) else None,
                            "error_count": len(exc.errors()),
                            "first_error": _first_error_summary(exc),
                        }
                    },
                )
        return valid

# ---------------------------------------------------------------------- #
# Cache-key construction
# ---------------------------------------------------------------------- #
def _build_search_key(
    query: str,
    *,
    min_price: float | None,
    max_price: float | None,
    page: int,
) -> str:
    """Build the canonical cache key for a search request.

    The key must be deterministic across requests. ``None`` and the
    float-cast string of a number must not collide, so a sentinel of
    ``"~"`` is used for missing bounds.

    Args:
        query: Normalised (stripped) search query.
        min_price: Lower bound, or ``None``.
        max_price: Upper bound, or ``None``.
        page: 1-indexed page number.

    Returns:
        A string of the form ``"search:{query}|{min}|{max}|{page}"``.
    """
    lo = "~" if min_price is None else str(min_price)
    hi = "~" if max_price is None else str(max_price)
    return f"search:{query}|{lo}|{hi}|{page}"

# ---------------------------------------------------------------------- #
# Coercion helpers
# ---------------------------------------------------------------------- #
def _coerce_optional_int(value: Any) -> int | None:
    """Coerce a parser-supplied value to ``int | None``.

    The parser is written to return the correct type, but a malformed
    Markdown page could conceivably produce something unexpected. The
    service is the last line of defence before Pydantic.

    Args:
        value: Value from the parser output.

    Returns:
        The integer, or ``None`` if the input was ``None`` or not
        coercible.
    """
    if value is None:
        return None
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return None

def _coerce_pagination(value: Any) -> Pagination:
    """Coerce a parser-supplied pagination dict into a Pagination model.

    Args:
        value: Value from the parser output. Expected to be a dict.

    Returns:
        A :class:`Pagination` instance. Falls back to defaults when the
        input is missing or malformed.
    """
    if isinstance(value, dict):
        try:
            return Pagination(**value)
        except ValidationError:
            pass
    return Pagination()

def _first_error_summary(exc: ValidationError) -> str:
    """Return a one-line summary of the first Pydantic error.

    Args:
        exc: A :class:`pydantic.ValidationError`.

    Returns:
        A short string like ``"price: Input should be greater than or equal to 0"``.
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
_default_service: SearchService | None = None

def get_search_service(store: ScrapeStore | None = None) -> SearchService:
    """Return a lazily-constructed, process-wide :class:`SearchService`.

    The singleton exists so that the FastAPI route layer does not pay the
    cost of constructing a Firecrawl adapter per request. Tests should not
    use this -- they should instantiate :class:`SearchService` directly
    with an injected scraper.

    The first call wins the ``store`` argument. Subsequent calls with a
    different store are ignored, which keeps the singleton semantics
    simple. Startup warms the singleton with the app's store.

    Args:
        store: Optional scrape store. Only used on the first call.

    Returns:
        The shared :class:`SearchService` instance.
    """
    global _default_service
    if _default_service is None:
        _default_service = SearchService(store=store)
    return _default_service

async def search_products(
    query: str,
    *,
    min_price: float | None = None,
    max_price: float | None = None,
    page: int = 1,
) -> SearchResult:
    """Convenience wrapper around :meth:`SearchService.search`.

    Uses the process-wide default service. Intended for the API layer and
    for one-off scripts. Prefer the class directly when you need to inject
    a scraper.

    Args:
        query: Free-text search query.
        min_price: Lower bound in PKR, or ``None``.
        max_price: Upper bound in PKR, or ``None``.
        page: 1-indexed page number.

    Returns:
        A fully-validated :class:`SearchResult`.
    """
    return await get_search_service().search(
        query,
        min_price=min_price,
        max_price=max_price,
        page=page,
    )

__all__ = ["SearchService", "get_search_service", "search_products"]
