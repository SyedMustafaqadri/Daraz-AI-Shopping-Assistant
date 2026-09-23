"""Concrete Daraz scraper built on top of :class:`FirecrawlAdapter`.

Responsibilities:

    1. Build Daraz-specific URLs (search and product pages). This is the
       ONLY place in the codebase that knows Daraz's URL syntax.
    2. Pick the correct Firecrawl extraction mode per page type (see
       ADR-001): Markdown for search pages, structured JSON for product
       pages.
    3. Delegate the actual HTTP call to :class:`FirecrawlAdapter`.
    4. Return raw content to the caller -- no parsing, no validation.

Rendering options (why product pages pass them):

    Daraz product pages render several sections (description,
    specifications and reviews) lazily after the initial
    document load. A default Firecrawl scrape returns only the
    above-the-fold content plus a lot of navigation chrome. Empirically,
    a default Markdown scrape of a Daraz product page contains none of
    the section headings -- see ``scripts/diagnose_product_page.py``.

    The fix is two scrape options, applied only to product pages:

    - ``wait_for``: lets the lazily-rendered sections appear.
    - ``only_main_content=False``: stops Firecrawl from classifying those
      sections as non-main and dropping them.

    Search-result pages render eagerly and do not need either option.

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
# Product-page rendering options
# ---------------------------------------------------------------------- #
#: Milliseconds to wait after page load before snapshotting. Daraz's
#: below-the-fold sections (description, specifications, and reviews)
#: render lazily. Five seconds is generous enough for a
#: cold render on a slow connection, and short enough that the request
#: still feels interactive.
_PRODUCT_WAIT_FOR_MS: int = 5000

#: Firecrawl's ``only_main_content`` heuristic classifies navigation and
#: sidebars correctly but is too aggressive on Daraz product pages -- it
#: drops the very sections we need. Disable it for product pages only.
_PRODUCT_ONLY_MAIN_CONTENT: bool = False

# ---------------------------------------------------------------------- #
# Prompt for LLM-driven product extraction (ADR-001)
# ---------------------------------------------------------------------- #
# The prompt walks the page section-by-section, mirroring how a human
# would read it top to bottom. It complements the rendering options
# above: the options ensure the sections appear in the snapshot, the
# prompt tells the LLM where to look for them and how to handle
# frequently-observed extraction quirks (duplicate paragraphs,
# single-variant lists, bare-numeric ids).
_PRODUCT_EXTRACTION_PROMPT: str = (
    "You are extracting structured product data from a single Daraz.pk "
    "product detail page. Read the page from top to bottom and produce a "
    "JSON object matching the provided schema.\n"
    "\n"
    "Where each field lives on a Daraz product page:\n"
    "- id, title, url, image, price, original_price, discount_percentage, "
    "currency: the top block next to the main product image.\n"
    "- sold_count, rating, rating_count: just below the title. rating is "
    "sometimes rendered as an image whose alt text reads '<N> out of 5 "
    "stars' -- if you can read that value, use it; otherwise omit rating.\n"
    "- availability: next to the 'Add to Cart' / 'Buy Now' buttons "
    "(e.g. 'In Stock').\n"
    "- location: the seller location line, e.g. 'Sindh, Karachi - "
    "Gulshan-e-Iqbal, Block 15'.\n"
    "- variants: the colour / size / bundle chip selector. If ANY chip is "
    "rendered, extract every chip as a separate variant entry. A single "
    "rendered variant is still a valid variant list -- do not return an "
    "empty array when the page shows a variant name.\n"
    "- seller: the 'Sold by' or seller-info block. seller.name and "
    "seller.positive_rate (a number 0-100) are usually shown there.\n"
    "- shipping: the delivery / shipping block. shipping.fee is a number "
    "in PKR, shipping.free_shipping is true only when the page explicitly "
    "says 'Free Shipping', shipping.estimated_delivery is the human string "
    "like 'Guaranteed by 21-24 Sep'.\n"
    "- description: the 'Product Description' or 'Product details' "
    "section, usually below the buy box. It may be collapsed behind a "
    "'Read more' link. Extract the full text, not just the title. "
    "Extract the description exactly once -- if the same paragraph or "
    "sentence appears multiple times on the page, include it only once "
    "in the output.\n"
    "- specifications: the specs table below the description. Return it "
    "as a flat object mapping label to string value, for example "
    "{'Brand': 'GTS', 'Model': '1550'}.\n"
    "- reviews: the 'Ratings & Reviews' section. Each review has a star "
    "rating (number 0-5), comment text, author name, and date string. "
    "Extract every review visible on the page.\n"
    "\n"
    "Numeric rules (strict):\n"
    "- price, original_price, shipping.fee: bare numbers in PKR. Write "
    "579, not 'Rs. 579' and not '579 PKR'.\n"
    "- discount_percentage: integer without the percent sign.\n"
    "- coins_save, sold_count, rating_count: integers.\n"
    "- rating, seller.rating: numbers between 0 and 5.\n"
    "- seller.positive_rate: number between 0 and 100.\n"
    "- currency is always 'PKR'.\n"
    "\n"
    "Identifier rules (strict):\n"
    "- id is the full Daraz identifier including the leading 'i' prefix. "
    "It is taken from the product URL /products/<id>.html, so the prefix "
    "is always present. Example: 'i927677133', never '927677133'.\n"
    "\n"
    "Completeness rules:\n"
    "- Extract every section above that is present on the page. Do not "
    "skip a section because it appears below the fold.\n"
    "- If a section is genuinely not rendered on the page, omit that "
    "field entirely -- do not include it with an empty string, empty "
    "object, or empty array. The schema's defaults will fill it in.\n"
    "- Never guess, never invent values that are not visible on the page.\n"
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
        base_url: Override for ``settings.daraz_base_url``. For tests.
        search_path: Override for ``settings.daraz_search_path``.

    Returns:
        A fully-qualified Daraz search URL. Spaces in the query are
        encoded as ``%20`` (Daraz accepts both ``%20`` and ``+``; we
        match Daraz's own rendered URLs).

    Example:
        >>> build_search_url("gaming mouse", max_price=800)
        'https://www.daraz.pk/catalog/?q=gaming%20mouse&price=-800'
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

    Daraz renders product URLs as ``/products/{slug}-{id}.html``. The
    slug is optional -- Daraz redirects ``/products/{id}.html`` to the
    canonical URL -- so we construct the slug-less form.

    Args:
        product_id: Daraz product identifier (e.g., ``"i1959941878"``).
        base_url: Override for ``settings.daraz_base_url``. For tests.

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

    # Daraz expects integer values in the price parameter; fractional
    # bounds are truncated (e.g., 800.50 -> 800). Matches the UI.
    lo = str(int(min_price)) if min_price is not None else ""
    hi = str(int(max_price)) if max_price is not None else ""
    return f"{lo}-{hi}"

# ---------------------------------------------------------------------- #
# Concrete scraper
# ---------------------------------------------------------------------- #
class FirecrawlDarazScraper(DarazScraper):
    """Daraz scraper implemented on top of :class:`FirecrawlAdapter`.

    The adapter is injected (or lazily defaulted) so that tests can
    supply a mock and so that future implementations can swap the
    transport without touching this class's logic.

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

        Search pages render eagerly and do not need the rendering
        options that product pages use. This method deliberately passes
        neither ``wait_for_ms`` nor ``only_main_content``.

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
    async def fetch_product_payload(
        self,
        product_id: str,
        *,
        max_age_ms: int | None = None,
    ) -> dict[str, Any]:
        """Fetch a structured product payload from a Daraz product page.

        The schema is derived from
        ``ProductDetails.model_json_schema()`` at call time so that any
        change to the Pydantic model automatically propagates to the LLM
        prompt without a second edit.

        Applies product-specific rendering options (``wait_for`` and
        ``only_main_content=False``) -- see the module docstring for why.

        Args:
            product_id: Daraz product identifier (e.g. ``"i1959941878"``).
            max_age_ms: Maximum age, in milliseconds, of a reused
                Firecrawl cache entry. ``None`` (the default) lets
                Firecrawl choose its own window per domain -- this is the
                recommended setting for the MVP. Pass ``0`` to force a
                live scrape when a stale read would cause a wrong
                decision.

        Returns:
            A dict shaped like the ``ProductDetails`` schema. Untrusted
            -- the caller must validate it.

        Raises:
            ScraperError: On any upstream failure.
        """
        # Import here to avoid a module-level cycle: models do not
        # import scrapers, but keeping this local makes the dependency
        # explicit.
        from daraz_ai_shopping_assistant.models.product import ProductDetails

        url = build_product_url(product_id)
        schema = ProductDetails.model_json_schema()

        logger.info(
            "DARAZ_PRODUCT_FETCH",
            extra={
                "ctx": {
                    "product_id": product_id,
                    "url": url,
                    "max_age_ms": max_age_ms,
                    "wait_for_ms": _PRODUCT_WAIT_FOR_MS,
                    "only_main_content": _PRODUCT_ONLY_MAIN_CONTENT,
                }
            },
        )

        payload = await self._adapter.scrape_json(
            url,
            schema=schema,
            prompt=_PRODUCT_EXTRACTION_PROMPT,
            max_age_ms=max_age_ms,
            wait_for_ms=_PRODUCT_WAIT_FOR_MS,
            only_main_content=_PRODUCT_ONLY_MAIN_CONTENT,
        )

        # Diagnostic: record the shape of what the LLM returned, without
        # logging the actual product content. This is what tells us
        # whether a "missing" section is genuinely absent from the
        # payload or was returned as an empty value by the LLM.
        logger.info(
            "DARAZ_PRODUCT_PAYLOAD_SHAPE",
            extra={
                "ctx": {
                    "product_id": product_id,
                    "keys": sorted(payload.keys()),
                    "description_len": len(payload.get("description") or ""),
                    "specifications_count": len(
                        payload.get("specifications") or {}
                    ),
                    "variants_count": len(payload.get("variants") or []),
                    "reviews_count": len(payload.get("reviews") or []),
                }
            },
        )
        return payload

__all__ = [
    "FirecrawlDarazScraper",
    "build_product_url",
    "build_search_url",
]
