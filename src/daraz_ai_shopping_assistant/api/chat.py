"""Chat endpoint.

Exposes two routes:

    - ``POST /api/v1/chat``        -- non-streaming JSON response.
    - ``POST /api/v1/chat/stream`` -- Server-Sent Events token stream.

Both handlers are thin: they validate the request body, delegate to the
chat service, and shape the transport. All agent logic -- intent
parsing, tool selection, response formatting -- lives in the ``agents``
package and is orchestrated by ``ChatService``.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from daraz_ai_shopping_assistant.api.deps import get_chat_service_dep
from daraz_ai_shopping_assistant.core.logging import get_logger
from daraz_ai_shopping_assistant.schemas.chat import ChatRequest, ChatResponse
from daraz_ai_shopping_assistant.services.chat_service import ChatService

logger = get_logger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])

@router.post(
    "",
    response_model=ChatResponse,
    summary="Chat with the shopping assistant",
    description=(
        "Send a natural-language message and receive a reply plus any "
        "structured data the agent produced. The LLM only parses the "
        "user's intent and phrases the reply -- it never invents product "
        "data. All product information comes from the same validated "
        "services that back the /products endpoints.\n\n"
        "Send the same ``conversation_id`` on every turn to continue a "
        "conversation. Omit it on the first turn; the server generates "
        "one and returns it in the response."
    ),
    responses={
        400: {"description": "Invalid request body."},
        429: {"description": "Rate-limited by Firecrawl or Daraz."},
        502: {"description": "Upstream scraping failure."},
        504: {"description": "Upstream timeout."},
    },
)
async def chat_endpoint(
    request: ChatRequest,
    service: Annotated[ChatService, Depends(get_chat_service_dep)],
) -> ChatResponse:
    """Process a user message through the agent pipeline.

    Args:
        request: The chat request body.
        service: Injected chat service.

    Returns:
        A ChatResponse with the assistant's reply and any structured data.
    """
    return await service.chat(request.message, request.conversation_id)

@router.post(
    "/stream",
    summary="Stream a chat response as Server-Sent Events",
    description=(
        "Same input contract as ``POST /chat``. The response is an SSE "
        "stream with three event types: ``token`` (one LLM token), "
        "``done`` (final conversation_id, intent, and error), and the "
        "literal ``[DONE]`` sentinel that closes the stream. Each event "
        "is one ``data: <json>`` frame followed by a blank line."
    ),
    responses={
        200: {
            "content": {"text/event-stream": {}},
            "description": (
                "SSE stream. Each frame is ``data: <json>\\n\\n``. The "
                "final frame is ``data: [DONE]\\n\\n``."
            ),
        },
        400: {"description": "Invalid request body."},
        429: {"description": "Rate-limited by Firecrawl or Daraz."},
        502: {"description": "Upstream scraping failure."},
        504: {"description": "Upstream timeout."},
    },
)
async def chat_stream_endpoint(
    request: ChatRequest,
    service: Annotated[ChatService, Depends(get_chat_service_dep)],
) -> StreamingResponse:
    """Stream a user message through the agent pipeline as SSE.

    Args:
        request: The chat request body.
        service: Injected chat service.

    Returns:
        A StreamingResponse emitting ``data: <json>`` frames.
    """

    async def event_generator() -> AsyncIterator[str]:
        """Yield SSE frames for the duration of the stream.

        Yields:
            ``data: <json>\\n\\n`` frames, terminated by
            ``data: [DONE]\\n\\n``.
        """
        try:
            async for event in service.chat_stream(
                request.message, request.conversation_id
            ):
                payload = json.dumps(event, ensure_ascii=False, default=str)
                yield f"data: {payload}\n\n"
        except Exception:
            logger.exception("CHAT_STREAM_FAILED")
            error_payload = json.dumps(
                {"type": "error", "message": "Internal server error."},
                ensure_ascii=False,
            )
            yield f"data: {error_payload}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )

__all__ = ["router"]
