"""Pydantic models for the search envelope.

Defines the objects returned by ``GET /api/v1/products/search``:

- :class:`SearchFilters` — the numeric bounds the user applied.
- :class:`Pagination` — page metadata as reported by Daraz.
- :class:`SearchResult` — the full response envelope.

See ``Specification.md`` §8, §10, and §22.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from daraz_ai_shopping_assistant.models.product import Product


class SearchFilters(BaseModel):
    """Price bounds applied to a search query.

    Both fields are optional. When both are provided, ``min_price`` must be
    less than or equal to ``max_price``.
    """

    model_config = ConfigDict(extra="forbid")

    min_price: float | None = Field(
        default=None,
        ge=0.0,
        description="Minimum price in PKR, or null when unbounded.",
    )
    max_price: float | None = Field(
        default=None,
        ge=0.0,
        description="Maximum price in PKR, or null when unbounded.",
    )

    @model_validator(mode="after")
    def _check_price_range(self) -> SearchFilters:
        """Ensure ``min_price <= max_price`` when both are set.

        Returns:
            ``self`` unchanged when the range is valid.

        Raises:
            ValueError: When ``min_price`` exceeds ``max_price``.
        """
        if (
            self.min_price is not None
            and self.max_price is not None
            and self.min_price > self.max_price
        ):
            raise ValueError(
                f"min_price ({self.min_price}) must be <= max_price ({self.max_price})"
            )
        return self


class Pagination(BaseModel):
    """Pagination metadata as reported by Daraz."""

    model_config = ConfigDict(extra="forbid")

    current_page: int = Field(
        default=1,
        ge=1,
        description="1-indexed page number of the current response.",
    )
    total_pages: int | None = Field(
        default=None,
        ge=0,
        description="Total number of pages Daraz reports, or null if unknown.",
    )
    items_per_page: int | None = Field(
        default=40,
        ge=1,
        description="Number of items rendered per page (Daraz uses 40).",
    )


class SearchResult(BaseModel):
    """The full response envelope for a product search.

    This is the object serialised by ``GET /api/v1/products/search``.
    """

    model_config = ConfigDict(extra="forbid")

    source: str = Field(
        default="Daraz.pk",
        min_length=1,
        description="Human-readable data source label.",
    )
    search_query: str = Field(
        ...,
        min_length=1,
        description="The user's search query, verbatim.",
    )
    filters: SearchFilters = Field(
        default_factory=SearchFilters,
        description="Numeric filters applied to the search.",
    )
    total_items_found: int | None = Field(
        default=None,
        ge=0,
        description="Total item count reported by Daraz, or null if unknown.",
    )
    scraped_at: datetime = Field(
        ...,
        description="Timezone-aware timestamp of when the search was scraped.",
    )
    products: list[Product] = Field(
        default_factory=list,
        description="Validated product objects extracted from the page.",
    )
    pagination: Pagination = Field(
        default_factory=Pagination,
        description="Page metadata for the current response.",
    )

    @field_validator("scraped_at")
    @classmethod
    def _ensure_timezone_aware(cls, value: datetime) -> datetime:
        """Require ``scraped_at`` to carry timezone information.

        Daraz's local time is PKT (+05:00). Storing naive timestamps would
        silently lose that offset, so we reject them outright.

        Args:
            value: The parsed datetime.

        Returns:
            The unchanged datetime when it is timezone-aware.

        Raises:
            ValueError: If the datetime is naive.
        """
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(
                "scraped_at must be timezone-aware (e.g., '...+05:00'). "
                "Naive datetimes are rejected."
            )
        return value
