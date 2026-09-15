"""Service layer.

Services orchestrate: they call scrapers, parsers, and Pydantic models in
the correct order, and they own the domain-level concerns that neither the
scraping nor the parsing layer should know about (timestamps, filter
objects, response envelopes, structured logging events).

Public API:
    - :class:`SearchService` -- search orchestration.
    - :func:`search_products` -- module-level convenience wrapper around a
      default :class:`SearchService` singleton.
"""

from __future__ import annotations

from daraz_ai_shopping_assistant.services.search_service import (
    SearchService,
    get_search_service,
    search_products,
)

__all__ = [
    "SearchService",
    "get_search_service",
    "search_products",
]
