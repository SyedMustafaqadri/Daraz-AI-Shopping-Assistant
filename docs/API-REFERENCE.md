# API Reference

All endpoints are versioned under the prefix configured by
`settings.api_v1_prefix` (default `/api/v1`). Responses are JSON.
In development (`APP_ENV=dev`), OpenAPI docs are served at `/docs`,
`/redoc`, and `/openapi.json`.

---

## Meta endpoints

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

## Product endpoints

### `GET /api/v1/products/search`

Search Daraz.pk for products matching a free-text query, optionally bounded
by price. Returns one page of validated products plus Daraz-reported
pagination.

Query parameters:

- `q` (required, string, min 1, max 200)
- `min_price` (optional, float, `>= 0`)
- `max_price` (optional, float, `>= 0`)
- `page` (optional, int, `>= 1`, `<= 200`)

Example:

```http
GET /api/v1/products/search?q=gaming%20mouse&max_price=800&page=1
```

Successful response:

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
      "title": "RGB Gaming Mouse",
      "url": "https://www.daraz.pk/products/7-i1959941878.html",
      "image": "https://img.drz.lazcdn.com/...",
      "price": 579.0,
      "currency": "PKR",
      "original_price": null,
      "discount_percentage": 27,
      "coins_save": 29.0,
      "sold_count": 184,
      "rating": null,
      "rating_count": 40,
      "location": "Punjab"
    }
  ],
  "pagination": { "current_page": 1, "total_pages": 102, "items_per_page": 40 }
}
```

FastAPI validation errors (`422`) are returned for malformed input before the
service is called. Application exceptions are mapped to HTTP status codes as
described in docs/ERRORS-AND-LOGGING.md.

---

### `GET /api/v1/products/{product_id}`

Fetch a single Daraz product by its Daraz ID. This path uses the product-detail
structured extraction flow and validates the payload through the
`ProductDetails` model before returning it.

Path parameter:

- `product_id` must match `^i\d+$` and look like `i1959941878`

Example:

```http
GET /api/v1/products/i1959941878
```

The response is a validated `ProductDetails` object, with nested seller,
shipping, variants, reviews, and recommendations data when Daraz provides it.

---

### `GET /api/v1/products/{product_id}/recommendations`

Return the recommendation list from the same product payload. This does not
trigger a second Firecrawl fetch. If the page has no recommendation carousel,
the list is empty.

Example:

```http
GET /api/v1/products/i1959941878/recommendations
```

Returns a JSON array of `Recommendation` objects.

---

## Chat endpoint

### `POST /api/v1/chat`

Send a user message and receive a reply plus optional structured tool data.
The chat layer uses a LangGraph agent to route intent and then calls backend
services for search or product lookups.

Request:

```json
{ "message": "Find me a gaming mouse under Rs. 5000" }
```

Response:

```json
{
  "reply": "I found a few gaming mice under Rs. 5000.",
  "intent": "search",
  "data": { "search_query": "gaming mouse", "products": [...] },
  "error": null
}
```

The agent never invents product data; it reuses the validated results from the
same product and search services that back the REST API.

---

## Response conventions

- every product object is a validated Pydantic model
- missing data stays `null` rather than being invented
- timestamps are timezone-aware PKT timestamps
- error responses are `{"detail": "..."}` with no internal stack traces
- FastAPI `422` handles malformed query/path input before application logic runs
