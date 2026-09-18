"""State schema for the LangGraph agent pipeline.

The graph carries one :class:`AgentState` dict through every node. Two
field types are used:

    - ``messages`` uses LangGraph's ``add_messages`` reducer, so nodes
      append new messages by returning ``{"messages": [msg]}`` instead of
      replacing the whole list.
    - Every other field is a plain value that the last writer wins on.

The state is intentionally small and JSON-serialisable so that
checkpointing can be added later without reshaping the graph.

The ``mode`` field selects which response prompt the ``respond`` node
uses. ``"text"`` is the default (markdown bullets, prices as
``Rs. 1,234``, product URLs). ``"voice"`` produces plain speech suitable
for text-to-speech.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from pydantic import BaseModel, ConfigDict, Field


class IntentType(StrEnum):
    """The set of user intents the agent can act on."""

    SEARCH = "search"
    GET_PRODUCT = "get_product"
    SMALL_TALK = "small_talk"

class ParsedIntent(BaseModel):
    """Structured output of the intent-parsing LLM call.

    The LLM produces one of these for every user message. Only the fields
    relevant to the chosen ``intent`` are populated.
    """

    model_config = ConfigDict(extra="forbid")

    intent: IntentType = Field(
        ...,
        description="Which action the agent should take.",
    )
    query: str | None = Field(
        default=None,
        description="Search query (for intent='search').",
    )
    product_id: str | None = Field(
        default=None,
        description=(
            "Daraz product identifier, 'i' followed by digits "
            "(for intent='get_product')."
        ),
    )
    min_price: float | None = Field(
        default=None,
        ge=0.0,
        description="Minimum price in PKR (for intent='search').",
    )
    max_price: float | None = Field(
        default=None,
        ge=0.0,
        description="Maximum price in PKR (for intent='search').",
    )
    page: int = Field(
        default=1,
        ge=1,
        description="1-indexed page number (for intent='search').",
    )

class AgentState(TypedDict):
    """Typed state carried through the graph.

    Attributes:
        messages: Conversation history. Appended to via ``add_messages``.
        intent: The parsed intent from the most recent LLM classification.
        tool_result: Raw dict returned by the executed tool, if any.
        error: Human-readable error message, or ``None`` on success.
        mode: ``"text"`` for chat replies, ``"voice"`` for spoken replies.
            When absent, the graph treats the turn as text.
    """

    messages: Annotated[list[AnyMessage], add_messages]
    intent: ParsedIntent | None
    tool_result: dict[str, Any] | None
    error: str | None
    mode: str

__all__ = ["AgentState", "IntentType", "ParsedIntent"]
