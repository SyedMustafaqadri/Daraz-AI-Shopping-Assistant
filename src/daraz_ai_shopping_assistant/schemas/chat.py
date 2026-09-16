"""Chat endpoint schemas.

The chat endpoint accepts a single user message and returns a reply plus
whatever structured data the agent produced. The response is deliberately
loose on the data field -- its shape depends on which tool ran, and the
``intent`` field tells the client how to interpret it.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ChatRequest(BaseModel):
    """Body of a ``POST /api/v1/chat`` request."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    message: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="The user's message to the assistant.",
        examples=["Find me a gaming mouse under Rs. 5000"],
    )

class ChatResponse(BaseModel):
    """Body of a ``POST /api/v1/chat`` response.

    ``reply`` is always present and is what a chat UI shows. ``data`` is
    the raw tool result and is only present when a tool ran -- the client
    uses ``intent`` to decide how to render it.
    """

    model_config = ConfigDict(extra="forbid")

    reply: str = Field(
        ...,
        min_length=1,
        description="The assistant's natural-language reply.",
    )
    intent: str | None = Field(
        default=None,
        description=(
            "Which action the agent took: 'search', 'get_product', "
            "'get_recommendations', or 'small_talk'."
        ),
    )
    data: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Raw tool result. Shape depends on intent: a SearchResult for "
            "'search', a ProductDetails for 'get_product', or a dict with "
            "a 'recommendations' list for 'get_recommendations'."
        ),
    )
    error: str | None = Field(
        default=None,
        description="Non-null when a tool or the LLM failed.",
    )

__all__ = ["ChatRequest", "ChatResponse"]
