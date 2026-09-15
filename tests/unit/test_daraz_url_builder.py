"""Unit tests for the Daraz URL builders in :mod:`scrapers.daraz`."""

from __future__ import annotations

import pytest

from daraz_ai_shopping_assistant.scrapers.daraz import (
    _build_price_param,
    build_product_url,
    build_search_url,
)

BASE = "https://www.daraz.pk"
PATH = "/catalog/"

# ---------------------------------------------------------------------- #
# _build_price_param
# ---------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("lo", "hi", "expected"),
    [
        (None, None, None),
        (None, 800, "-800"),
        (100, None, "100-"),
        (100, 800, "100-800"),
        (100.9, 800.9, "100-800"),  # truncation to int
        (0, 500, "0-500"),
    ],
    ids=["none", "max-only", "min-only", "both", "fractional", "zero-min"],
)
def test_build_price_param(
    lo: float | None, hi: float | None, expected: str | None
) -> None:
    """The Daraz price parameter is rendered in the documented syntax."""
    assert _build_price_param(lo, hi) == expected

# ---------------------------------------------------------------------- #
# build_search_url
# ---------------------------------------------------------------------- #
def test_build_search_url_query_only() -> None:
    """A bare query produces the minimum viable URL."""
    url = build_search_url("gaming mouse", base_url=BASE, search_path=PATH)
    assert url == f"{BASE}{PATH}?q=gaming%20mouse"

def test_build_search_url_max_price() -> None:
    """An upper bound alone yields ``price=-N``."""
    url = build_search_url("gaming mouse", max_price=800, base_url=BASE, search_path=PATH)
    assert url == f"{BASE}{PATH}?q=gaming%20mouse&price=-800"

def test_build_search_url_min_price() -> None:
    """A lower bound alone yields ``price=N-``."""
    url = build_search_url("mouse", min_price=100, base_url=BASE, search_path=PATH)
    assert url == f"{BASE}{PATH}?q=mouse&price=100-"

def test_build_search_url_price_range() -> None:
    """Both bounds yield ``price=MIN-MAX``."""
    url = build_search_url(
        "mouse", min_price=100, max_price=800, base_url=BASE, search_path=PATH
    )
    assert url == f"{BASE}{PATH}?q=mouse&price=100-800"

def test_build_search_url_page_one_is_omitted() -> None:
    """Page 1 must not appear in the URL (Daraz's default)."""
    url = build_search_url("mouse", page=1, base_url=BASE, search_path=PATH)
    assert "page=" not in url

def test_build_search_url_page_two_is_included() -> None:
    """Page >= 2 must be included."""
    url = build_search_url("mouse", page=2, base_url=BASE, search_path=PATH)
    assert "page=2" in url

def test_build_search_url_full_combination() -> None:
    """Query + price range + page all combine correctly."""
    url = build_search_url(
        "gaming mouse",
        min_price=100,
        max_price=800,
        page=3,
        base_url=BASE,
        search_path=PATH,
    )
    assert url == f"{BASE}{PATH}?q=gaming%20mouse&price=100-800&page=3"

def test_build_search_url_special_characters_encoded() -> None:
    """Query special characters are percent-encoded."""
    url = build_search_url("a & b", base_url=BASE, search_path=PATH)
    assert "%26" in url  # & encoded as %26
    assert "a%20%26%20b" in url

def test_build_search_url_strips_trailing_slash_on_base() -> None:
    """A trailing slash on the base URL is normalised away."""
    url = build_search_url("x", base_url="https://www.daraz.pk/", search_path=PATH)
    assert url.startswith("https://www.daraz.pk/catalog/?")

# ---------------------------------------------------------------------- #
# build_product_url
# ---------------------------------------------------------------------- #
def test_build_product_url() -> None:
    """A product URL is built from the ID."""
    url = build_product_url("i1959941878", base_url=BASE)
    assert url == f"{BASE}/products/i1959941878.html"

def test_build_product_url_strips_trailing_slash() -> None:
    """A trailing slash on the base URL is normalised away."""
    url = build_product_url("i1", base_url="https://www.daraz.pk/")
    assert url == "https://www.daraz.pk/products/i1.html"
