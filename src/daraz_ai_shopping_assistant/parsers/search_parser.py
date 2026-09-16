"""Search parser -- pure function: raw Daraz search Markdown -> structured dict.

Input:
    Raw Markdown scraped from https://www.daraz.pk/catalog/?q=...

Output:
    A dictionary shaped like SearchResult but with search_query, filters,
    and scraped_at omitted. Those are supplied by the service layer.

Design rules:
    - Pure function. No I/O, no network, no globals.
    - Missing fields are None -- never invented, never defaulted to zero.
    - Numeric fields are returned as int or float, never as formatted
      strings like "Rs. 579".
    - No Pydantic validation here. The parser returns a best-effort dict;
      the service layer runs Product.model_validate on each entry and
      discards the ones that fail.

Product card structure:
    Each product card is expected to contain at least a title line and a
    price line. Some cards also contain an image-anchor line (which Daraz
    renders for above-the-fold products but not always for lazy-loaded
    ones). Block splitting accounts for BOTH shapes:

        [![alt](image_url)](product_url)     <- image anchor (optional)
        [Title](product_url "Title")         <- title line (required)
        Rs. 579                              <- price (required)
        27% OffCoins save Rs. 29
        184 sold
        (40)
        Punjab

Adapting to layout changes:
    Every Daraz-specific regex lives at the top of this module. If Daraz
    changes its Markdown structure, adjust those constants.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------- #
# Regex patterns -- the ONLY place Daraz's Markdown structure is encoded.
# ---------------------------------------------------------------------- #

# Header: "11084 items found for "Gaming Mouse""
_ITEMS_FOUND_RE: re.Pattern[str] = re.compile(
    r"(?P<count>\d[\d,]*)\s+items?\s+found",
    re.IGNORECASE,
)

# Pagination links: "- [2](https://www.daraz.pk/catalog/?...&page=2)"
_PAGINATION_LINK_RE: re.Pattern[str] = re.compile(
    r"\[(?P<page>\d+)\]\(https?://www\.daraz\.pk/catalog/\?[^)]*\)"
)

# Image line -- matches when it sits alone on its own line:
#   [![alt](image_url)](product_url)
_IMAGE_LINE_RE: re.Pattern[str] = re.compile(
    r"^\[!\[(?P<alt>[^\]]*)\]\((?P<image>https?://[^)\s]+)\)\]"
    r"\((?P<url>https?://www\.daraz\.pk/products/[^)\s]+)\)\s*$",
    re.MULTILINE,
)

# Title line -- matches when it sits alone on its own line and does NOT
# start with the image-anchor prefix:
#   [Title](product_url "Title")
_TITLE_LINE_RE: re.Pattern[str] = re.compile(
    r"^(?!\[!\[)\[(?P<title>[^\]]+)\]"
    r"\((?P<url>https?://www\.daraz\.pk/products/[^)\s]+)"
    r"(?:\s+\"[^\"]*\")?\)\s*$",
    re.MULTILINE,
)

# Product ID embedded in a Daraz product URL: "...-i1959941878.html"
_PRODUCT_ID_RE: re.Pattern[str] = re.compile(r"-(?P<id>i\d+)\.html")

# Price line -- anchored so it does NOT accidentally match "Coins save Rs. 29"
# from the discount line:
#   Rs. 579
_PRICE_RE: re.Pattern[str] = re.compile(
    r"^Rs\.\s*(?P<price>\d[\d,]*)\s*$",
    re.MULTILINE,
)

# Discount -- anchored at line start; matches "27% Off" with or without a
# trailing "Coins save Rs. ..." on the same line:
_DISCOUNT_RE: re.Pattern[str] = re.compile(
    r"^(?P<discount>\d+)\s*%\s*Off",
    re.IGNORECASE | re.MULTILINE,
)

# Coins-save amount: "Coins save Rs. 29"
_COINS_RE: re.Pattern[str] = re.compile(
    r"Coins\s+save\s+Rs\.\s*(?P<coins>\d[\d,]*)",
    re.IGNORECASE,
)

# Sold count: "184 sold", "8.1K sold", "1,234 sold".
# Daraz abbreviates large counts with K (thousands) and M (millions).
_SOLD_RE: re.Pattern[str] = re.compile(
    r"(?P<value>\d[\d,]*(?:\.\d+)?\s*[KkMm]?)\s+sold",
    re.IGNORECASE,
)

# Rating count -- a bare number in parentheses on its own line: "(40)"
_RATING_COUNT_RE: re.Pattern[str] = re.compile(
    r"^\((?P<count>\d[\d,]*)\)\s*$",
    re.MULTILINE,
)

# Locations that Daraz.pk renders. Ordered longest-first so that
# "Khyber Pakhtunkhwa" is matched before any shorter prefix.
_LOCATIONS: tuple[str, ...] = (
    "Khyber Pakhtunkhwa",
    "Azad Kashmir",
    "Gilgit-Baltistan",
    "Balochistan",
    "Islamabad",
    "Punjab",
    "Sindh",
    "Overseas",
    "Local",
)
_LOCATION_RE: re.Pattern[str] = re.compile(
    r"\b(" + "|".join(re.escape(loc) for loc in _LOCATIONS) + r")\b"
)

# Detects the start of the pagination section so we can trim trailing
# categories/footer before parsing product blocks.
_PAGINATION_START_RE: re.Pattern[str] = re.compile(
    r"\n\s*-\s*\[\d+\]\(https?://www\.daraz\.pk/catalog/\?"
)

# ---------------------------------------------------------------------- #
# Numeric helpers
# ---------------------------------------------------------------------- #
def _to_int(value: str | None) -> int | None:
    """Convert a comma-formatted numeric string to int.

    Args:
        value: Raw digits possibly containing thousands separators (e.g.
            "1,299"), or None.

    Returns:
        The parsed integer, or None if the input was None.
    """
    if value is None:
        return None
    return int(value.replace(",", ""))

def _parse_short_int(raw: str | None) -> int | None:
    """Parse an abbreviated count like "8.1K" into an integer.

    Supports plain integers ("184"), comma-grouped integers ("1,234"), and
    abbreviated forms with a K (thousand) or M (million) suffix, case
    insensitive.

    Args:
        raw: Raw count as rendered by Daraz (e.g. "8.1K", "184", "1,234"),
            or None.

    Returns:
        The integer value, or None if the input was None.

    Raises:
        ValueError: If the string is non-empty but not a recognised count.
    """
    if raw is None:
        return None
    cleaned = raw.replace(",", "").strip()
    if not cleaned:
        raise ValueError("Empty numeric string passed to _parse_short_int")

    multiplier = 1
    suffix = cleaned[-1].upper()
    if suffix == "K":
        multiplier = 1_000
        cleaned = cleaned[:-1]
    elif suffix == "M":
        multiplier = 1_000_000
        cleaned = cleaned[:-1]

    return int(float(cleaned) * multiplier)

# ---------------------------------------------------------------------- #
# Field extractors
# ---------------------------------------------------------------------- #
def _extract_total_items(markdown: str) -> int | None:
    """Extract the "N items found" count from the page header.

    Args:
        markdown: Full Markdown of the search results page.

    Returns:
        The total item count, or None if the header is absent.
    """
    match = _ITEMS_FOUND_RE.search(markdown)
    if not match:
        return None
    return _to_int(match.group("count"))

def _extract_pagination(
    markdown: str,
    current_page: int | None,
    items_per_page: int = 40,
) -> dict[str, int | None]:
    """Extract pagination metadata from the bottom-of-page page links.

    Args:
        markdown: Full Markdown of the search results page.
        current_page: The 1-indexed page number the caller requested.
        items_per_page: Number of items Daraz renders per page.

    Returns:
        A dict with keys current_page, total_pages, and items_per_page.
    """
    page_numbers = [
        int(m.group("page")) for m in _PAGINATION_LINK_RE.finditer(markdown)
    ]
    total_pages = max(page_numbers) if page_numbers else None

    return {
        "current_page": current_page if current_page is not None else 1,
        "total_pages": total_pages,
        "items_per_page": items_per_page,
    }

def _extract_location(block: str) -> str | None:
    """Extract the seller/product location token from a product block.

    Args:
        block: Markdown for a single product card.

    Returns:
        The canonical location string, or None if absent.
    """
    match = _LOCATION_RE.search(block)
    return match.group(1) if match else None

def _parse_product_block(block: str) -> dict[str, object] | None:
    """Parse a single product block into a raw dict.

    A block is considered a valid product when it contains a title line
    (which carries the canonical URL) and a price line. The image anchor
    is optional -- Daraz does not render it for lazy-loaded products.

    Args:
        block: Markdown for one product card.

    Returns:
        A dict of extracted fields, or None if the block is not a valid
        product (missing URL, ID, or price).
    """
    title_match = _TITLE_LINE_RE.search(block)
    if not title_match:
        return None

    url = title_match.group("url")
    id_match = _PRODUCT_ID_RE.search(url)
    if not id_match:
        return None

    price_match = _PRICE_RE.search(block)
    if not price_match:
        return None

    image_match = _IMAGE_LINE_RE.search(block)
    discount_match = _DISCOUNT_RE.search(block)
    coins_match = _COINS_RE.search(block)
    sold_match = _SOLD_RE.search(block)
    rating_count_match = _RATING_COUNT_RE.search(block)

    sold_count: int | None = None
    if sold_match:
        try:
            sold_count = _parse_short_int(sold_match.group("value"))
        except ValueError:
            sold_count = None

    return {
        "id": id_match.group("id"),
        "title": title_match.group("title").strip(),
        "url": url,
        "image": image_match.group("image") if image_match else None,
        "price": float(_to_int(price_match.group("price")) or 0),
        "currency": "PKR",
        "original_price": None,  # not rendered on search cards
        "discount_percentage": (
            _to_int(discount_match.group("discount")) if discount_match else None
        ),
        "coins_save": _to_int(coins_match.group("coins")) if coins_match else None,
        "sold_count": sold_count,
        "rating": None,  # search cards omit the numeric star rating
        "rating_count": (
            _to_int(rating_count_match.group("count"))
            if rating_count_match
            else None
        ),
        "location": _extract_location(block),
    }

def _strip_trailing_sections(markdown: str) -> str:
    """Trim everything after the product results section.

    The pagination links, category listing, and page footer follow the
    product cards. Removing them before splitting keeps the final product
    block from absorbing unrelated content.

    Args:
        markdown: Full Markdown of the search results page.

    Returns:
        The Markdown truncated at (but not including) the pagination section.
    """
    match = _PAGINATION_START_RE.search(markdown)
    if match:
        return markdown[: match.start()]
    return markdown

def _split_into_product_blocks(markdown: str) -> list[str]:
    """Split the search-results Markdown into one chunk per product.

    Both image anchors and title lines are treated as product boundaries.
    This is essential because Daraz does not always render the image
    anchor for products below the fold (lazy loading), but the title line
    is present for every rendered product.

    Algorithm:
        1. Collect the position of every image-anchor line and every title
           line in the input.
        2. Deduplicate by product URL -- a product's "block start" is the
           first line (image or title) that references it.
        3. Slice the Markdown between consecutive block starts.

    Args:
        markdown: Trimmed Markdown of the search results section.

    Returns:
        A list of per-product Markdown chunks. Empty when no products are
        found.
    """
    occurrences: list[tuple[int, str]] = []
    for m in _IMAGE_LINE_RE.finditer(markdown):
        occurrences.append((m.start(), m.group("url")))
    for m in _TITLE_LINE_RE.finditer(markdown):
        occurrences.append((m.start(), m.group("url")))

    if not occurrences:
        return []

    # Stable sort by position so image anchors (earlier) precede title
    # lines (later) for the same URL.
    occurrences.sort(key=lambda pair: pair[0])

    seen: set[str] = set()
    boundaries: list[int] = []
    for position, url in occurrences:
        if url not in seen:
            seen.add(url)
            boundaries.append(position)

    blocks: list[str] = []
    for i, start in enumerate(boundaries):
        end = boundaries[i + 1] if i + 1 < len(boundaries) else len(markdown)
        blocks.append(markdown[start:end])
    return blocks

# ---------------------------------------------------------------------- #
# Public entry point
# ---------------------------------------------------------------------- #
def parse_search_results(
    markdown: str,
    *,
    current_page: int | None = None,
    items_per_page: int = 40,
) -> dict[str, object]:
    """Parse a Daraz search-results Markdown page into a structured dict.

    The returned dict is shaped like a SearchResult minus the fields that
    only the service layer knows: search_query, filters, and scraped_at.

    Args:
        markdown: Raw Markdown returned by Firecrawl for a Daraz search URL.
        current_page: The 1-indexed page number that was requested. Included
            verbatim in the output's pagination block. Defaults to 1.
        items_per_page: Number of items per page. Defaults to Daraz's 40.

    Returns:
        A dict with keys total_items_found, pagination, and products.
        Product dicts are best-effort and may fail Pydantic validation --
        the service layer filters those out.

    Example:
        >>> md = open("tests/fixtures/daraz_search_gaming_mouse.md").read()
        >>> parsed = parse_search_results(md, current_page=1)
        >>> isinstance(parsed["products"], list)
        True
    """
    total_items = _extract_total_items(markdown)
    pagination = _extract_pagination(
        markdown, current_page=current_page, items_per_page=items_per_page
    )

    trimmed = _strip_trailing_sections(markdown)
    blocks = _split_into_product_blocks(trimmed)

    products: list[dict[str, object]] = []
    for block in blocks:
        product = _parse_product_block(block)
        if product is not None:
            products.append(product)

    return {
        "total_items_found": total_items,
        "pagination": pagination,
        "products": products,
    }

__all__ = ["parse_search_results"]
