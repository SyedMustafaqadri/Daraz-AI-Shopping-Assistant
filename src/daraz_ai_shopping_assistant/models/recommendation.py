"""Pydantic model for a single Daraz recommendation.

Daraz product pages render a "Recommended for you" / "Similar products"
carousel. We extract those cards verbatim and return them as
:class:`Recommendation` objects.

These recommendations are **sourced from Daraz** — the backend does not
generate them. See ``Specification.md`` §15 and §16.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Recommendation(BaseModel):
    """A single product card shown in Daraz's own recommendation carousel.

    This is intentionally a subset of :class:`~daraz_ai_shopping_assistant.models.product.Product`.
    Daraz's carousels render less information than a search result, so we
    only model the fields that are reliably present.
    """

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        frozen=True,
    )

    id: str = Field(
        ...,
        min_length=1,
        description="Daraz product identifier (e.g., 'i1959941878').",
    )
    title: str = Field(
        ...,
        min_length=1,
        description="Product title as shown on the recommendation card.",
    )
    url: str = Field(
        ...,
        min_length=1,
        description="Canonical Daraz product URL.",
    )
    image: str | None = Field(
        default=None,
        description="Product thumbnail URL, or null if not rendered.",
    )
    price: float | None = Field(
        default=None,
        ge=0.0,
        description="Current price in `currency` units, or null if not rendered.",
    )
    currency: str = Field(
        default="PKR",
        min_length=3,
        max_length=3,
        description="ISO-4217 currency code. Always 'PKR' for Daraz.pk.",
    )
    discount_percentage: int | None = Field(
        default=None,
        ge=0,
        le=100,
        description="Discount percentage (0-100), or null if not rendered.",
    )

    @field_validator("url", "image")
    @classmethod
    def _validate_url(cls, value: str | None) -> str | None:
        """Reject non-HTTP(S) URLs early to catch parser bugs.

        Args:
            value: Raw URL string, or ``None``.

        Returns:
            The unchanged URL when valid.

        Raises:
            ValueError: If the URL does not start with ``http://`` or ``https://``.
        """
        if value is None:
            return None
        if not value.startswith(("http://", "https://")):
            raise ValueError(
                f"URL must start with 'http://' or 'https://' — got {value!r}"
            )
        return value
