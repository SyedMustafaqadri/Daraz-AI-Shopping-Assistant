"""Pydantic models for products.

Defines two levels of product representation:

- :class:`Product` — the lightweight object returned in search results.
- :class:`ProductDetails` — the full product-page object, which extends
  :class:`Product` with description, seller, shipping, variants, reviews,
  and Daraz-sourced recommendations.

Supporting sub-objects (:class:`Seller`, :class:`Shipping`,
:class:`ProductVariant`, :class:`Review`) are used inside
:class:`ProductDetails`.

See ``Specification.md`` §9, §10, and §14.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from daraz_ai_shopping_assistant.models.recommendation import Recommendation


# ---------------------------------------------------------------------- #
# Sub-objects used by ProductDetails
# ---------------------------------------------------------------------- #
class Seller(BaseModel):
    """Seller information rendered on a Daraz product page.

    Every field is optional because Daraz does not always expose seller
    metadata (e.g., for some marketplace listings).
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str | None = Field(
        default=None,
        description="Seller display name.",
    )
    rating: float | None = Field(
        default=None,
        ge=0.0,
        le=5.0,
        description="Seller rating on a 0-5 scale.",
    )
    positive_rate: float | None = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="Positive feedback rate as a percentage (0-100).",
    )


class Shipping(BaseModel):
    """Shipping information for a product.

    Every field is optional because shipping details vary by region and
    seller, and are sometimes hidden behind a location prompt.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    fee: float | None = Field(
        default=None,
        ge=0.0,
        description="Shipping fee in PKR, or null if unknown.",
    )
    free_shipping: bool | None = Field(
        default=None,
        description="True if the listing explicitly advertises free shipping.",
    )
    estimated_delivery: str | None = Field(
        default=None,
        description="Human-readable delivery estimate (e.g., '2-4 days').",
    )


class ProductVariant(BaseModel):
    """A single variant (size, colour, bundle, …) of a product."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(
        ...,
        min_length=1,
        description="Variant label as shown on the page.",
    )
    price: float | None = Field(
        default=None,
        ge=0.0,
        description="Variant-specific price in PKR, if different from the base.",
    )
    available: bool | None = Field(
        default=None,
        description="Whether this variant is in stock.",
    )
    image: str | None = Field(
        default=None,
        description="Variant-specific image URL.",
    )


class Review(BaseModel):
    """A single customer review as rendered on the product page."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    rating: float | None = Field(
        default=None,
        ge=0.0,
        le=5.0,
        description="Reviewer's star rating (0-5).",
    )
    comment: str | None = Field(
        default=None,
        description="Free-form review text.",
    )
    author: str | None = Field(
        default=None,
        description="Reviewer display name.",
    )
    date: str | None = Field(
        default=None,
        description="Review date as rendered by Daraz (unparsed string).",
    )


# ---------------------------------------------------------------------- #
# Product (search result)
# ---------------------------------------------------------------------- #
class Product(BaseModel):
    """A lightweight product as it appears in a Daraz search-result page.

    Numeric fields are stored as numbers, never as formatted strings.
    Missing fields are ``None`` — never invented, never defaulted to zero.
    """

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    id: str = Field(
        ...,
        min_length=1,
        description="Daraz product identifier (e.g., 'i1959941878').",
    )
    title: str = Field(
        ...,
        min_length=1,
        description="Product title.",
    )
    url: str = Field(
        ...,
        min_length=1,
        description="Canonical Daraz product URL.",
    )
    image: str | None = Field(
        default=None,
        description="Primary product image URL, or null if not rendered.",
    )
    price: float = Field(
        ...,
        ge=0.0,
        description="Current price in `currency` units.",
    )
    currency: str = Field(
        default="PKR",
        min_length=3,
        max_length=3,
        description="ISO-4217 currency code. Always 'PKR' for Daraz.pk.",
    )
    original_price: float | None = Field(
        default=None,
        ge=0.0,
        description="Original (pre-discount) price, when Daraz shows one.",
    )
    discount_percentage: int | None = Field(
        default=None,
        ge=0,
        le=100,
        description="Discount percentage (0-100), or null if no discount.",
    )
    coins_save: int | None = Field(
        default=None,
        ge=0,
        description="Coins savings amount in PKR, or null if not offered.",
    )
    sold_count: int | None = Field(
        default=None,
        ge=0,
        description="Number of units sold, or null if not shown.",
    )
    rating: float | None = Field(
        default=None,
        ge=0.0,
        le=5.0,
        description="Average star rating (0-5), or null if not rated.",
    )
    rating_count: int | None = Field(
        default=None,
        ge=0,
        description="Number of ratings/reviews, or null if not shown.",
    )
    location: str | None = Field(
        default=None,
        description="Seller / product location (e.g., 'Punjab').",
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


# ---------------------------------------------------------------------- #
# ProductDetails (extends Product)
# ---------------------------------------------------------------------- #
class ProductDetails(Product):
    """A full product-page representation.

    Inherits every field from :class:`Product` and adds the detail-only
    fields exposed on Daraz product pages. Every extra field is optional
    because not every product page renders the same sections.
    """

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    description: str | None = Field(
        default=None,
        description="Long-form product description (plain text or Markdown).",
    )
    specifications: dict[str, str] = Field(
        default_factory=dict,
        description="Key-value product specifications (e.g., {'Brand': 'Logitech'}).",
    )
    seller: Seller = Field(
        default_factory=Seller,
        description="Seller information, empty when not exposed.",
    )
    shipping: Shipping = Field(
        default_factory=Shipping,
        description="Shipping information, empty when not exposed.",
    )
    availability: str | None = Field(
        default=None,
        description="Availability string as rendered by Daraz (e.g., 'In Stock').",
    )
    variants: list[ProductVariant] = Field(
        default_factory=list,
        description="Product variants, empty when none are listed.",
    )
    reviews: list[Review] = Field(
        default_factory=list,
        description="Customer reviews, empty when none are rendered.",
    )
    recommendations: list[Recommendation] = Field(
        default_factory=list,
        description=(
            "Products recommended by Daraz on this page. Empty when the "
            "carousel is absent — never fabricated by the backend."
        ),
    )
