"""Unit tests for the agent state schema."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from daraz_ai_shopping_assistant.agents.state import (
    IntentType,
    ParsedIntent,
)


def test_intent_type_values() -> None:
    """IntentType exposes exactly the three documented intents."""
    assert {member.value for member in IntentType} == {
        "search",
        "get_product",
        "small_talk",
    }

def test_parsed_intent_search_minimal() -> None:
    """A minimal search intent is valid with only the query set."""
    intent = ParsedIntent(intent=IntentType.SEARCH, query="gaming mouse")
    assert intent.intent is IntentType.SEARCH
    assert intent.query == "gaming mouse"
    assert intent.min_price is None
    assert intent.max_price is None
    assert intent.page == 1
    assert intent.product_id is None

def test_parsed_intent_search_full() -> None:
    """A search intent with all parameters round-trips."""
    intent = ParsedIntent(
        intent=IntentType.SEARCH,
        query="mouse",
        min_price=100.0,
        max_price=800.0,
        page=2,
    )
    assert intent.min_price == 100.0
    assert intent.max_price == 800.0
    assert intent.page == 2

def test_parsed_intent_get_product() -> None:
    """A product intent carries a product_id."""
    intent = ParsedIntent(
        intent=IntentType.GET_PRODUCT, product_id="i1959941878"
    )
    assert intent.intent is IntentType.GET_PRODUCT
    assert intent.product_id == "i1959941878"

def test_parsed_intent_small_talk() -> None:
    """A small-talk intent needs no parameters."""
    intent = ParsedIntent(intent=IntentType.SMALL_TALK)
    assert intent.intent is IntentType.SMALL_TALK
    assert intent.query is None
    assert intent.product_id is None

def test_parsed_intent_rejects_extra_fields() -> None:
    """Undeclared fields are rejected."""
    with pytest.raises(ValidationError):
        ParsedIntent(intent=IntentType.SEARCH, query="x", extra="no")

def test_parsed_intent_rejects_negative_price() -> None:
    """A negative price bound is rejected."""
    with pytest.raises(ValidationError):
        ParsedIntent(intent=IntentType.SEARCH, query="x", max_price=-1.0)

def test_parsed_intent_rejects_zero_page() -> None:
    """Page must be >= 1."""
    with pytest.raises(ValidationError):
        ParsedIntent(intent=IntentType.SEARCH, query="x", page=0)

def test_parsed_intent_rejects_unknown_intent() -> None:
    """An unrecognised intent string is rejected."""
    with pytest.raises(ValidationError):
        ParsedIntent(intent="not-an-intent")  # type: ignore[arg-type]
