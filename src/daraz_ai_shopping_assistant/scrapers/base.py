"""Abstract scraper interface for Daraz.

This module defines the contract that every Daraz scraper must satisfy.
Services depend on ``DarazScraper`` -- never on a concrete implementation --
so that Firecrawl (or Playwright, or anything else) can be swapped without
touching the service layer.

Two extraction paths exist and are chosen by page type (see ADR-001 in
``docs/ARCHITECTURE-DECISIONS.md``):

    - Search pages: fetched as raw Markdown via :meth:`fetch_search_markdown`.
      A deterministic parser handles them in the service layer.
    - Product pages: fetched as a structured payload via
      :meth:`fetch_product_payload`. The payload follows the
      ``ProductDetails`` JSON schema and is validated by Pydantic.

The scraper is intentionally *dumb* about domain concepts: it returns raw
strings and dicts. It does not validate, normalise, or construct Pydantic
models. That is the service layer's responsibility.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class DarazScraper(ABC):
    """Abstract interface for fetching Daraz content.

    Implementations:

        - :class:`daraz_ai_shopping_assistant.scrapers.daraz.FirecrawlDarazScraper`
          (current production implementation).
        - Any future Playwright-based alternative, which would subclass this
          interface identically and be drop-in replaceable.
    """

    @abstractmethod
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
            query: Free-text search query (e.g., ``"gaming mouse"``).
            min_price: Lower bound in PKR, or ``None`` for unbounded.
            max_price: Upper bound in PKR, or ``None`` for unbounded.
            page: 1-indexed page number.

        Returns:
            The raw Markdown as returned by the scraper backend.

        Raises:
            ScraperError: On any upstream failure.
        """
        ...

    @abstractmethod
    async def fetch_product_payload(self, product_id: str) -> dict[str, Any]:
        """Fetch a structured product payload from a Daraz product page.

        Uses LLM-driven structured extraction (see ADR-001). The returned
        dict is *untrusted* -- the caller must run it through
        ``ProductDetails.model_validate``.

        Args:
            product_id: Daraz product identifier (e.g., ``"i1959941878"``).

        Returns:
            A dict shaped like the ``ProductDetails`` schema.

        Raises:
            ScraperError: On any upstream failure.
        """
        ...

__all__ = ["DarazScraper"]
