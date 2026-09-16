"""API-layer request and response schemas.

Schemas are the DTOs exchanged over HTTP. They may reuse domain models
directly (when the shapes are identical) or reshape them (when the API
needs to expose a different view).

Nothing in this package performs I/O or orchestrates anything -- these
are pure data shapes consumed by the FastAPI routes.
"""

from __future__ import annotations

from daraz_ai_shopping_assistant.schemas.chat import ChatRequest, ChatResponse
from daraz_ai_shopping_assistant.schemas.product import ProductDetailsResponse
from daraz_ai_shopping_assistant.schemas.search import SearchResponse

__all__ = [
    "ChatRequest",
    "ChatResponse",
    "ProductDetailsResponse",
    "SearchResponse",
]
