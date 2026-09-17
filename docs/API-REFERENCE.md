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

```
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

```
GET /api/v1/products/search?q=gaming%20mouse&max_price=800&page=1
```

Successful response:

```
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

FastAPI validation errors (`422`) are returned for malformed input before the
service is called. Application exceptions are mapped to HTTP status codes as
described in docs/ERRORS-AND-LOGGING.md.

**Local store:** A successful response is written to the local scrape store
keyed by `query + min_price + max_price + page`. A repeat request with the
same key returns the stored payload without calling Firecrawl, until the TTL
(default 6 hours) expires.

---

### `GET /api/v1/products/{product_id}`

Fetch a single Daraz product by its Daraz ID. This path uses the product-detail
structured extraction flow and validates the payload through the
`ProductDetails` model before returning it.

Path parameter:

- `product_id` must match `^i\d+$` and look like `i1959941878`

Example:

```
GET /api/v1/products/i1959941878
```

The response is a validated `ProductDetails` object, with nested seller,
shipping, variants, reviews, and recommendations data when Daraz provides it.

**Local store:** A successful response is written to the local scrape store
keyed by `product_id`. A repeat request returns the stored payload without
calling Firecrawl, until the TTL (default 24 hours) expires.

---

### `GET /api/v1/products/{product_id}/recommendations`

Return the recommendation list from the same product payload. This does not
trigger a second Firecrawl fetch. If the page has no recommendation carousel,
the list is empty.

Example:

```
GET /api/v1/products/i1959941878/recommendations
```

Returns a JSON array of `Recommendation` objects.

---

## Chat endpoints

### `POST /api/v1/chat`

Send a user message and receive a reply plus structured data. The chat
layer uses a LangGraph agent to route intent and calls the backend
services for search or product lookups.

Request:

```
{
  "message": "Find me a gaming mouse under Rs. 5000",
  "conversation_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7"
}
```

`conversation_id` is optional. Omit it on the first turn; the server
generates one and returns it in the response. Send it back on subsequent
turns to continue the same conversation.

Response:

```
{
  "reply": "Here are some gaming mice under Rs. 5000: ...",
  "conversation_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
  "intent": "search",
  "recommended_products": [
    {
      "id": "i1959941878",
      "title": "RGB Gaming Mouse",
      "url": "https://www.daraz.pk/products/7-i1959941878.html",
      "price": 579.0,
      "currency": "PKR"
    },
    {
      "id": "i201116087",
      "title": "Triple Mode High-Quality Wireless Bluetooth Gaming Mouse",
      "url": "https://www.daraz.pk/products/6-3-24-i201116087.html",
      "price": 997.0,
      "currency": "PKR"
    }
  ],
  "data": { "search_query": "gaming mouse", "products": [ ... ] },
  "error": null
}
```

Response fields:

| Field ↕▾ | Type ↕▾ | Notes ↕▾ |
|---|---|---|
| −`reply` | `string` | The assistant's natural-language reply. Always present. |
| −`conversation_id` | `string` | Send this back on the next turn. Always present. |
| −`intent` | `string | null` | `search`, `get_product`, `get_recommendations`, or `small_talk`. |
| −`recommended_products` | `array<object>` | Top products the assistant is recommending. Empty for small-talk. |
| −`data` | `object | null` | Raw tool result. Shape depends on `intent`. |
| −`error` | `string | null` | Non-null only when a tool or the LLM failed. |
⚙

**`recommended_products` semantics:**

| Intent ↕▾ | Contents ↕▾ | Limit ↕▾ |
|---|---|---|
| −`search` | top products from the search result | 5 |
| −`get_product` | the single product as a one-item list | 1 |
| −`get_recommendations` | top recommendations from the tool result | 5 |
| −`small_talk` | empty | -- |
| −tool error | empty | -- |
⚙

The agent never invents product data; every entry in `recommended_products`
comes from the same validated services that back the `/products` endpoints.

---

### `POST /api/v1/chat/stream`

Same input contract as `POST /api/v1/chat`. Returns a Server-Sent Events
stream instead of a single JSON body.

Request:

```
{
  "message": "Find me a gaming mouse under Rs. 5000",
  "conversation_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7"
}
```

Response: `Content-Type: text/event-stream`. Each event is one
`data: <json>` line followed by a blank line.

Two event types are emitted, followed by a terminal sentinel:

**Token event** -- one per LLM token as it is generated:

```
data: {"type": "token", "text": "Here "}

data: {"type": "token", "text": "are "}

data: {"type": "token", "text": "some "}
```

**Done event** -- once, after the stream completes:

```
data: {"type": "done", "conversation_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7", "intent": "search", "recommended_products": [ ... ], "error": null}
```

**Terminal sentinel**:

```
data: [DONE]
```

Only tokens from the reply-phrasing LLM call are streamed. The intent-
parsing call also hits the LLM but its output is a structured object and
is never forwarded to the client.

If an internal error occurs during streaming, the server emits an error
event followed by the sentinel:

```
data: {"type": "error", "message": "Internal server error."}

data: [DONE]
```

---

## Response conventions

- every product object is a validated Pydantic model
- missing data stays `null` rather than being invented
- timestamps are timezone-aware PKT timestamps
- error responses are `{"detail": "..."}` with no internal stack traces
- FastAPI `422` handles malformed query/path input before application logic runs
- conversation ids are echoed back on every chat response so clients can
thread turns together

