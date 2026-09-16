"""Unit tests for :mod:`daraz_ai_shopping_assistant.parsers.search_parser`.

The parser is a pure function, so these tests are fast and hermetic. Two
fixtures are used:

    1. SYNTHETIC_MARKDOWN -- a small, hand-written Markdown block embedded
       below that mirrors the real Daraz layout. All logic tests run
       against this and never touch the filesystem.
    2. tests/fixtures/daraz_search_gaming_mouse.md -- the real fixture.
       Tests using it are skipped if the file is missing or empty.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from daraz_ai_shopping_assistant.parsers.search_parser import (
    _extract_location,
    _extract_pagination,
    _extract_total_items,
    _parse_product_block,
    _parse_short_int,
    _split_into_product_blocks,
    _strip_trailing_sections,
    _to_int,
    parse_search_results,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "daraz_search_gaming_mouse.md"

def _load_real_fixture() -> str:
    """Return the real fixture text, or skip the test if unavailable.

    Returns:
        The fixture contents as a string.

    Raises:
        pytest.skip.Exception: When the file is missing or empty.
    """
    if not REAL_FIXTURE.exists():
        pytest.skip(
            f"Real fixture not found at {REAL_FIXTURE.relative_to(REPO_ROOT)}."
        )
    text = REAL_FIXTURE.read_text(encoding="utf-8")
    if not text.strip():
        pytest.skip(f"Real fixture at {REAL_FIXTURE.relative_to(REPO_ROOT)} is empty.")
    return text

SYNTHETIC_MARKDOWN = textwrap.dedent(
    """\
    # Gaming Mouse

    11084 items found for "Gaming Mouse"

    [![Test Product One](https://img.drz.lazcdn.com/static/pk/p/abc123.jpg_200x200q80.avif)](https://www.daraz.pk/products/slug-one-i1959941878.html)

    [Test Product One](https://www.daraz.pk/products/slug-one-i1959941878.html "Test Product One")

    Rs. 579

    27% OffCoins save Rs. 29

    184 sold

    (40)

    Punjab

    [![Test Product Two](https://img.drz.lazcdn.com/static/pk/p/def456.jpg_200x200q80.avif)](https://www.daraz.pk/products/slug-two-i1962924638.html)

    [Test Product Two](https://www.daraz.pk/products/slug-two-i1962924638.html "Test Product Two")

    Rs. 599

    34% OffCoins save Rs. 30

    251 sold

    (26)

    Sindh

    - [1](https://www.daraz.pk/catalog/?price=-800&q=Gaming%20Mouse)
    - [2](https://www.daraz.pk/catalog/?price=-800&q=Gaming%20Mouse&page=2)
    - [102](https://www.daraz.pk/catalog/?price=-800&q=Gaming%20Mouse&page=102)

    Category

    [TV Remote Controllers](https://www.daraz.pk/tv-remote-controllers/) [Wireless Earbuds](https://www.daraz.pk/wireless-earbuds/)
    """
)

# ---------------------------------------------------------------------- #
# _to_int
# ---------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("579", 579),
        ("1,299", 1299),
        ("11084", 11084),
        (None, None),
    ],
    ids=["plain", "with-commas", "five-digits", "none"],
)
def test_to_int(raw: str | None, expected: int | None) -> None:
    """Numeric strings parse correctly; None passes through."""
    assert _to_int(raw) == expected

# ---------------------------------------------------------------------- #
# _parse_short_int
# ---------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("184", 184),
        ("1,234", 1234),
        ("8.1K", 8100),
        ("8.1k", 8100),
        ("5.3K", 5300),
        ("16K", 16000),
        ("1.5M", 1_500_000),
        (None, None),
    ],
    ids=["plain", "commas", "K-upper", "K-lower", "5.3K", "integer-K", "M", "none"],
)
def test_parse_short_int(raw: str | None, expected: int | None) -> None:
    """Abbreviated counts (K/M) and plain integers parse correctly."""
    assert _parse_short_int(raw) == expected

def test_parse_short_int_rejects_empty() -> None:
    """An empty string is an error, not silently zero."""
    with pytest.raises(ValueError):
        _parse_short_int("")

# ---------------------------------------------------------------------- #
# _extract_total_items
# ---------------------------------------------------------------------- #
def test_extract_total_items_from_synthetic() -> None:
    """The 'N items found' header is parsed into an int."""
    assert _extract_total_items(SYNTHETIC_MARKDOWN) == 11084

def test_extract_total_items_missing_returns_none() -> None:
    """A page without the header returns None."""
    assert _extract_total_items("# Nothing here") is None

def test_extract_total_items_singular_phrase() -> None:
    """'1 item found' (singular) is handled."""
    md = '1 item found for "widget"'
    assert _extract_total_items(md) == 1

def test_extract_total_items_zero() -> None:
    """'0 items found' returns 0, not None."""
    md = '0 items found for "nothing"'
    assert _extract_total_items(md) == 0

# ---------------------------------------------------------------------- #
# _extract_pagination
# ---------------------------------------------------------------------- #
def test_extract_pagination_from_synthetic() -> None:
    """The largest page number in the pagination section is total_pages."""
    pagination = _extract_pagination(SYNTHETIC_MARKDOWN, current_page=1)
    assert pagination["current_page"] == 1
    assert pagination["total_pages"] == 102
    assert pagination["items_per_page"] == 40

def test_extract_pagination_defaults_current_page_to_one() -> None:
    """When current_page is None the output defaults to 1."""
    pagination = _extract_pagination(SYNTHETIC_MARKDOWN, current_page=None)
    assert pagination["current_page"] == 1

def test_extract_pagination_no_links() -> None:
    """A page without pagination links reports total_pages=None."""
    pagination = _extract_pagination("# empty", current_page=2)
    assert pagination["total_pages"] is None
    assert pagination["current_page"] == 2

# ---------------------------------------------------------------------- #
# _extract_location
# ---------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Some text\nPunjab\nmore text", "Punjab"),
        ("Seller from Sindh here", "Sindh"),
        ("Shipped from Khyber Pakhtunkhwa", "Khyber Pakhtunkhwa"),
        ("Ships from Azad Kashmir", "Azad Kashmir"),
        ("No location here", None),
    ],
    ids=["punjab", "sindh", "kpk", "azad-kashmir", "none"],
)
def test_extract_location(text: str, expected: str | None) -> None:
    """Known Daraz locations are recognised; unknown blocks return None."""
    assert _extract_location(text) == expected

# ---------------------------------------------------------------------- #
# _parse_product_block
# ---------------------------------------------------------------------- #
def test_parse_product_block_full() -> None:
    """A well-formed block yields every expected field."""
    block = textwrap.dedent(
        """\
        [![Alt Text](https://img.drz.lazcdn.com/static/pk/p/abc.jpg_200x200q80.avif)](https://www.daraz.pk/products/slug-i1959941878.html)

        [Real Title](https://www.daraz.pk/products/slug-i1959941878.html "Real Title")

        Rs. 579

        27% OffCoins save Rs. 29

        184 sold

        (40)

        Punjab
        """
    )
    product = _parse_product_block(block)
    assert product is not None
    assert product["id"] == "i1959941878"
    assert product["title"] == "Real Title"
    assert product["url"] == "https://www.daraz.pk/products/slug-i1959941878.html"
    assert product["image"] is not None
    assert product["image"].startswith("https://img.drz.lazcdn.com/")
    assert product["price"] == 579.0
    assert isinstance(product["price"], float)
    assert product["currency"] == "PKR"
    assert product["discount_percentage"] == 27
    assert product["coins_save"] == 29
    assert product["sold_count"] == 184
    assert product["rating"] is None
    assert product["rating_count"] == 40
    assert product["location"] == "Punjab"

def test_parse_product_block_without_image_anchor_succeeds() -> None:
    """A block with a title line and price but no image anchor is valid.

    This is the lazy-loading case: Daraz renders the title link for every
    product but only renders the image anchor for above-the-fold cards.
    """
    block = textwrap.dedent(
        """\
        [Lazy Loaded Mouse](https://www.daraz.pk/products/slug-i999.html "Lazy Loaded Mouse")

        Rs. 349

        5% OffCoins save Rs. 3

        12 sold

        (4)

        Sindh
        """
    )
    product = _parse_product_block(block)
    assert product is not None
    assert product["id"] == "i999"
    assert product["title"] == "Lazy Loaded Mouse"
    assert product["image"] is None
    assert product["price"] == 349.0
    assert product["sold_count"] == 12

def test_parse_product_block_handles_k_suffix_sold() -> None:
    """An abbreviated sold count like '8.1K sold' must be expanded."""
    block = textwrap.dedent(
        """\
        [![Alt](https://img.drz.lazcdn.com/x.jpg)](https://www.daraz.pk/products/slug-i319444203.html)

        [FunBug Mouse](https://www.daraz.pk/products/slug-i319444203.html "FunBug Mouse")

        Rs. 647

        74% OffCoins save Rs. 6

        8.1K sold

        (2369)

        Punjab
        """
    )
    product = _parse_product_block(block)
    assert product is not None
    assert product["sold_count"] == 8100
    assert product["rating_count"] == 2369

def test_parse_product_block_missing_title_returns_none() -> None:
    """A block with an image anchor but no title line is rejected."""
    block = textwrap.dedent(
        """\
        [![Alt](https://img.drz.lazcdn.com/x.jpg)](https://www.daraz.pk/products/slug-i1.html)

        Rs. 100
        """
    )
    assert _parse_product_block(block) is None

def test_parse_product_block_missing_price_returns_none() -> None:
    """A block without a price line is rejected."""
    block = textwrap.dedent(
        """\
        [![Alt](https://img.drz.lazcdn.com/x.jpg)](https://www.daraz.pk/products/slug-i1.html)

        [Title](https://www.daraz.pk/products/slug-i1.html "Title")

        5 sold
        """
    )
    assert _parse_product_block(block) is None

def test_parse_product_block_price_ignores_coins_line() -> None:
    """The anchored price regex must not match "Coins save Rs. X"."""
    block = textwrap.dedent(
        """\
        [![Alt](https://img.drz.lazcdn.com/x.jpg)](https://www.daraz.pk/products/slug-i1.html)

        [Title](https://www.daraz.pk/products/slug-i1.html "Title")

        Rs. 999

        Coins save Rs. 15
        """
    )
    product = _parse_product_block(block)
    assert product is not None
    assert product["price"] == 999.0
    assert product["coins_save"] == 15

def test_parse_product_block_optional_fields_missing() -> None:
    """A minimal block (only title + price) yields None for optionals."""
    block = textwrap.dedent(
        """\
        [![Alt](https://img.drz.lazcdn.com/x.jpg)](https://www.daraz.pk/products/slug-i1.html)

        [Title](https://www.daraz.pk/products/slug-i1.html "Title")

        Rs. 100
        """
    )
    product = _parse_product_block(block)
    assert product is not None
    assert product["discount_percentage"] is None
    assert product["coins_save"] is None
    assert product["sold_count"] is None
    assert product["rating_count"] is None
    assert product["location"] is None

# ---------------------------------------------------------------------- #
# _strip_trailing_sections and _split_into_product_blocks
# ---------------------------------------------------------------------- #
def test_strip_trailing_sections_removes_pagination() -> None:
    """Pagination and category content are removed from the tail."""
    trimmed = _strip_trailing_sections(SYNTHETIC_MARKDOWN)
    assert "Category" not in trimmed
    assert "- [102]" not in trimmed
    assert "i1959941878" in trimmed

def test_split_into_product_blocks_finds_two() -> None:
    """Two products produce two blocks."""
    trimmed = _strip_trailing_sections(SYNTHETIC_MARKDOWN)
    blocks = _split_into_product_blocks(trimmed)
    assert len(blocks) == 2
    assert "i1959941878" in blocks[0]
    assert "i1962924638" in blocks[1]

def test_split_into_product_blocks_lazy_loaded_shape() -> None:
    """Blocks are split correctly when image anchors are missing.

    Simulates a page where the first two products have image anchors and
    the third does not (lazy loading).
    """
    md = textwrap.dedent(
        """\
        [![Alt1](https://img/x.jpg)](https://www.daraz.pk/products/slug-one-i1.html)

        [One](https://www.daraz.pk/products/slug-one-i1.html "One")

        Rs. 100

        [![Alt2](https://img/y.jpg)](https://www.daraz.pk/products/slug-two-i2.html)

        [Two](https://www.daraz.pk/products/slug-two-i2.html "Two")

        Rs. 200

        [Three](https://www.daraz.pk/products/slug-three-i3.html "Three")

        Rs. 300
        """
    )
    blocks = _split_into_product_blocks(md)
    assert len(blocks) == 3
    assert "i1" in blocks[0]
    assert "i2" in blocks[1]
    assert "i3" in blocks[2]
    # The lazy-loaded block has no image anchor but still parses.
    product = _parse_product_block(blocks[2])
    assert product is not None
    assert product["id"] == "i3"
    assert product["image"] is None
    assert product["price"] == 300.0

def test_split_into_product_blocks_empty() -> None:
    """No products produce an empty list."""
    assert _split_into_product_blocks("# nothing here") == []

# ---------------------------------------------------------------------- #
# parse_search_results -- end-to-end against the synthetic fixture
# ---------------------------------------------------------------------- #
def test_parse_search_results_full() -> None:
    """The public entry point extracts header, pagination, and products."""
    parsed = parse_search_results(SYNTHETIC_MARKDOWN, current_page=1)

    assert parsed["total_items_found"] == 11084
    assert parsed["pagination"] == {
        "current_page": 1,
        "total_pages": 102,
        "items_per_page": 40,
    }
    products = parsed["products"]
    assert isinstance(products, list)
    assert len(products) == 2

    first = products[0]
    assert first["id"] == "i1959941878"
    assert first["price"] == 579.0
    assert first["discount_percentage"] == 27
    assert first["coins_save"] == 29
    assert first["sold_count"] == 184
    assert first["rating_count"] == 40
    assert first["location"] == "Punjab"

    second = products[1]
    assert second["id"] == "i1962924638"
    assert second["price"] == 599.0
    assert second["location"] == "Sindh"

def test_parse_search_results_empty_page() -> None:
    """A page with no products returns empty products and None total."""
    parsed = parse_search_results("# Nothing\n\nNo items found.", current_page=1)
    assert parsed["products"] == []
    assert parsed["total_items_found"] is None
    assert parsed["pagination"]["total_pages"] is None

# ---------------------------------------------------------------------- #
# Real fixture -- structural sanity check
# ---------------------------------------------------------------------- #
def test_parse_real_fixture_structural_sanity() -> None:
    """Structural sanity check against the real scraped fixture."""
    markdown = _load_real_fixture()
    parsed = parse_search_results(markdown, current_page=1)

    products = parsed["products"]
    assert isinstance(products, list)
    assert len(products) > 0, (
        "Parser found zero products in the real fixture. Inspect the "
        "fixture and adjust the regexes at the top of search_parser.py."
    )

    assert parsed["total_items_found"] is not None
    assert parsed["total_items_found"] > 0

    for product in products:
        assert product["id"].startswith("i")
        assert product["title"]
        assert product["url"].startswith("https://www.daraz.pk/products/")
        assert isinstance(product["price"], float)
        assert product["price"] >= 0.0
        assert product["currency"] == "PKR"

def test_parse_real_fixture_matches_known_products() -> None:
    """Spot-check specific products we know exist in the fixture."""
    markdown = _load_real_fixture()
    parsed = parse_search_results(markdown, current_page=1)
    by_id = {p["id"]: p for p in parsed["products"]}

    expected_ids = {
        "i1959941878",
        "i1962924638",
        "i319444203",
        "i440779980",
        "i498790293",
        "i1968708477",
        "i1947016010",
        "i1958263300",
    }
    missing = expected_ids - set(by_id.keys())
    assert not missing, f"Expected product IDs missing from parse: {sorted(missing)}"

    assert by_id["i1959941878"]["price"] == 579.0
    assert by_id["i1959941878"]["sold_count"] == 184
    assert by_id["i319444203"]["sold_count"] == 8100
    assert by_id["i440779980"]["sold_count"] == 5800
    assert by_id["i498790293"]["sold_count"] == 5300
    assert by_id["i1947016010"]["location"] == "Khyber Pakhtunkhwa"
    assert by_id["i1968708477"]["rating_count"] is None
