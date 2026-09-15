"""Unit tests for :mod:`daraz_ai_shopping_assistant.models.recommendation`."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from daraz_ai_shopping_assistant.models.recommendation import Recommendation


def _valid_payload() -> dict[str, object]:
    """Return a minimal, valid recommendation payload.

    Returns:
        A dictionary that should pass :class:`Recommendation` validation.
    """
    return {
        "id": "i1959941878",
        "title": "RGB Gaming Mouse",
        "url": "https://www.daraz.pk/products/7-i1959941878.html",
    }


def test_minimal_valid_recommendation() -> None:
    """A recommendation with only id/title/url must be valid."""
    rec = Recommendation.model_validate(_valid_payload())
    assert rec.id == "i1959941878"
    assert rec.title == "RGB Gaming Mouse"
    assert rec.image is None
    assert rec.price is None
    assert rec.currency == "PKR"
    assert rec.discount_percentage is None


def test_full_recommendation() -> None:
    """All fields supplied must round-trip correctly."""
    rec = Recommendation.model_validate(
        {
            **_valid_payload(),
            "image": "https://img.drz.lazcdn.com/static/pk/p/abc.jpg",
            "price": 799.0,
            "discount_percentage": 40,
        }
    )
    assert rec.price == 799.0
    assert rec.discount_percentage == 40
    assert rec.image is not None


def test_missing_required_field_rejected() -> None:
    """Omitting a required field must raise ValidationError."""
    payload = _valid_payload()
    payload.pop("url")
    with pytest.raises(ValidationError):
        Recommendation.model_validate(payload)


def test_blank_title_rejected() -> None:
    """An empty title must be rejected."""
    payload = {**_valid_payload(), "title": ""}
    with pytest.raises(ValidationError):
        Recommendation.model_validate(payload)


def test_negative_price_rejected() -> None:
    """A negative price must be rejected."""
    payload = {**_valid_payload(), "price": -1.0}
    with pytest.raises(ValidationError):
        Recommendation.model_validate(payload)


def test_discount_above_100_rejected() -> None:
    """A discount above 100% must be rejected."""
    payload = {**_valid_payload(), "discount_percentage": 101}
    with pytest.raises(ValidationError):
        Recommendation.model_validate(payload)


def test_non_http_url_rejected() -> None:
    """A relative or javascript: URL must be rejected."""
    payload = {**_valid_payload(), "url": "/products/7-i1.html"}
    with pytest.raises(ValidationError):
        Recommendation.model_validate(payload)


def test_extra_field_forbidden() -> None:
    """An undeclared field must raise ValidationError (extra='forbid')."""
    payload = {**_valid_payload(), "unexpected": "value"}
    with pytest.raises(ValidationError):
        Recommendation.model_validate(payload)


def test_recommendation_is_immutable() -> None:
    """Recommendation is frozen — mutation must raise."""
    rec = Recommendation.model_validate(_valid_payload())
    with pytest.raises(ValidationError):
        rec.title = "Changed"  # type: ignore[misc]
