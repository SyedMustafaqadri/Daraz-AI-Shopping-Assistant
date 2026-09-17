"""FastAPI dependency providers.

Every external service the API depends on is resolved through a function
in this module so that tests can override it with
``app.dependency_overrides[provider] = factory``.

Rules:
    - Providers are plain functions with no side effects beyond returning
      the object.
    - Providers must not perform I/O. Construction of an expensive object
      should be deferred to the service's own laziness.
    - The returned object is what the route handler receives as its
      parameter.

The process-wide service singletons are warmed in ``main._lifespan`` so
that the scrape store and conversation checkpointer are already bound by
the time the first request arrives. These providers simply return them.
"""

from __future__ import annotations

from daraz_ai_shopping_assistant.services.chat_service import (
    ChatService,
    get_chat_service,
)
from daraz_ai_shopping_assistant.services.product_service import (
    ProductService,
    get_product_service,
)
from daraz_ai_shopping_assistant.services.search_service import (
    SearchService,
    get_search_service,
)


def get_search_service_dep() -> SearchService:
    """Return the process-wide SearchService singleton.

    Returns:
        The shared SearchService instance.
    """
    return get_search_service()

def get_product_service_dep() -> ProductService:
    """Return the process-wide ProductService singleton.

    Returns:
        The shared ProductService instance.
    """
    return get_product_service()

def get_chat_service_dep() -> ChatService:
    """Return the process-wide ChatService singleton.

    Returns:
        The shared ChatService instance.
    """
    return get_chat_service()

__all__ = [
    "get_chat_service_dep",
    "get_product_service_dep",
    "get_search_service_dep",
]
