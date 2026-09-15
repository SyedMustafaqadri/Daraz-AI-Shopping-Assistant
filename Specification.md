# Daraz AI Shopping Assistant — Backend Specification

## 1. Project Overview

Build a backend service for an AI-powered shopping assistant that searches products from **Daraz.pk**, extracts product information using **Firecrawl**, structures and validates the data, and exposes clean APIs for a future chatbot/frontend.

The initial system will **not implement its own recommendation algorithm**.

When available on Daraz product pages, the system will extract **Daraz's own recommended/similar products** and return them through the backend.

The backend will initially be developed independently from the frontend.

---

# 2. Primary Goals

The MVP backend must be able to:

1. Search Daraz products using a natural or structured search query.
2. Scrape Daraz search-result pages using Firecrawl.
3. Convert scraped Markdown/content into validated structured product data.
4. Retrieve detailed information for an individual product.
5. Extract Daraz's recommended/similar products from the product page.
6. Expose all of the above through FastAPI.
7. Provide clean, predictable JSON responses suitable for a future Next.js frontend.
8. Support an AI/agent layer later without changing the underlying scraping services.

---

# 3. Non-Goals for MVP

The following are intentionally excluded from the first version:

- Custom product recommendation algorithms.
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
       Daraz Search Service       Product Service
             |                           |
             +-------------+-------------+
                           |
                           v
                    DarazScraper
                           |
                           v
                       Firecrawl
                           |
                           v
                        Daraz.pk
```

Future AI layer:

```
                        Client
                          |
                          v
                    POST /api/v1/chat
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

---

# 5. Technology Stack

## Backend

- Python 3.14
- FastAPI
- Pydantic / Pydantic Settings
- HTTP client as required by Firecrawl SDK/API
- Firecrawl (both `scrape` for Markdown and `scrape` with a JSON schema for structured extraction — see ADR-001)
- LangGraph (future AI layer)

## Scraping

Primary:

- Firecrawl

Two extraction modes are used, split by page type (see §17 and §40 / ADR-001):

- **Markdown mode** (`scrape(url) → str`) — for search-result pages. Deterministic parsing follows.
- **Structured extraction mode** (`scrape_json(url, schema=...) → dict`) — for product detail pages. An LLM reads the page and returns data matching a JSON Schema, which is then validated through Pydantic.

Potential fallback:

- Playwright

Playwright should not be introduced unless Firecrawl proves insufficient for a required workflow.

## Database

No database is required for the first scraping/API prototype.

When persistence is introduced:

- PostgreSQL

---

# 6. Core Design Principles

## 6.1 Separation of Responsibilities

The system must separate:

```
AI reasoning
    !=
scraping
    !=
data normalization
    !=
API layer
```

The LLM should not directly control the complete scraping process.

The LLM should mainly be responsible for:

- understanding user intent,
- extracting search requirements,
- interpreting messy product information when necessary,
- calling backend tools.

Firecrawl should be responsible for obtaining webpage content.

Application code should be responsible for deterministic parsing, validation, normalization, and API responses.

## 6.2 Extraction Strategy by Page Type

Two distinct extraction pipelines exist, chosen by page type, not by convenience:

| Page type ↕▾ | Firecrawl call ↕▾ | Extraction ↕▾ | Validated by ↕▾ |
|---|---|---|---|
| −Search results (`/catalog/?q=...`) | `scrape(url)` → Markdown | Deterministic parser (regex + string logic) | `Product.model_validate` |
| Product detail (`/products/...`) | `scrape_json(url, schema=...)` → dict | Firecrawl structured extraction (LLM) | `ProductDetails.model_validate` |
| Recommendations (embedded in product page) | `scrape_json(url, schema=...)` → dict | Same as product detail, nested schema | `Recommendation.model_validate` |
⚙

**Rules:**

- Never call `scrape_json` on a search result page.
- Never call `scrape` (Markdown) on a product detail page and feed it to a regex parser.
- Never skip Pydantic validation on the structured-extraction output — the schema is a suggestion, the model is the contract.

See §17 (LLM Usage) and §40 (ADRs) for the full rationale.

---

# 7. Data Flow — Search

```
User Query
   |
   v
Search Service
   |
   v
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
Product objects
   |
   v
Pydantic Validation
   |
   v
Search Response
```

Example:

```
Input:
Gaming Mouse

Filter:
Maximum price = Rs. 800

Generated Daraz URL:
https://www.daraz.pk/catalog/?q=Gaming%20Mouse&price=-800
```

---

# 7.1 Data Flow — Product Detail

```
Product ID
   |
   v
Build Daraz Product URL
   |
   v
Firecrawl (structured extraction mode)
   |
   +--- JSON Schema from ProductDetails.model_json_schema()
   +--- Prompt: "Extract product info; omit missing fields"
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
Product Details Response
```

Validation failure handling:

- Log `PRODUCT_VALIDATION_FAILED` with the offending payload.
- Raise `ParseError(source="product")` to the service layer.
- Never surface the raw LLM payload to the API consumer.

---

# 8. Search Result Schema

A search response must follow this general structure:

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

The core lightweight product object should contain:

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
| −`id` | string | Yes | Daraz product identifier |
| −`title` | string | Yes | Product title |
| `url` | string | Yes | Original Daraz product URL |
| `image` | string/null | No | Product image URL |
| `price` | number | Yes | Numeric value only |
| `currency` | string | Yes | Use `PKR` |
| `original_price` | number/null | No | Original price if available |
| `discount_percentage` | number/null | No | Numeric percentage |
| `coins_save` | number/null | No | Numeric PKR value |
| `sold_count` | number/null | No | Number sold |
| `rating` | number/null | No | Actual star rating |
| `rating_count` | number/null | No | Number of ratings/reviews |
| `location` | string/null | No | Seller/product location |
⚙

---

# 11. Data Type Rules

Do not preserve presentation formatting in core numeric fields.

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

The backend should normalize values before returning them.

For structured-extraction output (product pages), the JSON Schema passed to Firecrawl should already declare numeric types for these fields. If the LLM still returns a formatted string, `ProductDetails.model_validate` will reject it (Pydantic does not coerce `"Rs. 579"` to `float`) — this is intentional: it forces the prompt to be tightened rather than silently corrupting data.

---

# 12. Missing Data Rules

Missing information must be represented as:

```
null
```

Never allow the LLM to invent missing information.

Example:

```
{
  "rating": null,
  "image": null
}
```

is valid.

The following is invalid:

```
{
  "rating": 4.6
}
```

when no rating was actually present in the scraped data.

The structured-extraction prompt must instruct the model to **omit** rather than guess. Missing fields default to `None` / empty via the Pydantic model defaults.

---

# 13. Search Product vs Detailed Product

The system must distinguish between lightweight search results and full product details.

## Search Product

Used for catalog/search results.

Contains primarily:

```
id
title
url
image
price
discount
sold
rating
rating_count
location
```

## Product Details

Contains the search-product fields plus:

```
description
specifications
seller
shipping
availability
variants
reviews
recommendations
```

---

# 14. Detailed Product Schema

Example:

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

  "seller": {
    "name": null,
    "rating": null,
    "positive_rate": null
  },

  "shipping": {
    "fee": null,
    "free_shipping": null,
    "estimated_delivery": null
  },

  "availability": null,

  "variants": [],

  "reviews": [],

  "recommendations": []
}
```

Fields should be populated only when the information is actually available.

**Extraction note:** `ProductDetails` is produced via Firecrawl structured extraction, not Markdown parsing. The JSON Schema passed to Firecrawl is derived from `ProductDetails.model_json_schema()` — this guarantees the LLM sees exactly the contract Pydantic will enforce. See §40 / ADR-001.

---

# 15. Recommendation Strategy

The MVP will NOT build a custom recommendation engine.

Daraz already has its own recommendation system.

The backend should attempt to extract recommendation products shown on the Daraz product page.

Conceptually:

```
Product Page
    |
    +-- Product Information
    |
    +-- Seller
    |
    +-- Reviews
    |
    +-- Specifications
    |
    +-- Daraz Recommendations
              |
              v
       Recommendation[]
```

The system should preserve the fact that these recommendations are sourced from Daraz.

Recommendations are extracted as part of the **product detail structured extraction** — the `ProductDetails` JSON Schema includes a `recommendations: array` field. No separate Firecrawl call is made.

Example:

```
{
  "recommendations": [
    {
      "id": "...",
      "title": "...",
      "price": 799,
      "url": "...",
      "image": "..."
    }
  ]
}
```

---

# 16. Recommendation Failure Behavior

If Daraz recommendations cannot be detected:

```
{
  "recommendations": []
}
```

The backend must NOT generate fake recommendations.

Future versions may add a custom recommendation engine as a fallback, but this is outside MVP scope.

---

# 17. LLM Usage

> **Amendment (2026-09-15, ADR-001):** Search-result pages use deterministic Markdown parsing only. Product detail pages use Firecrawl structured extraction (LLM + JSON Schema) validated through Pydantic. See §40 for the full rationale and constraints.

The LLM should NOT be responsible for blindly converting the entire webpage into JSON.

This rule stands for **search-result pages** and any page whose layout is uniform enough for a regex parser. It is deliberately lifted for **product detail pages**, where irregular layout makes deterministic parsing fragile.

Preferred approach (search):

```
Firecrawl Markdown
        |
        v
Deterministic parsing
        |
        v
Structured fields
        |
        v
LLM only where semantic interpretation is necessary
        |
        v
Pydantic validation
```

Preferred approach (product detail):

```
Firecrawl structured extraction
        |
        v
JSON payload matching ProductDetails schema
        |
        v
Pydantic validation (the model is the contract)
        |
        v
Structured fields
```

The LLM may be used for:

- query understanding,
- product-title normalization,
- feature extraction,
- category classification,
- semantic interpretation,
- **whole-page structured extraction on product detail pages only**.

The LLM should not fabricate:

- prices,
- product IDs,
- URLs,
- ratings,
- review counts,
- seller information.

---

# 18. Future Search Query Schema

Natural-language queries should eventually be transformed into a structured object such as:

```
{
  "query": "gaming mouse",
  "category": "gaming mouse",
  "min_price": null,
  "max_price": 5000,
  "brand": null,
  "features": [
    "wireless",
    "RGB"
  ],
  "sort": null
}
```

This schema will be used later by the AI agent to construct Daraz searches.

---

# 19. Scraper Interface

The scraping layer should expose a stable interface.

Conceptually:

```
class DarazScraper:
    def search_products(...)
    def get_product(...)
    def get_recommendations(...)
```

The rest of the backend should depend on this interface rather than directly depending on Firecrawl.

`DarazScraper.search_products` uses the **Markdown path** internally.

`DarazScraper.get_product` and `DarazScraper.get_recommendations` use the **structured extraction path** internally.

---

# 20. Firecrawl Adapter

Firecrawl should be hidden behind the scraper abstraction.

```
Application
    |
    v
DarazScraper
    |
    v
FirecrawlAdapter
    |
    +--- scrape(url) -> str        (Markdown mode)
    |
    +--- scrape_json(url, schema) -> dict   (structured extraction)
    |
    v
Firecrawl API
```

The adapter exposes two methods:

| Method | Returns | Used by |
|---|---|---|
| `scrape(url)` | `str` (Markdown) | Search result pages |
| `scrape_json(url, schema=..., prompt=..., max_age_ms=...)` | `dict[str, Any]` | Product detail pages |

Both share identical retry semantics, exception classification, and structured logging (`FIRECRAWL_REQUEST`, `FIRECRAWL_RESPONSE`, `FIRECRAWL_RETRY`, each carrying a `mode` field of `"markdown"` or `"json"`).

This makes it possible to replace Firecrawl later.

Potential future implementation:

```
DarazScraper
   |
   +-- FirecrawlAdapter
   |
   +-- PlaywrightAdapter
```

---

# 21. Current Project Structure

The project uses a **`src/` layout** managed by `uv`. The package name is `daraz_ai_shopping_assistant`.

```
daraz-ai-shopping-assistant/
│
├── src/
│   └── daraz_ai_shopping_assistant/
│       ├── __init__.py
│       ├── core/
│       ├── models/
│       └── scrapers/
│
├── tests/
│   ├── fixtures/
│   └── unit/
│
├── scripts/
│   └── generate_fixtures.py
│
├── docs/
│   └── ARCHITECTURE-DECISIONS.md
│
├── .venv/                     # managed by uv (not committed)
├── .gitignore
├── .python-version            # pinned to Python 3.14
├── pyproject.toml
├── uv.lock
├── README.md
├── Specification.md
└── structure.txt
```

## 21.1 Target Structure (to be implemented)

The following structure should be built inside `src/daraz_ai_shopping_assistant/`:

```
src/
└── daraz_ai_shopping_assistant/
    │
    ├── __init__.py
    ├── main.py                # FastAPI app entry point
    │
    ├── api/
    │   ├── __init__.py
    │   ├── search.py
    │   ├── products.py
    │   └── chat.py            # placeholder for future AI layer
    │
    ├── models/
    │   ├── __init__.py
    │   ├── product.py
    │   ├── search.py
    │   └── recommendation.py
    │
    ├── schemas/
    │   ├── __init__.py
    │   ├── product.py
    │   ├── search.py
    │   └── chat.py
    │
    ├── services/
    │   ├── __init__.py
    │   ├── search_service.py
    │   ├── product_service.py
    │   └── recommendation_service.py
    │
    ├── scrapers/
    │   ├── __init__.py
    │   ├── base.py
    │   ├── daraz.py
    │   └── firecrawl.py
    │
    ├── parsers/
    │   ├── __init__.py
    │   ├── search_parser.py
    │   ├── product_parser.py        # validation-only; extraction is Firecrawl
    │   └── recommendation_parser.py # validation-only; extraction is Firecrawl
    │
    ├── agents/                # empty until Phase 8
    │   ├── __init__.py
    │   ├── graph.py
    │   ├── state.py
    │   └── tools.py
    │
    ├── core/
    │   ├── __init__.py
    │   ├── config.py
    │   ├── exceptions.py
    │   └── logging.py
    │
    └── utils/
        └── __init__.py
```

Tests live at the repository root:

```
tests/
├── __init__.py
├── unit/
├── integration/
├── api/
└── fixtures/
```

## 21.2 Notes on the Structure

- The `src/` layout is enforced by `uv` and `pyproject.toml`.
- `app/` is **not** used — the package is `daraz_ai_shopping_assistant`, not `app`.
- The AI/agent folder (`agents/`) remains empty until Phase 8 is explicitly activated.
- `tests/` sits at the repository root, outside `src/`.
- `core/config.py` loads settings from `.env` via `pydantic-settings`.
- `docs/ARCHITECTURE-DECISIONS.md` records binding architectural decisions (see §40).
- `product_parser.py` and `recommendation_parser.py` are **validators**, not regex parsers: their job is to take Firecrawl's structured JSON output and validate it through Pydantic (plus any small normalisation the LLM missed).

---

# 22. API Endpoints

## Search Products

```
GET /api/v1/products/search
```

Parameters:

```
q
min_price
max_price
page
```

Example:

```
GET /api/v1/products/search?q=gaming%20mouse&max_price=800
```

Response:

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

# 23. Get Product

```
GET /api/v1/products/{product_id}
```

The endpoint should return detailed product information.

Example:

```
GET /api/v1/products/i1959941878
```

This endpoint internally uses Firecrawl **structured extraction** with the `ProductDetails` JSON Schema. See §40 / ADR-001.

---

# 24. Get Recommendations

```
GET /api/v1/products/{product_id}/recommendations
```

Returns recommendations detected from Daraz's product page.

Recommendations are a nested field of the product-detail structured extraction. The endpoint extracts the `recommendations` array from the `ProductDetails` payload — no additional Firecrawl call is made.

---

# 25. Future Chat Endpoint

Not required initially, but architecture should support:

```
POST /api/v1/chat
```

Example request:

```
{
  "message": "Find me a gaming mouse under Rs.
```

Future agent flow:

```
User Message
    |
    v
LangGraph
    |
    v
Understand Intent
    |
    v
Search Parameters
    |
    v
search_
```

---

# 26. Search Pagination

Daraz may report a very large number of total products.

Example:

```
11,084 products
102 pages
40 products/page
```

The backend must NOT scrape the entire catalog by default.

MVP behavior:

```
Request page 1
    |
    v
Extract available products
    |
    v
Return results
```

Future versions may support multiple-page searching until enough suitable products have been found.

---

# 27. Product Deduplication

Products retrieved from multiple search queries or pages may contain duplicates.

The preferred initial deduplication key is:

```
Daraz product ID
```

For example:

```
i1959941878
```

If the product ID is available, it should be considered the canonical identifier.

---

# 28. Search URL Builder

Daraz-specific URL syntax should be isolated inside a dedicated component.

Example:

```
User filter:
max_price = 800
```

The rest of the application should not need to know Daraz's URL syntax.

---

# 29. Error Handling

The backend must handle:

- Firecrawl API errors
- Daraz page unavailable
- timeout
- malformed Markdown
- missing product data
- invalid product URL
- parser failure
- **structured extraction returning malformed JSON**
- **structured extraction returning data that fails Pydantic validation**
- rate limits
- unexpected Daraz page changes

API responses should use appropriate HTTP status codes.

Example:

```
400 → invalid
```

Do not expose internal stack traces to API consumers.

The raw payload from a failed structured extraction must **never** be returned to the client — log it server-side and return a generic `ParseError`.

---

# 30. Logging

The backend should log important events.

Example:

```
SEARCH_ST
```

Logs should include useful context such as:

```
query
page
number of products
duration
error type
mode ("
```

Do not log secrets or API keys.

---

# 31. Environment Variables

Example `.env`:

```
FIRECRAWL
```

Future:

```
LLM_API_KEY=
DATABASE_URL=
```

All secrets must remain outside source code.

---

# 32. Testing Strategy

Testing should happen in layers.

## Unit Tests

Test:

- price parsing
- discount parsing
- product ID extraction
- URL extraction
- pagination extraction
- Markdown parsing
- Pydantic validation
- **FirecrawlAdapter in both modes (Markdown + JSON), fully mocked**

## Integration Tests

Test:

```
Daraz URL
   
```

## API Tests

Test:

```
GET /products/search
GET /products/{id}
GET /products/{id}/recommendations
```

Structured-extraction tests use a **saved sample payload** as a fixture — the LLM is never called in tests.

---

# 33. MVP Development Order

Implement in this order.

## Phase 1 — Data Models

Create:

```
Product
SearchFilters
Pagination
SearchResult
Seller
Shipping
Product
```

using Pydantic. ✅ **Done.**

---

## Phase 2 — Firecrawl Integration

Implement:

```
FirecrawlAdapter
```

Two methods: `scrape` (Markdown) and `scrape_json` (structured extraction). ✅ **Done.**

---

## Phase 3 — Search Parser

Input:

```
Firecrawl Markdown
```

Output:

```
SearchResult
```

*(Next.)*

---

## Phase 4 — Daraz Search Service

Implement:

```
search_products()
```

Responsibilities:

```
build URL
→ Firecrawl (Markdown)
→ parse
```

---

## Phase 5 — FastAPI Search Endpoint

Implement:

```
GET /api/v1/products/search
```

At this stage the backend should already be useful without AI.

---

## Phase 6 — Product Page Extraction

Implement:

```
get_product()
```

Uses Firecrawl **structured extraction** with the `ProductDetails` schema, then validates through Pydantic.

---

## Phase 7 — Recommendation Extraction

Implement:

```
get_recommendations()
```

Reads the `recommendations` array from the product-detail payload. No second Firecrawl call.

---

## Phase 8 — Future AI Layer

Add:

```
LangGraph
LLM
tool calling
conversation state
```

Only after the deterministic backend is working reliably.

---

# 34. Definition of Done — MVP

The MVP is considered successful when this workflow works reliably:

```
GET /api/v1/products/search?q=gaming+mouse&
```

produces:

```
FastAPI
    ↓
Search Service
    ↓
DarazScraper
    ↓
Firecrawl (Markdown mode)
    ↓
Daraz search page
    ↓
Parser
```

Then:

```
GET /api/v1/products/{id}
```

returns detailed product information (via structured extraction).

And:

```
GET /api/v1/products/{id}/recommendations
```

returns Daraz's own recommendations when available.

---

# 35. Future Vision

The eventual product should support natural-language shopping:

```
User:
"I need a wireless
```

The AI agent should:

```
Understand request
      ↓
Create search
```

Examples of future interactions:

```
"Show me cheaper ones
```

The backend architecture must allow these capabilities to be added without rewriting the scraping layer.

---

# 36. Current Installed Dependencies

The project is managed with **uv** and currently installs the following direct dependencies. Only these may be used without asking the user.

## 36.1 Runtime (MVP)

| Package ↕▾ | Purpose ↕▾ |
|---|---|
| −`fastapi` | API framework |
| −`uvicorn[standard]` | ASGI server with reload + uvloop |
| −`pydantic` | Data models & validation |
| −`pydantic-settings` | `.env`-driven config |
| −`python-dotenv` | Load `.env` |
| −`httpx` | Async HTTP client |
| −`firecrawl-py` | Official Firecrawl SDK |
⚙

## 36.2 AI Layer (Phase 8 only)

| Package ↕▾ | Purpose ↕▾ |
|---|---|
| −`langgraph` | Agent graph runtime |
| −`langchain-core` | Core LLM abstractions |
| −`langchain-google-genai` | Gemini provider (currently installed) |
⚙

> Note: the currently installed provider is `langchain-google-genai`. If a different provider (OpenAI, Anthropic, etc.) is preferred, it must be swapped explicitly.

## 36.3 Dev / Tooling

| Package ↕▾ | Purpose ↕▾ |
|---|---|
| −`pytest` | Test runner |
| −`pytest-asyncio` | Async test support |
| −`pytest-cov` | Coverage reports |
| −`ruff` | Linter + formatter |
| −`mypy` | Static type checking |
| −`pre-commit` | Git hooks |
| −`playwright` | Installed but **not to be used** unless explicitly requested (see §5) |
⚙

## 36.4 Rules

- Do not add a new dependency without asking the user first.
- Do not use Playwright unless the user explicitly requests it.
- Do not add Redis, Qdrant, Postgres, or background job libraries in MVP (see §3).

---

# 37. uv Commands Reference

Common commands used with this project:

```
# Initialize the project
uv init

# Pin the Python version (already pinned to 3.14)
uv python pin 3.14

# Add runtime dependencies
uv add fast
```

---

# 38. Package Entry Point

Because the project uses a `src/` layout, the FastAPI app must be referenced as:

```
daraz_ai_shopping_assistant.main:app
```

**Not** `app.main:app`.

The `main.py` file lives at:

```
src/daraz_ai_shopping_assistant/main.py
```

---

# 39. Environment and Version Notes

- **Python:** 3.14 (pinned via `.python-version`)
- **Package manager:** `uv`
- **Layout:** `src/` layout — package name `daraz_ai_shopping_assistant`
- **Virtual environment:** `.venv/` at repository root (created by `uv`, not committed)

All commands must be run through `uv run ...` unless the virtual environment is activated manually.

---

# 40. Architecture Decision Records

Binding decisions that shape the codebase are recorded in `docs/ARCHITECTURE-DECISIONS.md`. When a change contradicts an ADR, either update the ADR or open a new one — do not silently diverge.

## ADR-001 — Search pages use deterministic parsing; product pages use structured extraction

**Status:** Accepted (2026-09-15)

**Decision:** Split extraction strategy by page type.

| Page type ↕▾ | Adapter method ↕▾ | Extraction ↕▾ |
|---|---|---|
| −Search results (`/catalog/?q=...`) | `scrape(url)` → Markdown | Deterministic parser |
| −Product detail (`/products/...`) | `scrape_json(url, schema=...)` → dict | Firecrawl structured extraction (LLM + JSON Schema) |
| −Recommendations (nested in product page) | Same as product detail | Same as product detail, nested schema |
⚙

**Why:** Search pages are high-volume and structurally uniform — Markdown parsing is cheaper and deterministic. Product detail pages have irregular layout (specifications tables, seller widgets, variant pickers, variable-length reviews) where an LLM-driven extraction is more robust than fragile regex, and the cost is acceptable at that call volume.

**Consequences:**

- Search pipeline remains fully deterministic. No LLM touches a search result.
- Product detail extraction costs more Firecrawl credits.
- Product detail extraction is non-deterministic across runs — mitigated by Pydantic validation.
- Structured-extraction failures are harder to unit test — mitigated by saving sample payloads as fixtures.

**Forbidden:**

- Calling `scrape_json` on a search result page.
- Calling `scrape` (Markdown) on a product detail page and feeding it to a regex parser.
- Skipping Pydantic validation on structured-extraction output.
- Letting the LLM produce `id`, `url`, `price`, or `currency` values without validation.

**Full text:** `docs/ARCHITECTURE-DECISIONS.md` §ADR-001.

