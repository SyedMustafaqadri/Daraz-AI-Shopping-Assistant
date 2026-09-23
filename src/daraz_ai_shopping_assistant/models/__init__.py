"""Pydantic domain models for the Daraz AI Shopping Assistant.

This package defines every structured object the backend passes between
layers. Parsers return raw dicts; services validate those dicts into these
models; API routes serialise them to JSON.

Public API:
    - :class:`Product` — a lightweight search-result product.
    - :class:`ProductDetails` — a full product page (extends Product).
    - :class:`Seller`, :class:`Shipping`, :class:`ProductVariant`, :class:`Review`
      — sub-objects used inside :class:`ProductDetails`.
    - :class:`SearchFilters`, :class:`Pagination`, :class:`SearchResult`
      — the envelope returned by the search endpoint.
"""

from __future__ import annotations

from daraz_ai_shopping_assistant.models.product import (
    Product,
    ProductDetails,
    ProductVariant,
    Review,
    Seller,
    Shipping,
)
from daraz_ai_shopping_assistant.models.search import (
    Pagination,
    SearchFilters,
    SearchResult,
)

__all__ = [
    "Pagination",
    "Product",
    "ProductDetails",
    "ProductVariant",
    "Review",
    "SearchFilters",
    "SearchResult",
    "Seller",
    "Shipping",
]
