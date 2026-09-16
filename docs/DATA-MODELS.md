# Data Models Reference

All domain objects are Pydantic `BaseModel` subclasses in
`src/daraz_ai_shopping_assistant/models/`. They are the **contract** every
layer validates against. Parsers and scrapers emit *untrusted* dicts; only
after `model_validate` does data become a real object the API may return.

General rules:

- Every model sets `extra="forbid"` so unexpected fields are rejected.
- Numeric fields are numeric. Missing data is `None` — never invented, never
  defaulted to `0`.
- Optional numerics use PEP 604 (`float | None`), not `Optional[float]`.

---

## 1. `Product` (models/product.py)

The lightweight object returned in search results.

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | `str` | yes | Daraz product ID (e.g. `i1959941878`), `min_length=1` |
| `title` | `str` | yes | Product title, `min_length=1` |
| `url` | `str` | yes | Canonical Daraz URL; must be `http(s)://` |
| `image` | `str \| None` | no | Image URL; must be `http(s)://` when present |
| `price` | `float` | yes | `ge=0` |
| `currency` | `str` | yes | 3-char ISO-4217; defaults to `"PKR"` |
| `original_price` | `float \| None` | no | Pre-discount price, `ge=0` |
| `discount_percentage` | `int \| None` | no | `0–100` |
| `coins_save` | `int \| None` | no | PKR value, `ge=0` |
| `sold_count` | `int \| None` | no | `ge=0` |
| `rating` | `float \| None` | no | `0–5` |
| `rating_count` | `int \| None` | no | `ge=0` |
| `location` | `str \| None` | no | e.g. `"Punjab"` |

**Validators:** `_validate_url` enforces that `url` and `image` start with
`http://` or `https://` when non-null.

---

## 2. `ProductDetails` (models/product.py)

Extends `Product` with the full product-page fields. Every added field is
optional because not every Daraz product page renders the same sections.

| Field | Type | Default | Notes |
|---|---|---|---|
| `description` | `str \| None` | `None` | Long-form description |
| `specifications` | `dict[str, str]` | `{}` | Flat key→value specs |
| `seller` | `Seller` | empty | Seller widget |
| `shipping` | `Shipping` | empty | Shipping details |
| `availability` | `str \| None` | `None` | e.g. `"In Stock"` |
| `variants` | `list[ProductVariant]` | `[]` | Colour/size/bundle |
| `reviews` | `list[Review]` | `[]` | Customer reviews |
| `recommendations` | `list[Recommendation]` | `[]` | Daraz's own carousel; never fabricated |

`ProductDetails` is the model whose `model_json_schema()` is passed to
Firecrawl structured extraction.

---

## 3. Sub-objects (models/product.py)

### `Seller`
| Field | Type | Notes |
|---|---|---|
| `name` | `str \| None` | Seller display name |
| `rating` | `float \| None` | `0–5` |
| `positive_rate` | `float \| None` | `0–100` percentage |

### `Shipping`
| Field | Type | Notes |
|---|---|---|
| `fee` | `float \| None` | PKR, `ge=0` |
| `free_shipping` | `bool \| None` | Explicit free-shipping flag |
| `estimated_delivery` | `str \| None` | Human-readable estimate |

### `ProductVariant`
| Field | Type | Notes |
|---|---|---|
| `name` | `str` | required, `min_length=1` |
| `price` | `float \| None` | variant-specific price |
| `available` | `bool \| None` | in-stock flag |
| `image` | `str \| None` | variant image |

### `Review`
| Field | Type | Notes |
|---|---|---|
| `rating` | `float \| None` | `0–5` |
| `comment` | `str \| None` | free-form text |
| `author` | `str \| None` | reviewer name |
| `date` | `str \| None` | unparsed date string as rendered |

---

## 4. `Recommendation` (models/recommendation.py)

A single card from Daraz's recommendation carousel. A **frozen**,
intentionally-reduced subset of `Product` — the carousel renders less
information than a search result.

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | `str` | yes | `min_length=1` |
| `title` | `str` | yes | `min_length=1` |
| `url` | `str` | yes | must be `http(s)://` |
| `image` | `str \| None` | no | must be `http(s)://` when present |
| `price` | `float \| None` | no | `ge=0` |
| `currency` | `str` | yes | defaults to `"PKR"` |
| `discount_percentage` | `int \| None` | no | `0–100` |

`model_config` adds `frozen=True` so recommendations are immutable.

---

## 5. Search Envelope (models/search.py)

### `SearchFilters`
| Field | Type | Notes |
|---|---|---|
| `min_price` | `float \| None` | `ge=0` |
| `max_price` | `float \| None` | `ge=0` |

A `model_validator(mode="after")` enforces `min_price <= max_price` when both
are set — a contradictory range fails fast, before any network call.

### `Pagination`
| Field | Type | Default | Notes |
|---|---|---|---|
| `current_page` | `int` | `1` | `ge=1` |
| `total_pages` | `int \| None` | `None` | `ge=0` |
| `items_per_page` | `int \| None` | `40` | Daraz uses 40 |

### `SearchResult`
The full response envelope for `GET /products/search`.

| Field | Type | Notes |
|---|---|---|
| `source` | `str` | defaults to `"Daraz.pk"` |
| `search_query` | `str` | the query, verbatim, `min_length=1` |
| `filters` | `SearchFilters` | defaults to empty |
| `total_items_found` | `int \| None` | `ge=0` |
| `scraped_at` | `datetime` | **must be timezone-aware** (enforced) |
| `products` | `list[Product]` | validated product objects |
| `pagination` | `Pagination` | defaults to page 1 |

**`scraped_at` validation:** a `field_validator` rejects naive datetimes. The
service always stamps this with `pkt_now()` (PKT, UTC+05:00).

---

## 6. Worked Examples

A valid `Product` (search result):

```json
{
  "id": "i1959941878",
  "title": "RGB Gaming Mouse...",
  "url": "https://www.daraz.pk/products/7-i1959941878.html",
  "image": "https://img.drz.lazcdn.com/...",
  "price": 579.0,
  "currency": "PKR",
  "original_price": null,
  "discount_percentage": 27,
  "coins_save": 29,
  "sold_count": 184,
  "rating": null,
  "rating_count": 40,
  "location": "Punjab"
}
```

A valid `SearchResult` envelope:

```json
{
  "source": "Daraz.pk",
  "search_query": "Gaming Mouse",
  "filters": { "min_price": null, "max_price": 800.0 },
  "total_items_found": 11084,
  "scraped_at": "2026-09-15T10:49:06+05:00",
  "products": [ "...valid Product objects..." ],
  "pagination": { "current_page": 1, "total_pages": 102, "items_per_page": 40 }
}
```

---

## 7. `schemas/search.py`

For the MVP the API response shape is identical to the domain model, so
`schemas/search.SearchResponse` is simply an alias for `SearchResult`:

```python
SearchResponse = SearchResult
```

If the API ever needs to reshape the response (drop fields, add links, wrap
in an envelope), subclass or replace the alias — the rest of the codebase
keeps using the domain model.
