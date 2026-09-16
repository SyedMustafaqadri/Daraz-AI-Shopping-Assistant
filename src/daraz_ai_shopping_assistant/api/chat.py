"""Chat endpoint.

Exposes ``POST /api/v1/chat``. The handler is thin: it validates the
request body, delegates to the chat service, and returns the response.
All agent logic -- intent parsing, tool selection, response formatting --
lives in the ``agents`` package and is orchestrated by ``ChatService``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from daraz_ai_shopping_assistant.api.deps import get_chat_service_dep
from daraz_ai_shopping_assistant.schemas.chat import ChatRequest, ChatResponse
from daraz_ai_shopping_assistant.services.chat_service import ChatService

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
        "services that back the /products endpoints."
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
    return await service.chat(request.message)

__all__ = ["router"]
