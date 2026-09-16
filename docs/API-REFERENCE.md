# API Reference

All endpoints are versioned under the prefix set by
`settings.api_v1_prefix` (default `/api/v1`). Responses are JSON. In
development (`APP_ENV=dev`), interactive OpenAPI docs are served at
`/docs`, `/redoc`, and `/openapi.json`.

---

## Meta Endpoints

### `GET /`
Tiny landing payload so hitting the host root is not a 404.

```json
{ "service": "Daraz AI Shopping Assistant", "docs": "/docs" }
```

### `GET /health`
Liveness probe.

```json
{ "status": "ok", "app": "Daraz AI Shopping Assistant", "env": "dev", "version": "0.1.0" }
```

---

## Product Endpoints

### `GET /api/v1/products/search`

Search Daraz.pk for products matching a free-text query, optionally bounded
by price. Returns one page of validated products plus Daraz-reported
pagination.

**Query parameters**

| Param | Type | Required | Constraints | Notes |
|---|---|---|---|---|
| `q` | `str` | yes | `min_length=1`, `max_length=200` | Free-text query, e.g. `gaming mouse` |
| `min_price` | `float` | no | `ge=0` | Lower bound in PKR |
| `max_price` | `float` | no | `ge=0` | Upper bound in PKR |
| `page` | `int` | no | `ge=1`, `le=200` | 1-indexed; defaults to 1 |

**Example**

```
GET /api/v1/products/search?q=gaming%20mouse&max_price=800&page=1
```

**200 response body**

```json
{
  "source": "Daraz.pk",
  "search_query": "gaming mouse",
  "filters": { "min_price": null, "max_price": 800.0 },
  "total_items_found": 11084,
  "scraped_at": "2026-09-15T10:49:06+05:00",
  "products": [
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
  ],
  "pagination": { "current_page": 1, "total_pages": 102, "items_per_page": 40 }
}
```

**FastAPI validation errors (422)** are returned for malformed input
(missing `q`, empty `q`, negative price, `page < 1`). These are produced by
FastAPI's own `Query` validators, before the service runs.

**Application errors** are mapped from the service/scraper exceptions; see
[ERRORS-AND-LOGGING.md](ERRORS-AND-LOGGING.md) for the full table.

---

### `GET /api/v1/products/{product_id}`  *(planned — Phase 6)*

Return full product details via Firecrawl structured extraction with the
`ProductDetails` schema. Not yet implemented.

### `GET /api/v1/products/{product_id}/recommendations`  *(planned — Phase 7)*

Return Daraz's own recommendations read from the product-detail payload. No
second Firecrawl call. Not yet implemented.

---

## `POST /api/v1/chat`  *(planned — Phase 8)*

The future natural-language endpoint. Not implemented until the deterministic
backend is proven. When built, LangGraph tools will call the **services**,
never the scrapers or Firecrawl directly.

---

## Response Conventions

- Every product in a response is a fully-validated Pydantic model; no raw
  dicts.
- Missing data is `null`, never an invented value.
- Timestamps are timezone-aware (PKT, UTC+05:00).
- Error responses contain only `{"detail": "..."}` — no stack traces, no
  internal context.
