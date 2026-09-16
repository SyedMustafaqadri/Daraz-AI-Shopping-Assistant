# Errors and Logging

This document covers the exception hierarchy and the structured logging
conventions used by the backend.

---

## 1. Exception hierarchy

All application-specific exceptions derive from `DarazScraperError`. Each
subclass sets `http_status` so the API layer can map failures without parsing
messages or string-matching error text.

```text
DarazScraperError (http_status=500)
├── InvalidRequestError        (400)
├── ProductNotFoundError       (404)
├── ScraperError               (502)
│   ├── ScraperTimeoutError    (504)
│   └── UpstreamRateLimitError (429)
└── ParseError                 (502)
```

Subclasses are raised at the layer where the issue occurs:

- `InvalidRequestError`: invalid query/input values or malformed product ids
- `ProductNotFoundError`: requested product could not be found
- `ScraperError`: Firecrawl or Daraz returned an upstream failure
- `ScraperTimeoutError`: upstream request timed out
- `UpstreamRateLimitError`: upstream rate limit was hit
- `ParseError`: structured data failed Pydantic validation

These exceptions are centralised in `src/daraz_ai_shopping_assistant/core/exceptions.py`.

---

## 2. HTTP mapping

The API layer registers exception handlers in `api/exception_handlers.py`.

| Situation | HTTP | Response |
|---|---|---|
| invalid request params | 400 | `{"detail": "..."}` |
| product missing | 404 | `{"detail": "..."}` |
| rate limited | 429 | `{"detail": "..."}` |
| upstream scraping or parse failure | 502 | `{"detail": "..."}` |
| upstream timeout | 504 | `{"detail": "..."}` |
| unhandled exception | 500 | `{"detail": "Internal server error."}` |
| FastAPI parameter validation | 422 | FastAPI validation error |

The API never leaks stack traces or internal context in the response body.

---

## 3. Logging conventions

Logging is configured in `src/daraz_ai_shopping_assistant/core/logging.py`.
The project uses a structured logging pattern via `extra={"ctx": {...}}`.

Examples:

```python
logger.info(
    "SEARCH_STARTED",
    extra={"ctx": {"query": q, "page": page}},
)
```

The formatters render `ctx` into readable console output or JSON output.

### Core events used in the codebase

| Event | Emitted by | Notes |
|---|---|---|
| `APP_STARTED` / `APP_STOPPED` | app lifecycle | startup/shutdown |
| `SEARCH_STARTED` | `SearchService.search` | query + page |
| `PRODUCTS_EXTRACTED` | `SearchService.search` | count |
| `PRODUCT_VALIDATION_FAILED` | search validation | invalid item dropped |
| `SEARCH_COMPLETED` | `SearchService.search` | success summary |
| `SEARCH_EMPTY` | search result handling | empty page |
| `PRODUCT_FETCH_STARTED` | `ProductService.get_product` | product id |
| `PRODUCT_FETCH_COMPLETED` | `ProductService.get_product` | duration + recommendation count |
| `PRODUCT_VALIDATION_FAILED` | product validation | payload failed Pydantic validation |
| `RECOMMENDATIONS_EXTRACTED` | `ProductService.get_recommendations` | recommendation count |
| `FIRECRAWL_REQUEST` | `FirecrawlAdapter` | includes `mode` |
| `FIRECRAWL_RESPONSE` | `FirecrawlAdapter` | includes `mode` and response shape |
| `FIRECRAWL_RETRY` | adapter retry | transient failure |
| `API_ERROR` | exception handler | request-scoped error |
| `API_UNHANDLED_EXCEPTION` | catch-all handler | full stack trace logged |
| `CHAT_STARTED` / `CHAT_COMPLETED` | chat service | agent timing |
| `AGENT_INTENT_PARSED` | graph | routing result |
| `AGENT_INTENT_PARSE_FAILED` | graph | fallback to small talk |

Sensitive values such as API keys, full HTML, and user-identifying data are never logged.

---

## 4. Firecrawl logging notes

`FIRECRAWL_*` log entries always include a `mode` field:

- `"markdown"` for `scrape()`
- `"json"` for `scrape_json()`

This makes it easy to distinguish the deterministic search path from the product-detail extraction path in logs and traces.