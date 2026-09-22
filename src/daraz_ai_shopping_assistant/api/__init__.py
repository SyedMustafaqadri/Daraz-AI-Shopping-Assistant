"""FastAPI route layer.

This package owns HTTP concerns: URL paths, query and path parameters,
status codes, and response serialisation. Business logic lives in
``services``.

The exported ``api_router`` is mounted by ``main.create_app`` under
``settings.api_v1_prefix``.

Router inclusion order is significant:

    - ``search_router`` is included FIRST. It defines the fixed path
      ``/products/search``. The ``products_router`` below defines the
      parameterised path ``/products/{product_id}``, which would otherwise
      capture the literal ``search`` segment and fail pattern validation.
    - ``products_router`` is included SECOND.
    - ``chat_router`` has its own prefix and does not collide.
    - ``voice_router`` exposes ``/voice/ws`` and does not collide.
    - ``voice_live_router`` exposes ``/voice/live/ws`` and does not collide.
    - ``telnyx_router`` exposes the Telnyx webhook and Conversation Relay
      WebSocket endpoints.
"""

from __future__ import annotations

from fastapi import APIRouter

from daraz_ai_shopping_assistant.api.chat import router as chat_router
from daraz_ai_shopping_assistant.api.products import router as products_router
from daraz_ai_shopping_assistant.api.search import router as search_router
from daraz_ai_shopping_assistant.api.telnyx import router as telnyx_router
from daraz_ai_shopping_assistant.api.voice import router as voice_router
from daraz_ai_shopping_assistant.api.voice_live import router as voice_live_router

api_router: APIRouter = APIRouter()

# Order matters: fixed paths before parameterised paths.
api_router.include_router(search_router)
api_router.include_router(products_router)
api_router.include_router(chat_router)
api_router.include_router(voice_router)
api_router.include_router(voice_live_router)
api_router.include_router(telnyx_router)

__all__ = ["api_router"]
