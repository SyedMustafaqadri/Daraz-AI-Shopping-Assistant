"""Service layer.

Services orchestrate: they call scrapers, parsers, agents, and Pydantic
models in the correct order, and they own the domain-level concerns that
neither the scraping nor the parsing layer should know about.

Package-level re-exports are deliberately limited to the services that do
NOT depend on the agents package. ``ChatService`` is a facade over the
agent graph, and the agent graph depends on ``ProductService`` and
``SearchService``. Re-exporting ``ChatService`` from this package would
create an import cycle:

    services/__init__
        -> services.chat_service
            -> agents.graph
                -> agents.tools
                    -> services.product_service
                        -> services/__init__   (cycle)

Consumers that need ``ChatService`` import it directly from
``daraz_ai_shopping_assistant.services.chat_service``.

Public API:
    - SearchService / search_products -- search orchestration.
    - ProductService / get_product_service -- product-detail orchestration.
"""

from __future__ import annotations

from daraz_ai_shopping_assistant.services.product_service import (
    ProductService,
    get_product_service,
)
from daraz_ai_shopping_assistant.services.search_service import (
    SearchService,
    get_search_service,
    search_products,
)

__all__ = [
    "ProductService",
    "SearchService",
    "get_product_service",
    "get_search_service",
    "search_products",
]
