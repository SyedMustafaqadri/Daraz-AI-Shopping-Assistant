"""Scraping layer.

This package owns all external content retrieval. Contract:

    - :class:`DarazScraper` -- abstract interface services depend on.
    - :class:`FirecrawlDarazScraper` -- concrete implementation using
      Firecrawl's Markdown and structured-extraction modes.
    - :class:`FirecrawlAdapter` -- the ONLY module permitted to import
      ``firecrawl``. Wraps the SDK, retries transient failures, and maps
      upstream errors to typed application exceptions.

Nothing outside this package may import ``firecrawl`` directly. See
``agent.md`` Section 5 and ``Specification.md`` Sections 19-20.
"""

from __future__ import annotations

from daraz_ai_shopping_assistant.scrapers.base import DarazScraper
from daraz_ai_shopping_assistant.scrapers.daraz import (
    FirecrawlDarazScraper,
    build_product_url,
    build_search_url,
)
from daraz_ai_shopping_assistant.scrapers.firecrawl import FirecrawlAdapter

__all__ = [
    "DarazScraper",
    "FirecrawlAdapter",
    "FirecrawlDarazScraper",
    "build_product_url",
    "build_search_url",
]
