"""Unit tests for :mod:`daraz_ai_shopping_assistant.models.product`."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from daraz_ai_shopping_assistant.models.product import (
    Product,
    ProductDetails,
    ProductVariant,
    Review,
    Seller,
    Shipping,
)


def _valid_product_payload() -> dict[str, object]:
    """Return a minimal, valid Product payload.

    Returns:
        A dict that should pass :class:`Product` validation.
    """
    return {
        "id": "i1959941878",
        "title": "RGB Gaming Mouse with 7 lights",
        "url": "https://www.daraz.pk/products/7-i1959941878.html",
        "price": 579.0,
    }


# ---------------------------------------------------------------------- #
# Product
# ---------------------------------------------------------------------- #
def test_minimal_product() -> None:
    """A product with only required fields must be valid; optionals default to None."""
    product = Product.model_validate(_valid_product_payload())
    assert product.id == "i1959941878"
    assert product.price == 579.0
    assert product.currency == "PKR"
    assert product.image is None
    assert product.original_price is None
    assert product.discount_percentage is None
    assert product.coins_save is None
    assert product.sold_count is None
    assert product.rating is None
    assert product.rating_count is None
    assert product.location is None


def test_full_product() -> None:
    """Every field supplied must round-trip."""
    product = Product.model_validate(
        {
            **_valid_product_payload(),
            "image": "https://img.drz.lazcdn.com/static/pk/p/abc.jpg",
            "original_price": 799.0,
            "discount_percentage": 27,
            "coins_save": 29,
            "sold_count": 184,
            "rating": 4.6,
            "rating_count": 40,
            "location": "Punjab",
        }
    )
    assert product.discount_percentage == 27
    assert product.coins_save == 29
    assert product.sold_count == 184
    assert product.rating == 4.6
    assert product.rating_count == 40
    assert product.location == "Punjab"


def test_price_coerces_int_to_float() -> None:
    """An integer price must be coerced to float."""
    product = Product.model_validate({**_valid_product_payload(), "price": 579})
    assert isinstance(product.price, float)
    assert product.price == 579.0


def test_negative_price_rejected() -> None:
    """A negative price must be rejected."""
    with pytest.raises(ValidationError):
        Product.model_validate({**_valid_product_payload(), "price": -1.0})


def test_discount_above_100_rejected() -> None:
    """A discount > 100% must be rejected."""
    with pytest.raises(ValidationError):
        Product.model_validate({**_valid_product_payload(), "discount_percentage": 150})


def test_discount_negative_rejected() -> None:
    """A negative discount must be rejected."""
    with pytest.raises(ValidationError):
        Product.model_validate({**_valid_product_payload(), "discount_percentage": -5})


def test_rating_above_5_rejected() -> None:
    """A rating > 5 must be rejected."""
    with pytest.raises(ValidationError):
        Product.model_validate({**_valid_product_payload(), "rating": 5.1})


def test_blank_title_rejected() -> None:
    """A blank title must be rejected."""
    with pytest.raises(ValidationError):
        Product.model_validate({**_valid_product_payload(), "title": ""})


def test_non_http_url_rejected() -> None:
    """A relative URL must be rejected."""
    with pytest.raises(ValidationError):
        Product.model_validate({**_valid_product_payload(), "url": "products/abc.html"})


def test_extra_field_forbidden() -> None:
    """Undeclared fields must raise ValidationError."""
    with pytest.raises(ValidationError):
        Product.model_validate({**_valid_product_payload(), "unknown_field": "x"})


def test_currency_must_be_three_chars() -> None:
    """Currency codes must be exactly 3 characters."""
    with pytest.raises(ValidationError):
        Product.model_validate({**_valid_product_payload(), "currency": "PK"})


# ---------------------------------------------------------------------- #
# Seller
# ---------------------------------------------------------------------- #
def test_seller_defaults_to_all_none() -> None:
    """Seller with no arguments must be all None."""
    seller = Seller()
    assert seller.name is None
    assert seller.rating is None
    assert seller.positive_rate is None


def test_seller_rating_upper_bound() -> None:
    """Seller rating > 5 must be rejected."""
    with pytest.raises(ValidationError):
        Seller(name="Shop", rating=5.5)


# ---------------------------------------------------------------------- #
# Shipping
# ---------------------------------------------------------------------- #
def test_shipping_defaults_to_all_none() -> None:
    """Shipping with no arguments must be all None."""
    shipping = Shipping()
    assert shipping.fee is None
    assert shipping.free_shipping is None
    assert shipping.estimated_delivery is None


def test_shipping_negative_fee_rejected() -> None:
    """A negative shipping fee must be rejected."""
    with pytest.raises(ValidationError):
        Shipping(fee=-1.0)


# ---------------------------------------------------------------------- #
# ProductVariant & Review
# ---------------------------------------------------------------------- #
def test_variant_requires_name() -> None:
    """A variant without a name must be rejected."""
    with pytest.raises(ValidationError):
        ProductVariant()  # type: ignore[call-arg]


def test_variant_minimal_valid() -> None:
    """A variant with just a name is valid."""
    variant = ProductVariant(name="Black")
    assert variant.name == "Black"
    assert variant.price is None


def test_review_all_optional() -> None:
    """An empty Review is valid — every field is optional."""
    review = Review()
    assert review.rating is None
    assert review.comment is None


def test_review_rating_out_of_range_rejected() -> None:
    """A review rating > 5 must be rejected."""
    with pytest.raises(ValidationError):
        Review(rating=6.0)


# ---------------------------------------------------------------------- #
# ProductDetails
# ---------------------------------------------------------------------- #
def test_details_inherits_product_fields() -> None:
    """ProductDetails must accept every Product field."""
    details = ProductDetails.model_validate(_valid_product_payload())
    assert details.id == "i1959941878"
    assert details.price == 579.0


def test_details_defaults() -> None:
    """Detail-only fields must default to empty / None."""
    details = ProductDetails.model_validate(_valid_product_payload())
    assert details.description is None
    assert details.specifications == {}
    assert isinstance(details.seller, Seller)
    assert isinstance(details.shipping, Shipping)
    assert details.availability is None
    assert details.variants == []
    assert details.reviews == []


def test_details_with_nested_objects() -> None:
    """ProductDetails must accept nested sub-objects."""
    details = ProductDetails.model_validate(
        {
            **_valid_product_payload(),
            "description": "A great mouse.",
            "specifications": {"Brand": "Logitech", "DPI": "8000"},
            "seller": {"name": "Logitech Store", "rating": 4.8, "positive_rate": 95.0},
            "shipping": {
                "fee": 0.0,
                "free_shipping": True,
                "estimated_delivery": "2-4 days",
            },
            "availability": "In Stock",
            "variants": [{"name": "Black", "price": 579.0, "available": True}],
            "reviews": [{"rating": 5.0, "comment": "Great!", "author": "Ali"}],
        }
    )
    assert details.specifications["Brand"] == "Logitech"
    assert details.seller.name == "Logitech Store"
    assert details.shipping.free_shipping is True
    assert len(details.variants) == 1
    assert len(details.reviews) == 1


def test_details_rejects_extra_fields() -> None:
    """ProductDetails must still forbid extra fields."""
    with pytest.raises(ValidationError):
        ProductDetails.model_validate({**_valid_product_payload(), "extra": "no"})
