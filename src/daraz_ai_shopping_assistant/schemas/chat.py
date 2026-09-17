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

Recommended products:

    ``recommended_products`` is a curated, structured list of the products
    the assistant is recommending in this turn. It mirrors the window the
    LLM was told to work from (top five for search and recommendation
    intents). Clients that want a structured list without parsing the
    LLM's prose read this field.
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
    uses ``intent`` to decide how to render it. ``recommended_products``
    is a curated, structured list that mirrors the products the reply
    refers to.
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
            "'get_recommendations', or 'small_talk'."
        ),
    )
    recommended_products: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "Structured list of the products the assistant is "
            "recommending in this turn. For a search intent this is the "
            "top five products from the result set. For a get_product "
            "intent it is a single-item list. For get_recommendations it "
            "is the top five recommendations. Empty for small-talk and "
            "tool errors. Each entry is a product dict shaped like the "
            "Product or Recommendation model."
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
