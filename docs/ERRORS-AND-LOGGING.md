# Errors and Logging

This document covers the two cross-cutting concerns that every layer must
respect: the exception hierarchy (`core/exceptions.py`) and the structured
logging conventions (`core/logging.py`).

---

## 1. Exception Hierarchy

All application-specific exceptions derive from `DarazScraperError`. Each
subclass carries an `http_status` class attribute so API-level handlers map
failures to HTTP codes **without inspecting exception messages**.

```
DarazScraperError (base, http_status=500)
├── InvalidRequestError        (400)
├── ProductNotFoundError       (404)
├── ScraperError               (502)
│   ├── ScraperTimeoutError    (504)
│   └── UpstreamRateLimitError (429)
└── ParseError                 (502)
```

### Base: `DarazScraperError`

```python
class DarazScraperError(Exception):
    http_status: int = 500

    def __init__(self, message: str, *, context: dict[str, object] | None = None) -> None:
        ...
```

- `message`: human-readable description.
- `context`: optional structured context (`query`, `url`, `product_id`, ...).
- `__str__` appends context as `key=value` pairs when present.

### Subclasses

| Exception | `http_status` | When raised | Extra attributes |
|---|---|---|---|
| `InvalidRequestError` | 400 | Caller supplied invalid parameters (empty query, contradictory filters) | — |
| `ProductNotFoundError` | 404 | Requested product cannot be found | — |
| `ScraperError` | 502 | Scraper failed to retrieve/return content (upstream) | `url`, `upstream_status` |
| `ScraperTimeoutError` | 504 | Upstream did not respond within the timeout | inherits `url`, `upstream_status` |
| `UpstreamRateLimitError` | 429 | Firecrawl or Daraz rate-limited the request | inherits `url`, `upstream_status` |
| `ParseError` | 502 | Scraped content could not be parsed into structured data | `source` (e.g. `"product"`) |

### Where each is raised

- `ScraperError` / `ScraperTimeoutError` / `UpstreamRateLimitError` — raised
  by `FirecrawlAdapter` (via `_map_exception`) after all retries are
  exhausted.
- `InvalidRequestError` — raised by `SearchService.search` when the query is
  empty/whitespace, or when `min_price > max_price`.
- `ParseError` — raised when a structured-extraction payload fails Pydantic
  validation (product detail path). The raw LLM payload is logged
  server-side and **never** returned to the client.

---

## 2. HTTP Mapping

The API layer registers two handlers (see `api/exception_handlers.py`):

| Situation | HTTP | Body |
|---|---|---|
| Invalid request params | 400 | `{"detail": "..."}` |
| Product not found | 404 | `{"detail": "..."}` |
| Rate limited (Firecrawl/Daraz) | 429 | `{"detail": "..."}` |
| Upstream scraping failure | 502 | `{"detail": "..."}` |
| Upstream timeout | 504 | `{"detail": "..."}` |
| Parse failure | 502 | `{"detail": "..."}` |
| Anything unhandled | 500 | `{"detail": "Internal server error."}` |
| Malformed input (FastAPI `Query`/`Path` validation) | 422 | FastAPI's standard validation error |

**Rules:**

- Never leak stack traces to consumers. FastAPI handlers return
  `{"detail": "..."}` only.
- The full exception context (url, upstream_status, query, index, ...) is
  logged server-side but is **not** in the response body.
- FastAPI's own 422 validation errors (bad query params) are separate from
  application 400 errors and are handled by FastAPI, not by
  `daraz_scraper_error_handler`.

---

## 3. Exception Handlers (`api/exception_handlers.py`)

`register_exception_handlers(app)` is called from `create_app()` **before**
any route is registered, so handlers are in place for the first request.

### `daraz_scraper_error_handler`

- Registered against `DarazScraperError`.
- Because Starlette's `add_exception_handler` is typed to accept a handler
  that takes the base `Exception`, the handler is declared with `exc:
  Exception` and narrows at runtime with `isinstance(exc,
  DarazScraperError)`. Non-matching exceptions are re-raised so the catch-all
  takes over.
- Logs `API_ERROR` with path, method, status, message, and context.
- Returns a `JSONResponse` with `exc.http_status` and `{"detail":
  exc.message}`.

### `unhandled_exception_handler`

- Registered against `Exception` (catch-all).
- Logs the full traceback via `logger.exception("API_UNHANDLED_EXCEPTION", ...)`.
- Returns a generic `500` with `{"detail": "Internal server error."}` — no
  internal details leak.

**Order matters:** the specific handler is registered before the catch-all so
Starlette dispatches to the most specific match first.

---

## 4. Logging

### 4.1 Setup

`configure_logging(level=None, fmt=None)` in `core/logging.py` sets up the
root logger. It is **idempotent** — calling it more than once replaces the
existing handler set rather than stacking.

- `fmt="console"` (default, dev): human-readable, with a `ctx:` line for
  structured context.
- `fmt="json"` (production): single-line JSON objects.

`get_logger(name)` returns a module-level logger. Always call it at import
time with `__name__`:

```python
from daraz_ai_shopping_assistant.core.logging import get_logger
logger = get_logger(__name__)
```

Unless in `DEBUG` mode, noisy third-party loggers (`httpx`, `httpcore`,
`urllib3`, `firecrawl`) are silenced to `WARNING`.

### 4.2 Structured Context

Attach structured context via `extra={"ctx": {...}}`:

```python
logger.info(
    "SEARCH_STARTED",
    extra={"ctx": {"query": q, "page": page}},
)
```

The formatters render `record.ctx` (a mapping) into the output — console as a
`ctx:` line, JSON as a `ctx` object.

### 4.3 Event-Name Convention

Significant lifecycle events use uppercase token prefixes (Spec §30).
Include structured context: `query`, `page`, `count`, `duration_ms`,
`error_type`.

**Emit these exact event names:**

| Event | Emitted by | Level |
|---|---|---|
| `APP_STARTED` / `APP_STOPPED` | `main.py` lifespan | INFO |
| `SEARCH_STARTED` | `SearchService.search` | INFO |
| `PRODUCTS_EXTRACTED` | `SearchService.search` | INFO |
| `PRODUCT_VALIDATION_FAILED` | `SearchService._validate_products` | WARNING |
| `SEARCH_COMPLETED` | `SearchService.search` | INFO |
| `SEARCH_EMPTY` | `SearchService.search` (no products) | WARNING |
| `DARAZ_SEARCH_FETCH` | `FirecrawlDarazScraper.fetch_search_markdown` | INFO |
| `DARAZ_PRODUCT_FETCH` | `FirecrawlDarazScraper.fetch_product_payload` | INFO |
| `FIRECRAWL_REQUEST` | `FirecrawlAdapter.scrape` / `scrape_json` | INFO |
| `FIRECRAWL_RESPONSE` | `FirecrawlAdapter.scrape` / `scrape_json` | INFO |
| `FIRECRAWL_RETRY` | `FirecrawlAdapter` on a retryable failure | WARNING |
| `API_ERROR` | `daraz_scraper_error_handler` | WARNING |
| `API_UNHANDLED_EXCEPTION` | `unhandled_exception_handler` | ERROR |

Future (Phase 6/7) events from Spec §30: `PRODUCT_FETCH_STARTED`,
`PRODUCT_FETCH_COMPLETED`, `RECOMMENDATIONS_EXTRACTED`.

**Never log:** API keys, full HTML, user PII.

### 4.4 Firecrawl Mode Field

`FIRECRAWL_*` events always carry a `mode` key — `"markdown"` for
`scrape`, `"json"` for `scrape_json` — so the two paths are distinguishable
in logs. `FIRECRAWL_RESPONSE` includes `chars` (Markdown) or `keys`
(JSON), `duration_ms`, and `attempts`.
