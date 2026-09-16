"""Product endpoint schemas.

The product-detail response shape is identical to the domain model
``ProductDetails``. Exposing an alias keeps a single source of truth for
the schema -- the JSON Schema Firecrawl is asked to produce and the
response FastAPI serialises are the same object.

If the API ever needs to reshape the response, replace this alias with a
subclass or a purpose-built model. The rest of the code keeps using the
domain model.
"""

from __future__ import annotations

from daraz_ai_shopping_assistant.models.product import ProductDetails

#: Alias exposed to the API layer for the product-detail response body.
ProductDetailsResponse = ProductDetails

__all__ = ["ProductDetailsResponse"]
