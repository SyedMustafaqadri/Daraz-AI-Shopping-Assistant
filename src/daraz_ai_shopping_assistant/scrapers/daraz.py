"""Concrete Daraz scraper built on top of :class:`FirecrawlAdapter`.

Responsibilities:

    1. Build Daraz-specific URLs (search and product pages). This is the
       ONLY place in the codebase that knows Daraz's URL syntax.
    2. Pick the correct Firecrawl extraction mode per page type (see
       ADR-001): Markdown for search pages, structured JSON for product
       pages.
    3. Delegate the actual HTTP call to :class:`FirecrawlAdapter`.
    4. Return raw content to the caller -- no parsing, no validation.

Search URL format (Daraz.pk):

    https://www.daraz.pk/catalog/?q=Gaming%20Mouse&price=-800&page=2

    - ``q``      : search query, URL-encoded with %20 for spaces.
    - ``price``  : ``-MAX`` for an upper bound, ``MIN-MAX`` for a range,
                   ``MIN-`` for a lower bound. Omitted when neither bound
                   is set.
    - ``page``   : 1-indexed. Omitted when page == 1 (Daraz's default).

Product URL format:

    https://www.daraz.pk/products/{product_id}.html
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote, urlencode

from daraz_ai_shopping_assistant.core.config import settings
from daraz_ai_shopping_assistant.core.logging import get_logger
from daraz_ai_shopping_assistant.scrapers.base import DarazScraper
from daraz_ai_shopping_assistant.scrapers.firecrawl import FirecrawlAdapter

logger = get_logger(__name__)

# ---------------------------------------------------------------------- #
# Prompt for LLM-driven product extraction (ADR-001)
# ---------------------------------------------------------------------- #
_PRODUCT_EXTRACTION_PROMPT: str = (
    "Extract structured product details from this Daraz.pk product page. "
    "Fill in every field you can determine from the page. For fields that "
    "are not present, omit them entirely -- never guess, never invent. "
    "Numeric fields must be numbers, not formatted strings: price is a bare "
    "number (579, not 'Rs. 579'), discount_percentage is an integer without "
    "the percent sign, coins_save and sold_count are integers. currency is "
    "always 'PKR'. specifications is a flat object mapping label to string "
    "value. seller.positive_rate is a number 0-100. variants, reviews, and "
    "recommendations are arrays; use an empty array when none are shown."
)

# ---------------------------------------------------------------------- #
# URL builders -- pure functions
# ---------------------------------------------------------------------- #
def build_search_url(
    query: str,
    *,
    min_price: float | None = None,
    max_price: float | None = None,
    page: int | None = None,
    base_url: str | None = None,
    search_path: str | None = None,
) -> str:
    """Build a Daraz search-results URL.

    Args:
        query: Free-text query (e.g., ``"gaming mouse"``).
        min_price: Lower price bound in PKR, or ``None``.
        max_price: Upper price bound in PKR, or ``None``.
        page: 1-indexed page number. ``None`` and ``1`` both omit the
            ``page`` parameter (Daraz's default is page 1).
        base_url: Override for ``settings.daraz_base_url``. Mainly for tests.
        search_path: Override for ``settings.daraz_search_path``.

    Returns:
        A fully-qualified Daraz search URL. Spaces in the query are encoded
        as ``%20`` (Daraz accepts both ``%20`` and ``+``; we match Daraz's
        own rendered URLs).

    Example:
        >>> build_search_url("gaming mouse", max_price=800)
        'https://www.daraz.pk/catalog/?q=gaming%20mouse&price=-800'
        >>> build_search_url("gaming mouse", min_price=100, max_price=800, page=2)
        'https://www.daraz.pk/catalog/?q=gaming%20mouse&price=100-800&page=2'
    """
    resolved_base = (base_url or settings.daraz_base_url).rstrip("/")
    resolved_path = search_path or settings.daraz_search_path

    params: dict[str, str] = {"q": query}

    price_param = _build_price_param(min_price, max_price)
    if price_param is not None:
        params["price"] = price_param

    if page is not None and page > 1:
        params["page"] = str(page)

    query_string = urlencode(params, quote_via=quote)
    return f"{resolved_base}{resolved_path}?{query_string}"

def build_product_url(
    product_id: str,
    *,
    base_url: str | None = None,
) -> str:
    """Build a canonical Daraz product-page URL from a product ID.

    Daraz renders product URLs as ``/products/{slug}-{id}.html``. The slug
    is optional -- Daraz redirects ``/products/{id}.html`` to the canonical
    URL -- so we construct the slug-less form.

    Args:
        product_id: Daraz product identifier (e.g., ``"i1959941878"``).
        base_url: Override for ``settings.daraz_base_url``. Mainly for tests.

    Returns:
        A fully-qualified Daraz product URL.

    Example:
        >>> build_product_url("i1959941878")
        'https://www.daraz.pk/products/i1959941878.html'
    """
    resolved_base = (base_url or settings.daraz_base_url).rstrip("/")
    return f"{resolved_base}/products/{product_id}.html"

def _build_price_param(
    min_price: float | None,
    max_price: float | None,
) -> str | None:
    """Render the ``price`` query parameter used by Daraz.

    Daraz's price filter syntax:

        - ``-MAX``       -- upper bound only.
        - ``MIN-MAX``    -- both bounds.
        - ``MIN-``       -- lower bound only.

    Args:
        min_price: Lower bound in PKR, or ``None``.
        max_price: Upper bound in PKR, or ``None``.

    Returns:
        The price parameter value, or ``None`` when no bound is set.
    """
    if min_price is None and max_price is None:
        return None

    # Daraz expects integer values in the price parameter; fractional bounds
    # are truncated (e.g., 800.50 -> 800). This matches the UI behaviour.
    lo = str(int(min_price)) if min_price is not None else ""
    hi = str(int(max_price)) if max_price is not None else ""
    return f"{lo}-{hi}"

# ---------------------------------------------------------------------- #
# Concrete scraper
# ---------------------------------------------------------------------- #
class FirecrawlDarazScraper(DarazScraper):
    """Daraz scraper implemented on top of :class:`FirecrawlAdapter`.

    The adapter is injected (or lazily defaulted) so that tests can supply
    a mock and so that future implementations can swap the transport
    without touching this class's logic.

    Attributes:
        _adapter: The :class:`FirecrawlAdapter` used for HTTP fetches.
    """

    def __init__(self, adapter: FirecrawlAdapter | None = None) -> None:
        """Initialise the scraper.

        Args:
            adapter: Optional adapter to use. When ``None``, a default
                adapter reading ``settings.firecrawl_api_key`` is created.
        """
        self._adapter: FirecrawlAdapter = adapter or FirecrawlAdapter()

    # ------------------------------------------------------------------ #
    # Search pages -- Markdown path (ADR-001)
    # ------------------------------------------------------------------ #
    async def fetch_search_markdown(
        self,
        query: str,
        *,
        min_price: float | None = None,
        max_price: float | None = None,
        page: int = 1,
    ) -> str:
        """Fetch the raw Markdown of a Daraz search-results page.

        Args:
            query: Free-text search query.
            min_price: Lower bound in PKR, or ``None``.
            max_price: Upper bound in PKR, or ``None``.
            page: 1-indexed page number.

        Returns:
            The raw Markdown as returned by Firecrawl.

        Raises:
            ScraperError: On any upstream failure.
        """
        url = build_search_url(
            query,
            min_price=min_price,
            max_price=max_price,
            page=page,
        )
        logger.info(
            "DARAZ_SEARCH_FETCH",
            extra={
                "ctx": {
                    "query": query,
                    "min_price": min_price,
                    "max_price": max_price,
                    "page": page,
                    "url": url,
                }
            },
        )
        return await self._adapter.scrape(url)

    # ------------------------------------------------------------------ #
    # Product pages -- structured extraction path (ADR-001)
    # ------------------------------------------------------------------ #
    async def fetch_product_payload(self, product_id: str) -> dict[str, Any]:
        """Fetch a structured product payload from a Daraz product page.

        The schema is derived from ``ProductDetails.model_json_schema()``
        at call time so that any change to the Pydantic model automatically
        propagates to the LLM prompt without a second edit.

        Args:
            product_id: Daraz product identifier (e.g., ``"i1959941878"``).

        Returns:
            A dict shaped like the ``ProductDetails`` schema. Untrusted --
            the caller must validate it.

        Raises:
            ScraperError: On any upstream failure.
        """
        # Import here to avoid a module-level cycle: models do not import
        # scrapers, but keeping this local makes the dependency explicit.
        from daraz_ai_shopping_assistant.models.product import ProductDetails

        url = build_product_url(product_id)
        schema = ProductDetails.model_json_schema()

        logger.info(
            "DARAZ_PRODUCT_FETCH",
            extra={"ctx": {"product_id": product_id, "url": url}},
        )

        return await self._adapter.scrape_json(
            url,
            schema=schema,
            prompt=_PRODUCT_EXTRACTION_PROMPT,
        )

__all__ = [
    "FirecrawlDarazScraper",
    "build_product_url",
    "build_search_url",
]
