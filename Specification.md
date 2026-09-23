# Daraz AI Shopping Assistant -- Backend Specification

## 1. Project Overview

Build a backend service for an AI-powered shopping assistant that searches
products from **Daraz.pk**, extracts product information using **Firecrawl**,
structures and validates the data, and exposes clean APIs for a future
chatbot/frontend.

The backend is developed independently from any frontend.

**Status:** Phases 1-9 complete. All endpoints (including streaming and
conversation memory) working end-to-end against live Daraz. Code pushed to
GitHub.

---

# 2. Primary Goals

The MVP backend is able to:

1. Search Daraz products using a natural or structured search query.
2. Scrape Daraz search-result pages using Firecrawl.
3. Convert scraped Markdown into validated structured product data.
4. Retrieve detailed information for an individual product.
5. Extract Daraz's recommended/similar products from the product page.
6. Expose all of the above through FastAPI.
7. Provide clean, predictable JSON responses suitable for a future Next.js frontend.
8. Support a conversational AI layer with memory and streaming on top of
   the deterministic services.
9. Persist successful scrape payloads to a local JSON file so repeat
   requests do not burn Firecrawl credits.

---

# 3. Non-Goals for MVP

Intentionally excluded from the first version:

- Vector database / embeddings.
- Qdrant.
- Redis.
- Complex background job infrastructure.
- User authentication.
- Payment functionality.
- Product purchasing.
- Price tracking.
- Notifications.
- Full review sentiment analysis.
- Multi-marketplace search.
- Frontend implementation.
- Database persistence (the local JSON scrape store is a persistence layer
  for scrape payloads and is explicitly in scope; it is not a database
  and does not cross the "database persistence" line).

These may be added later.

---

# 4. High-Level Architecture

```text
                         CLIENT
                           |
                           v
                       FastAPI
                           |
                           v
                    Service Layer
                           |
             +-------------+-------------+
             |                           |
             v                           v
       Search Service              Product Service
             |                           |
             +-------------+-------------+
                           |
             +-------------+-------------+
             |                           |
             v                           v
       ScrapeStore (JSON)           DarazScraper
       (read/write from                 |
        services only)                  v
                                   Firecrawl
                                        |
                                        v
                                     Daraz.pk
```

Chat layer (implemented, Phase 8-9):

```
                        Client
                          |
                          v
                 POST /api/v1/chat
                          |
                          v
                    ChatService  <---> MemorySaver (per conversation_id)
                          |
                          v
                       LangGraph
                          |
               +----------+----------+
               |          |          |
               v          v          v
            Search     Product    Recommend
             Tool       Tool        Tool
               |          |          |
               +----------+----------+
                          |
                          v
                    Daraz Services
                          |
                          v
                       Firecrawl
```

The graph's LLM is used **only** for intent parsing and reply phrasing. It
never sees raw HTML, never extracts product fields, and never bypasses the
service layer.

---

# 5. Technology Stack

## Backend

- Python 3.14
- FastAPI
- Pydantic / Pydantic Settings
- httpx (transitive via Firecrawl SDK)
- Firecrawl (`scrape` for Markdown, structured extraction for product pages)
- LangGraph (chat layer, Phases 8-9)

## Scraping

Primary: **Firecrawl**

Two extraction modes are used, split by page type (see Section 6.2 and
Section 40 / ADR-001):

- **Markdown mode** (`scrape(url) -> str`) for search-result pages. A
deterministic parser handles the content afterwards.
- **Structured extraction mode** (`scrape_json(url, schema=...) -> dict`)
for product detail pages. Firecrawl's LLM reads the page and returns
data matching a JSON Schema, which is then validated through Pydantic.

Potential fallback: **Playwright** -- installed but NOT used.

## Persistence

- **Local JSON file** (`data/scrape_store.json`) for scrape payloads.
- **In-memory LangGraph checkpointer** for conversation state.
- No database for the MVP. PostgreSQL remains a future option, gated on
explicit request and documented as an architectural decision at that time.

---

# 6. Core Design Principles

## 6.1 Separation of Responsibilities

The system separates:

```
AI reasoning
    !=
scraping
    !=
data normalization
    !=
local persistence
    !=
API layer
```

The LLM never controls the scraping process. It is responsible for:

- understanding user intent,
- extracting search requirements,
- phrasing the final reply to the user.

Firecrawl obtains webpage content. Application code performs deterministic
parsing, validation, normalization, persistence, and API responses.

## 6.2 Extraction Strategy by Page Type

Two distinct extraction pipelines exist, chosen by page type:

| Page type ↕▾ | Firecrawl call ↕▾ | Extraction ↕▾ | Validated by ↕▾ |
|---|---|---|---|
| −Search results (`/catalog/?q=...`) | `scrape(url)` -> Markdown | Deterministic parser (regex + string logic) | `Product.model_validate` |
| Product detail (`/products/...`) | `scrape_json(url, schema=...)` -> dict | Firecrawl structured extraction (LLM) | `ProductDetails.model_validate` |
⚙

**Rules:**

- Never call `scrape_json` on a search result page.
- Never call `scrape` (Markdown) on a product detail page and feed it to
a regex parser.
- Never skip Pydantic validation on structured-extraction output.

---

# 7. Data Flow -- Search

```
User Query
   |
   v
Search Service
   |
   v
Build cache key (query + filters + page)
   |
   v
ScrapeStore.get(key)  -- hit? -> SearchResult.model_validate -> return
   |
   v  (miss)
Build Daraz Search URL
   |
   v
Firecrawl (Markdown mode)
   |
   v
Daraz Search Page
   |
   v
Markdown / Scraped Content
   |
   v
Parser
   |
   v
Product objects (untrusted dicts)
   |
   v
Pydantic Validation
   |
   v
ScrapeStore.set(key, ttl=search_ttl)  -- only on success
   |
   v
Search Response
```

Example:

```
Input:  Gaming Mouse
Filter: Maximum price = Rs. 800

Generated Daraz URL:
https://www.daraz.pk/catalog/?q=Gaming%20Mouse&price=-800
```

---

# 7.1 Data Flow -- Product Detail

```
Product ID
   |
   v
Build cache key (product_id)
   |
   v
ScrapeStore.get(key)  -- hit? -> ProductDetails.model_validate -> return
   |
   v  (miss)
Build Daraz Product URL
   |
   v
Firecrawl (structured extraction mode)
   |
   +--- JSON Schema from ProductDetails.model_json_schema()
   +--- Prompt: section-by-section extraction instructions
   |
   v
Daraz Product Page
   |
   v
Structured JSON payload
   |
   v
Pydantic Validation (ProductDetails.model_validate)
   |
   v
ScrapeStore.set(key, ttl=product_ttl)
   |
   v
Product Details Response
```

Product-page rendering options applied during the scrape:

- `wait_for_ms=5000` -- lets lazily-rendered sections appear.
- `only_main_content=False` -- stops Firecrawl from classifying the
sections we need as non-main and dropping them.

Validation failure handling:

- Log `PRODUCT_VALIDATION_FAILED` with the offending payload shape (never
the raw content).
- Raise `ParseError(source="product")` to the service layer.
- Never surface the raw LLM payload to the API consumer.

---

# 8. Search Result Schema

A search response follows this structure:

```
{
  "source": "Daraz.pk",
  "search_query": "Gaming Mouse",
  "filters": {
    "min_price": null,
    "max_price": 800
  },
  "total_items_found": 11084,
  "scraped_at": "2026-09-15T10:49:06+05:00",
  "products": [],
  "pagination": {
    "current_page": 1,
    "total_pages": 102,
    "items_per_page": 40
  }
}
```

---

# 9. Product Schema

The lightweight product object:

```
{
  "id": "i1959941878",
  "title": "RGB Gaming Mouse...",
  "url": "https://www.daraz.pk/products/7-i1959941878.html",
  "image": "https://img.drz.lazcdn.com/...",
  "price": 579,
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

---

# 10. Product Field Requirements

| Field ↕▾ | Type ↕▾ | Required ↕▾ | Notes ↕▾ |
|---|---|---|---|
| −`id` | string | Yes | Daraz product identifier (e.g. `i1959941878`) |
| `title` | string | Yes | Product title |
| `url` | string | Yes | Original Daraz product URL |
| `image` | string/null | No | Product image URL |
| `price` | number | Yes | Numeric value only |
| `currency` | string | Yes | Always `PKR` for Daraz.pk |
| `original_price` | number/null | No | Original price if available |
| `discount_percentage` | number/null | No | Numeric percentage (0-100) |
| `coins_save` | number/null | No | Numeric PKR value |
| `sold_count` | number/null | No | Number sold |
| `rating` | number/null | No | Star rating (0-5) |
| `rating_count` | number/null | No | Number of ratings/reviews |
| `location` | string/null | No | Seller/product location |
⚙

---

# 11. Data Type Rules

Do not preserve presentation formatting in numeric fields.

Bad:

```
{
  "price": "Rs. 579",
  "discount": "27% Off",
  "coins_save": "Rs. 29"
}
```

Good:

```
{
  "price": 579,
  "currency": "PKR",
  "discount_percentage": 27,
  "coins_save": 29
}
```

The backend normalizes values before returning them.

---

# 12. Missing Data Rules

Missing information is `null`. Never allow the LLM to invent missing
information.

The structured-extraction prompt instructs the model to **omit** fields
rather than guess. Missing fields default to `None` or empty via Pydantic
model defaults.

---

# 13. Search Product vs Detailed Product

## Search Product

Used for catalog/search results. Contains:

```
id, title, url, image, price, currency,
original_price, discount_percentage, coins_save,
sold_count, rating, rating_count, location
```

## Product Details

Contains all search-product fields plus:

```
description, specifications, seller, shipping,
availability, variants, reviews
```

---

# 14. Detailed Product Schema

```
{
  "id": "i1959941878",
  "title": "RGB Gaming Mouse...",
  "url": "https://www.daraz.pk/products/...",
  "image": "...",
  "price": 579,
  "currency": "PKR",
  "original_price": null,
  "discount_percentage": 27,
  "coins_save": 29,
  "sold_count": 184,
  "rating": null,
  "rating_count": 40,
  "location": "Punjab",
  "description": null,
  "specifications": {},
  "seller": { "name": null, "rating": null, "positive_rate": null },
  "shipping": { "fee": null, "free_shipping": null, "estimated_delivery": null },
  "availability": null,
  "variants": [],
  "reviews": [],
}
```

Fields are populated only when the information is available.

**Extraction note:** `ProductDetails` is produced via Firecrawl structured
extraction, not Markdown parsing. See Section 40 / ADR-001.

## 14.1 Known Limitations of Product-Detail Extraction

Three sections are unreliable on the current Daraz product-page layout and
are documented here rather than chased indefinitely:

- **`specifications`** may return `{}` even when the "Specifications of"
heading is present in Firecrawl's Markdown snapshot. Treat an empty
`specifications` object as "not available" rather than "not rendered".
- **`rating`** on search-result products is always `null`. Daraz renders
stars as images, which Markdown strips. `rating_count` is present and
correct.

All other fields (`id`, `title`, `url`, `image`, `price`, `currency`,
`original_price`, `discount_percentage`, `sold_count`, `rating_count`,
`location`, `description`, `seller`, `shipping`, `availability`, `reviews`,
`variants`) are extracted reliably on the product pages tested so far.

The service-layer `_normalise_product_id` helper repairs one known LLM
quirk: the model occasionally strips the `i` prefix from the `id` field.

---

# 17. LLM Usage

> **Amendment (ADR-001):** Search-result pages use deterministic Markdown
> parsing only. Product detail pages use Firecrawl structured extraction
> (LLM + JSON Schema) validated through Pydantic.

The LLM is NOT responsible for blindly converting the entire webpage into
JSON. This rule stands for search-result pages and any page whose layout is
uniform enough for a regex parser. It is deliberately lifted for product
detail pages.

The LLM may be used for:

- query understanding,
- product-title normalization,
- feature extraction,
- category classification,
- semantic interpretation,
- whole-page structured extraction on product detail pages only,
- intent classification and reply phrasing in the chat layer (Section 41).

The LLM must not fabricate:

- prices,
- product IDs,
- URLs,
- ratings,
- review counts,
- seller information.

---

# 18. Future Search Query Schema

Natural-language queries are eventually transformed into a structured
object:

```
{
  "query": "gaming mouse",
  "category": "gaming mouse",
  "min_price": null,
  "max_price": 5000,
  "brand": null,
  "features": ["wireless", "RGB"],
  "sort": null
}
```

Currently the chat layer parses to a simpler `ParsedIntent` (Section 41).
This richer schema is a future extension.

---

# 19. Scraper Interface

The scraping layer exposes a stable interface:

```
class DarazScraper:
    async def fetch_search_markdown(...) -> str
    async def fetch_product_payload(...) -> dict[str, Any]
```

The rest of the backend depends on this interface, never directly on
Firecrawl.

---

# 20. Firecrawl Adapter

Firecrawl is hidden behind the scraper abstraction:

```
Application
    |
    v
DarazScraper
    |
    v
FirecrawlAdapter
    |
    +--- scrape(url) -> str                       (Markdown mode)
    |
    +--- scrape_json(url, schema, prompt) -> dict (structured extraction)
    |
    v
Firecrawl API
```

Both methods share identical retry semantics, exception classification, and
structured logging (`FIRECRAWL_REQUEST`, `FIRECRAWL_RESPONSE`,
`FIRECRAWL_RETRY`, each with a `mode` field of `"markdown"` or `"json"`).

Current Firecrawl JSON format shape:

```
formats=[{"type": "json", "prompt": "...", "schema": {...}}]
```

---

# 21. Project Structure

The project uses a **`src/` layout** managed by `uv`. The package name is
`daraz_ai_shopping_assistant`.

```
daraz-ai-shopping-assistant/
|
+-- src/
|   +-- daraz_ai_shopping_assistant/
|       +-- __init__.py
|       +-- main.py                  # FastAPI app entry point
|       |
|       +-- api/
|       |   +-- __init__.py          # api_router; mounts sub-routers
|       |   +-- deps.py              # dependency providers
|       |   +-- exception_handlers.py
|       |   +-- search.py
|       |   +-- products.py
|       |   +-- chat.py              # /chat and /chat/stream
|       |
|       +-- models/
|       |   +-- __init__.py
|       |   +-- product.py
|       |   +-- search.py
|       |
|       +-- schemas/
|       |   +-- __init__.py
|       |   +-- product.py
|       |   +-- search.py
|       |   +-- chat.py
|       |
|       +-- services/
|       |   +-- __init__.py          # re-exports search + product (NOT chat)
|       |   +-- search_service.py
|       |   +-- product_service.py
|       |   +-- chat_service.py      # facade over the LangGraph agent
|       |
|       +-- scrapers/
|       |   +-- __init__.py
|       |   +-- base.py              # abstract DarazScraper
|       |   +-- daraz.py             # FirecrawlDarazScraper, URL builders
|       |   +-- firecrawl.py         # ONLY importer of `firecrawl`
|       |
|       +-- parsers/
|       |   +-- __init__.py
|       |   +-- search_parser.py     # pure function: Markdown -> dict
|       |
|       +-- agents/
|       |   +-- __init__.py
|       |   +-- state.py             # AgentState, ParsedIntent, IntentType
|       |   +-- tools.py             # thin wrappers around services
|       |   +-- graph.py             # StateGraph, build_graph, get_compiled_graph
|       |
|       +-- storage/
|       |   +-- __init__.py
|       |   +-- json_store.py        # ScrapeStore -- local JSON persistence
|       |
|       +-- core/
|       |   +-- __init__.py
|       |   +-- config.py
|       |   +-- exceptions.py
|       |   +-- logging.py
|       |
|       +-- utils/
|           +-- __init__.py
|           +-- datetime.py          # PKT timezone helpers
|
+-- tests/
|   +-- __init__.py
|   +-- conftest.py
|   +-- unit/
|   +-- api/
|   +-- integration/
|   +-- fixtures/
|
+-- scripts/
+-- docs/
+-- data/                             # gitignored; created at runtime
+-- .venv/                            # managed by uv (not committed)
+-- .gitignore
+-- .python-version
+-- pyproject.toml
+-- uv.lock
+-- README.md
+-- Specification.md
+-- AGENTS.md
+-- .env.example
```

## 21.1 Notes on the Structure

- `src/` layout enforced by `uv` and `pyproject.toml`.
- The package is `daraz_ai_shopping_assistant`. There is no `app/` folder.
- `services/__init__.py` deliberately does NOT re-export `ChatService` to
avoid a circular import.
- `api/__init__.py` registers `search_router` BEFORE `products_router`.
- `firecrawl.py` is the only module permitted to import `firecrawl`.
- `storage/` is a leaf. It is called only by the service layer.
- `core/config.py` loads settings from `.env` via pydantic-settings.

---

# 22. Search Products Endpoint

```
GET /api/v1/products/search
```

Parameters:

```
q           (required, string, 1-200 chars)
min_price   (optional, float, >= 0)
max_price   (optional, float, >= 0)
page        (optional, int, >= 1, <= 200, default 1)
```

Response shape: `SearchResult` (Section 8).

---

# 23. Get Product Endpoint

```
GET /api/v1/products/{product_id}
```

Path parameter `product_id` must match `^i\d+$`.

Response shape: `ProductDetails` (Section 14).

---

# 25. Chat Endpoint

```
POST /api/v1/chat
```

Request body:

```
{
  "message": "Find me a gaming mouse under Rs. 5000",
  "conversation_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7"
}
```

`conversation_id` is optional. Omit it on the first turn; the server
generates one and returns it in the response. Send it back on subsequent
turns to continue the same conversation.

Response body:

```
{
  "reply": "Here are some gaming mice under Rs. 5,000...",
  "conversation_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
  "intent": "search",
  "data": { "products": [ ... ], ... },
  "error": null
}
```

Response fields:

| Field ↕▾ | Meaning ↕▾ |
|---|---|
| −`reply` | Assistant's natural-language reply (always present) |
| −`conversation_id` | Echo for the next turn (always present) |
| `intent` | `search`, `get_product`, `small_talk` |
| `data` | Raw tool result, shape depends on intent |
| `error` | Non-null only when a tool failed |
⚙

Tools call services, never scrapers or Firecrawl directly. The graph
absorbs `DarazScraperError` and reports it on the `error` field.

---

# 26. Search Pagination

Daraz may report a very large number of total products (e.g. 11,084
products across 102 pages).

The backend does NOT scrape the entire catalog by default.

---

# 27. Product Deduplication

Products retrieved from multiple queries or pages may contain duplicates.
The canonical deduplication key is the **Daraz product ID**.

---

# 28. Search URL Builder

Daraz-specific URL syntax is isolated inside `scrapers/daraz.py`:

```
def build_search_url(query, *, min_price=None, max_price=None, page=None) -> str
def build_product_url(product_id) -> str
```

Daraz price parameter syntax:

```
-800        # max only
100-        # min only
100-800     # both bounds
```

---

# 29. Error Handling

The backend handles:

- Firecrawl API errors
- Daraz page unavailable
- timeout
- malformed Markdown
- missing product data
- invalid product URL
- parser failure
- structured extraction returning malformed JSON
- structured extraction returning data that fails Pydantic validation
- rate limits
- unexpected Daraz page changes

HTTP status mapping:

```
400 -> invalid request  (InvalidRequestError)
404 -> product not found (ProductNotFoundError)
429 -> rate limited     (UpstreamRateLimitError)
502 -> upstream scraping or parsing failure (ScraperError, ParseError)
504 -> upstream timeout (ScraperTimeoutError)
500 -> internal server error (DarazScraperError base, unhandled)
```

Do not expose internal stack traces to API consumers.

---

# 30. Logging

Significant lifecycle events are logged with structured context. Full list
in `docs/ERRORS-AND-LOGGING.md` Section 3.

Never log secrets, API keys, full HTML dumps, or full store payloads.

---

# 31. Environment Variables

Required (in `.env`):

```
FIRECRAWL_API_KEY=fc-...        # required for all scraping
GOOGLE_API_KEY=...              # required only for the chat endpoints
```

Optional:

```
APP_NAME=Daraz AI Shopping Assistant
APP_ENV=dev
APP_DEBUG=false
API_V1_PREFIX=/api/v1
API_HOST=0.0.0.0
API_PORT=8000
FIRECRAWL_BASE_URL=https://api.firecrawl.dev
FIRECRAWL_TIMEOUT_SECONDS=30
FIRECRAWL_MAX_RETRIES=2
LLM_MODEL=gemini-2.5-flash
LLM_TEMPERATURE=0.0
DARAZ_BASE_URL=https://www.daraz.pk
DARAZ_SEARCH_PATH=/catalog/
DARAZ_DEFAULT_CURRENCY=PKR
DARAZ_ITEMS_PER_PAGE=40
SCRAPER_DEFAULT_PAGE=1
SCRAPE_STORE_PATH=data/scrape_store.json
SCRAPE_STORE_SEARCH_TTL_SECONDS=21600
SCRAPE_STORE_PRODUCT_TTL_SECONDS=86400
LOG_LEVEL=INFO
LOG_FORMAT=console
```

`.env.example` at the repo root documents every variable.

`GOOGLE_API_KEY` is optional at import time. It is validated lazily by the
chat service when the endpoint is called without a configured key.

---

# 32. Testing Strategy

## Unit Tests

Cover:

- price parsing, discount parsing, sold-count abbreviation (`8.1K`)
- product ID extraction
- URL extraction and URL builders
- pagination extraction
- Markdown parsing block splitting
- Pydantic validation for every model
- FirecrawlAdapter in both modes (Markdown + JSON), fully mocked
- `SearchService` and `ProductService` orchestration with mocked scrapers
- `ScrapeStore` load, get, set, prune, corrupt-file tolerance, atomicity
- agent state schema, tool functions, graph routing, chat service

## Integration Tests

Reserved for end-to-end flows hitting real Daraz via Firecrawl. Marked
`@pytest.mark.integration` and excluded from CI by default.

## API Tests

Cover every HTTP endpoint using FastAPI's `TestClient` with dependency
overrides:

```
GET  /api/v1/products/search
GET  /api/v1/products/{id}
POST /api/v1/chat
POST /api/v1/chat/stream
```

The LLM is never called in tests.

## Current coverage

All unit + API tests pass. Ruff clean. Mypy clean.

---

# 33. Development Order -- Status

## Phase 1 -- Data Models

Pydantic models. **Done.**

## Phase 2 -- Firecrawl Integration

`FirecrawlAdapter`. **Done.**

## Phase 3 -- Search Parser

`parse_search_results`. **Done.**

## Phase 4 -- Daraz Search Service

`DarazScraper`, `FirecrawlDarazScraper`, URL builders, `SearchService`. **Done.**

## Phase 5 -- FastAPI Search Endpoint

`GET /api/v1/products/search`. **Done.**

## Phase 6 -- Product Page Extraction

`ProductService.get_product()`. **Done.**

## Phase 8 -- AI Layer

LangGraph agent, `ChatService`, `POST /api/v1/chat`. **Done.**

## Phase 9 -- Memory, Streaming, Storage

Conversation memory (`MemorySaver`), Server-Sent Events streaming
(`/api/v1/chat/stream`), and local JSON scrape store. **Done.**

---

# 34. Definition of Done -- MVP

The MVP is successful when:

- `GET /api/v1/products/search` returns a validated `SearchResult`.
- `GET /api/v1/products/{id}` returns a validated `ProductDetails`.
- `POST /api/v1/chat` routes correctly and returns a non-fabricated reply.
- `POST /api/v1/chat/stream` emits tokens incrementally over SSE.
- A repeat request for the same search or product key hits the local
store and does not call Firecrawl.

**Status:** All flows verified against live Daraz.

---

# 35. Future Vision

Natural-language shopping:

```
User: "I need a wireless gaming mouse under Rs. 5,000 with RGB."
```

The agent should:

```
Understand request
      |
      v
Create search parameters
      |
      v
Search Daraz
      |
      v
Extract products
      |
      v
Filter/rank results
      |
      v
Display products
      |
      v
Allow user to inspect product
      |
      v
Continue conversation
```

Examples of future interactions the architecture supports:

- "Show me cheaper ones."
- "Only wireless products."
- "Tell me more about the second product."
- "Compare these three."

The current implementation covers search, product-detail, chat with memory,
streaming, and local scrape caching. Multi-turn
filtering and comparison are extensions that do not require rewriting the
scraping layer.

---

# 36. Current Installed Dependencies

Managed with **uv**.

## 36.1 Runtime

| Package ↕▾ | Purpose ↕▾ |
|---|---|
| −`fastapi` | API framework |
| −`uvicorn[standard]` | ASGI server with reload + uvloop |
| −`pydantic` | Data models & validation |
| −`pydantic-settings` | `.env`-driven config |
| `python-dotenv` | Load `.env` |
| `httpx` | Async HTTP client |
| `firecrawl-py` | Official Firecrawl SDK |
⚙

## 36.2 Chat Layer

| Package ↕▾ | Purpose ↕▾ |
|---|---|
| −`langgraph` | Agent graph runtime |
| −`langchain-core` | Core LLM abstractions |
| −`langchain-google-genai` | Gemini provider |
⚙

## 36.3 Dev / Tooling

| Package ↕▾ | Purpose ↕▾ |
|---|---|
| −`pytest` | Test runner |
| −`pytest-asyncio` | Async test support |
| `pytest-cov` | Coverage reports |
| `ruff` | Linter + formatter |
| `mypy` | Static type checking |
| `pre-commit` | Git hooks |
| `playwright` | Installed but NOT used. See Section 5. |
⚙

## 36.4 Rules

- Do not add a new dependency without asking the user first.
- Do not use Playwright unless the user explicitly requests it.
- Do not add Redis, Qdrant, Postgres, or background job libraries.

---

# 37. uv Commands Reference

```
uv init
uv python pin 3.14
uv add fastapi "uvicorn[standard]" pydantic pydantic-settings \
       python-dotenv httpx firecrawl-py
uv add langgraph langchain-core langchain-google-genai
uv add --dev pytest pytest-asyncio pytest-cov ruff mypy pre-commit
uv sync
uv run uvicorn daraz_ai_shopping_assistant.main:app --reload
uv run pytest
uv run ruff check .
uv run mypy src
uv run python scripts/generate_fixtures.py
uv run python scripts/diagnose_search.py --query "gaming mouse"
uv run python scripts/diagnose_product_page.py --product-id i927677133
```

---

# 38. Package Entry Point

The FastAPI app is referenced as:

```
daraz_ai_shopping_assistant.main:app
```

The `main.py` file lives at
`src/daraz_ai_shopping_assistant/main.py`.

`main.py` exposes:

- `create_app()` -- factory used by tests and by production.
- `app` -- module-level instance uvicorn imports.

Tests call `create_app()` to get isolated instances with their own
dependency overrides.

---

# 39. Environment and Version Notes

- **Python:** 3.14 (pinned via `.python-version`)
- **Package manager:** `uv`
- **Layout:** `src/` layout -- package name `daraz_ai_shopping_assistant`
- **Virtual environment:** `.venv/` at repository root

All commands must be run through `uv run ...`.

---

# 40. Architecture Decision Records

Binding decisions that shape the codebase are recorded here. The full text
of each ADR lives in `docs/ARCHITECTURE-DECISIONS.md`.

## ADR-001 -- Search pages use deterministic parsing; product pages use structured extraction

**Status:** Accepted (2026-09-15)

**Decision:** Split extraction strategy by page type.

| Page type ↕▾ | Adapter method ↕▾ | Extraction ↕▾ |
|---|---|---|
| −Search results (`/catalog/?q=...`) | `scrape(url)` -> Markdown | Deterministic parser |
| Product detail (`/products/...`) | `scrape_json(url, schema=...)` -> dict | Firecrawl structured extraction |
⚙

**Forbidden:**

- Calling `scrape_json` on a search result page.
- Calling `scrape` (Markdown) on a product detail page and feeding it to a
regex parser.
- Skipping Pydantic validation on structured-extraction output.

## ADR-002 -- The chat-layer LLM classifies intent and phrases replies only

**Status:** Accepted (2026-09-16)

**Decision:** In the LangGraph chat layer, the LLM is used for exactly two
things:

1. **Intent parsing.** Produce a structured `ParsedIntent`.
2. **Reply phrasing.** Produce a plain-language reply from the tool result.

The LLM does **not** extract product data, see raw HTML, call Firecrawl, or
bypass the service layer.

**Forbidden:**

- Letting the LLM choose which tool to call based on raw text without
structured output.
- Letting the LLM see or paraphrase raw Firecrawl Markdown or HTML.
- Letting the LLM construct product URLs, IDs, or prices.
- Skipping Pydantic validation on tool results that flow back through the
graph.

---

# 41. Chat Layer

The chat layer lives in `src/daraz_ai_shopping_assistant/agents/` and is
exposed through `services/chat_service.py` and the chat endpoints. It sits
**on top of** the same validated backend services that back the REST
endpoints. It does not replace, bypass, or duplicate them.

The LLM in this layer does exactly two things (ADR-002):

1. Classify the user's intent into a structured `ParsedIntent`.
2. Phrase a plain-language reply from the tool result.

It never sees raw HTML or Markdown, never extracts product fields, and
never invents values.

## 41.1 Graph Pipeline

The graph is a LangGraph `StateGraph` compiled once per process.

```
              START
                |
                v
         +--------------+
         | parse_intent |   LLM.with_structured_output(ParsedIntent)
         +--------------+
                |
       conditional edge on intent.intent
                |
     +----------+----------+-----------------+
     |          |          |                 |
     v          v          v                 v
   search   get_product  respond
     |          |          |                 |
     +----------+----------+-----------------+
                |
                v
             respond  (LLM phrases the reply)
                |
                v
               END
```

- `parse_intent` calls the LLM with a system prompt listing the four
intents and the parameters each requires. Failure or malformed output
falls back to `IntentType.SMALL_TALK`.
- Tool nodes call the functions in `agents/tools.py`. A typed
`DarazScraperError` is caught, logged as `AGENT_TOOL_FAILED`, and
recorded on `AgentState.error`.
- `respond` builds a prompt from the user's question, the parsed intent,
any error, and a truncated JSON dump of the tool result.

## 41.2 Tools

`agents/tools.py` exposes three async functions:

| Tool ↕▾ | Service call ↕▾ | Returns ↕▾ |
|---|---|---|
| −`search_products_tool(...)` | `SearchService.search` | `SearchResult` as JSON dict |
| −`get_product_tool(product_id)` | `ProductService.get_product` | `ProductDetails` as JSON dict |
⚙

Tools call **services**, never scrapers and never Firecrawl directly.

## 41.3 ParsedIntent

```
class IntentType(StrEnum):
    SEARCH = "search"
    GET_PRODUCT = "get_product"
    SMALL_TALK = "small_talk"

class ParsedIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: IntentType
    query: str | None = None
    product_id: str | None = None
    min_price: float | None = Field(default=None, ge=0.0)
    max_price: float | None = Field(default=None, ge=0.0)
    page: int = Field(default=1, ge=1)
```

`ParsedIntent` is deliberately narrower than the future search-query
schema sketched in Section 18.

## 41.4 ChatService

`services/chat_service.py` wraps the compiled graph behind a
service-layer interface. It is the only place that:

- builds the initial `AgentState` from a user message,
- resolves the conversation id,
- extracts the final reply from the graph state,
- shapes the response envelope (`ChatResponse`),
- exposes a streaming variant.

Error-handling policy:

- A `DarazScraperError` raised inside a tool node is absorbed by the graph
and surfaces on `AgentState.error`. `ChatService` forwards the `error`
string to the response.
- Unhandled exceptions from the LLM (bad API key, network failure)
propagate to FastAPI's global exception handlers and become a generic
500.

`ChatService.chat()` logs `CHAT_STARTED` at entry and `CHAT_COMPLETED` at
exit, including `intent`, `has_data`, `recommended_count`, `has_error`,
and `duration_ms`.

---

# 42. Streaming, Conversation Memory, and Local Store

This section describes the Phase 9 additions.

## 42.1 Streaming endpoint

```
POST /api/v1/chat/stream
```

Request body: same as `POST /api/v1/chat`.

Response: `Content-Type: text/event-stream`. Each event is one
`data: <json>` line followed by a blank line.

Event types:

- `{"type": "token", "text": "..."}` -- one per LLM token as it is
generated.
- `{"type": "done", "conversation_id": ..., "intent": ..., "error": ...}` -- once, after the stream completes.

Terminal sentinel:

```
data: [DONE]
```

Only tokens from the reply-phrasing LLM call are streamed. The
intent-parsing call is filtered out by `metadata.langgraph_node == "respond"`.

If an internal error occurs during streaming:

```
data: {"type": "error", "message": "Internal server error."}
data: [DONE]
```

## 42.2 Conversation memory

The graph is compiled with a LangGraph checkpointer (`MemorySaver` in
production, injected via `warm_compiled_graph()` from the app lifespan).

- State is keyed by `thread_id`, which the service sets to the resolved
`conversation_id`.
- If the client omits `conversation_id`, the server generates a UUID and
returns it in the response.
- A new process starts with an empty checkpointer. Conversations do NOT
survive a restart.
- The checkpointer holds the full history; the LLM only sees the last 16
messages (8 turns) via `trim_messages(strategy="last", start_on="human")`.
- Both `parse_intent` and `respond` see the trimmed history, so follow-up
turns resolve correctly.

## 42.4 Local scrape store

Successful search and product-detail scrapes are written to a JSON file
on disk (`data/scrape_store.json` by default).

Key format:

- Search: `search:{query}|{min_price}|{max_price}|{page}` (`~` for missing
bounds).
- Product: `product:{product_id}`.

Default TTLs: 6 hours for search, 24 hours for product. Configurable via
`SCRAPE_STORE_SEARCH_TTL_SECONDS` and `SCRAPE_STORE_PRODUCT_TTL_SECONDS`.

Behaviour:

- Loaded once at app startup and attached to the service singletons.
- Read path: `get(key)` returns the payload if not expired.
- Write path: `set(key, ...)` writes the entire file atomically
(`tempfile` + `os.replace`).
- Expired entries are pruned at load and again on shutdown.
- A missing, corrupt, or empty file is treated as an empty store. The app
never crashes on bad persisted state.
- No eviction policy. A one-shot warning is logged at 10 MB.
- Only the service layer touches the store. Routes, scrapers, parsers, and
agents must never import `storage/`.

The store is explicitly NOT a database and NOT a caching layer in the sense
that Non-Goal Section 3 forbids. It is local durability for scrape payloads
and is bounded by TTL.

---

End of Specification.
