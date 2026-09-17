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
|-- InvalidRequestError        (400)
|-- ProductNotFoundError       (404)
|-- ScraperError               (502)
|   |-- ScraperTimeoutError    (504)
|   `-- UpstreamRateLimitError (429)
`-- ParseError                 (502)
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

| Situation ↕▾ | HTTP ↕▾ | Response ↕▾ |
|---|---|---|
| −invalid request params | 400 | `{"detail": "..."}` |
| product missing | 404 | `{"detail": "..."}` |
| rate limited | 429 | `{"detail": "..."}` |
| upstream scraping or parse failure | 502 | `{"detail": "..."}` |
| upstream timeout | 504 | `{"detail": "..."}` |
| unhandled exception | 500 | `{"detail": "Internal server error."}` |
| FastAPI parameter validation | 422 | FastAPI validation error |
⚙

The API never leaks stack traces or internal context in the response body.

---

## 3. Logging conventions

Logging is configured in `src/daraz_ai_shopping_assistant/core/logging.py`.
The project uses a structured logging pattern via `extra={"ctx": {...}}`.

Examples:

```
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
| `SEARCH_STORE_HIT` | `SearchService.search` | cache hit; short-circuits the scrape |
| `PRODUCTS_EXTRACTED` | `SearchService.search` | count |
| `PRODUCT_VALIDATION_FAILED` | search + product validation | invalid item or payload dropped |
| `SEARCH_COMPLETED` | `SearchService.search` | success summary |
| `SEARCH_EMPTY` | search result handling | empty page |
| `PRODUCT_STORE_HIT` | `ProductService.get_product` | cache hit; short-circuits the scrape |
| `PRODUCT_FETCH_STARTED` | `ProductService.get_product` | product id |
| `PRODUCT_FETCH_COMPLETED` | `ProductService.get_product` | duration + recommendation count |
| `PRODUCT_FETCH_UNEXPECTED_ERROR` | `ProductService.get_product` | non-scraper exception |
| `PRODUCT_ID_MISSING_PREFIX` | `ProductService._normalise_product_id` | LLM dropped the `i` prefix |
| `PRODUCT_ID_MISMATCH` | `ProductService._normalise_product_id` | LLM returned a different id |
| `RECOMMENDATIONS_EXTRACTED` | `ProductService.get_recommendations` | recommendation count |
| `DARAZ_SEARCH_FETCH` | `FirecrawlDarazScraper` | outgoing Daraz search URL |
| `DARAZ_PRODUCT_FETCH` | `FirecrawlDarazScraper` | outgoing Daraz product URL |
| `DARAZ_PRODUCT_PAYLOAD_SHAPE` | `FirecrawlDarazScraper` | shape-only summary of the LLM payload |
| `FIRECRAWL_REQUEST` | `FirecrawlAdapter` | includes `mode` |
| `FIRECRAWL_RESPONSE` | `FirecrawlAdapter` | includes `mode` and response shape |
| `FIRECRAWL_RETRY` | `FirecrawlAdapter` retry | transient failure |
| `SCRAPE_STORE_CREATED` | `ScrapeStore.load` | file missing; empty store bootstrapped |
| `SCRAPE_STORE_LOADED` | `ScrapeStore.load` | entries loaded + expired entries pruned at load |
| `SCRAPE_STORE_LOAD_FAILED` | `ScrapeStore.load` | corrupt or unreadable file; empty store bootstrapped |
| `SCRAPE_STORE_WRITE_FAILED` | `ScrapeStore._persist_locked` | atomic write failed; in-memory state preserved |
| `SCRAPE_STORE_LARGE` | `ScrapeStore._persist_locked` | one-shot warning once the file crosses 10 MB |
| `SCRAPE_STORE_PRUNED` | app shutdown | count of expired entries removed on shutdown |
| `SCRAPE_STORE_PRUNE_FAILED` | app shutdown | prune raised; shutdown continues |
| `CHAT_STARTED` / `CHAT_COMPLETED` | `ChatService` | message length + intent + recommended count + timing |
| `CHAT_STREAM_STATE_LOOKUP_FAILED` | `ChatService.chat_stream` | post-stream state fetch failed; stream still terminated |
| `CHAT_STREAM_FAILED` | `api/chat.py` | stream generator raised; error frame still emitted |
| `AGENT_INTENT_PARSED` | `agents/graph.py` | routing result |
| `AGENT_INTENT_PARSE_FAILED` | `agents/graph.py` | fallback to small talk |
| `AGENT_TOOL_SEARCH` | `agents/tools.py` | search tool invoked |
| `AGENT_TOOL_GET_PRODUCT` | `agents/tools.py` | product tool invoked |
| `AGENT_TOOL_GET_RECOMMENDATIONS` | `agents/tools.py` | recommendations tool invoked |
| `AGENT_TOOL_FAILED` | `agents/graph.py` | typed scraper error captured by a tool node |
| `AGENT_RESPONSE_FAILED` | `agents/graph.py` | reply-phrasing LLM call raised |
| `API_ERROR` | exception handler | request-scoped error |
| `API_UNHANDLED_EXCEPTION` | catch-all handler | full stack trace logged |

Sensitive values such as API keys, full HTML, and user-identifying data are
never logged.

---

## 4. Firecrawl logging notes

`FIRECRAWL_*` log entries always include a `mode` field:

- `"markdown"` for `scrape()`
- `"json"` for `scrape_json()`

This makes it easy to distinguish the deterministic search path from the
product-detail extraction path in logs and traces.

---

## 5. Scrape store notes

- Every store interaction is logged with the corresponding cache key or
product id, so a hit vs. miss is trivially visible in a single log line.
- The store never logs payload contents -- only counts, sizes, and paths.
- A corrupt store file is treated as an empty store. The app logs
`SCRAPE_STORE_LOAD_FAILED` and continues; the next successful scrape
rewrites the file.

