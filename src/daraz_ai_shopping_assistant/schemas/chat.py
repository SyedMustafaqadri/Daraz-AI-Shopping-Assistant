"""Chat endpoint schemas.

The chat endpoint accepts a single user message, optionally tagged with a
conversation id, and returns a reply plus whatever structured data the
agent produced. The response is deliberately loose on the data field -- its
shape depends on which tool ran, and the ``intent`` field tells the client
how to interpret it.

Conversation identity:

    A ``conversation_id`` threads a client's turns together. The server
    uses it as the LangGraph ``thread_id`` so the checkpointer can
    retrieve prior state. If the client omits it, the server generates a
    new one and returns it in the response; the client sends it back on
    the next turn.

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
    conversation_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        description=(
            "Client-supplied conversation identifier. Omit for a new "
            "conversation; the server generates one and returns it in the "
            "response. Send it back on the next turn to continue the same "
            "conversation."
        ),
        examples=["7c9e6679-7425-40de-944b-e07fc1f90ae7"],
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
    conversation_id: str = Field(
        ...,
        min_length=1,
        description=(
            "Conversation identifier for this exchange. Echo back the "
            "client's value when supplied, otherwise the server-generated "
            "one. Send this on the next turn to continue the conversation."
        ),
    )
    intent: str | None = Field(
        default=None,
        description=(
            "Which action the agent took: 'search', 'get_product', "
            "or 'small_talk'."
        ),
    )
    data: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Raw tool result. Shape depends on intent: a SearchResult for "
            "'search' or a ProductDetails for 'get_product'."
        ),
    )
    error: str | None = Field(
        default=None,
        description="Non-null when a tool or the LLM failed.",
    )

__all__ = ["ChatRequest", "ChatResponse"]
