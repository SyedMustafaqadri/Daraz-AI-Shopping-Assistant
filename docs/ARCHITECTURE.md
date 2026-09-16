# Architecture

The backend is a strict, dependency-injected pipeline. Each layer knows only
the layer directly below it, and the Pydantic model is the single trusted
contract that everything is validated against before it reaches a client.

---

## 1. Layering

```
┌─────────────────────────────────────────────────────────────┐
│  API Route (api/)                                           │
│  - URL paths, query params, response serialisation           │
│  - NO business logic. Delegates to services.                 │
└───────────────────────────┬─────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────┐
│  Service (services/)                                        │
│  - Orchestrates: build URL → call scraper → call parser →   │
│    validate → assemble envelope.                            │
│  - Owns timestamps, filter/pagination objects, decision on  │
│    dropped products.                                        │
│  - Does NOT know Firecrawl exists.                          │
└───────────────────────────┬─────────────────────────────────┘
        ┌───────────────────┼────────────────────┐
        │                   │                    │
┌───────▼──────┐   ┌────────▼────────┐   ┌──────▼──────────┐
│ Scraper      │   │ Parser          │   │ Pydantic Models │
│ (scrapers/)  │   │ (parsers/)      │   │ (models/)       │
│ DarazScraper │   │ pure functions  │   │ domain schema   │
│ interface +  │   │ Markdown → dict │   │ the contract    │
│ Firecrawl    │   │ no I/O          │   │                 │
│ impl         │   │                 │   │                 │
└───────┬──────┘   └─────────────────┘   └─────────────────┘
        │
┌───────▼─────────────────────────────────────────────────────┐
│  FirecrawlAdapter (scrapers/firecrawl.py)                   │
│  - ONLY module permitted to import `firecrawl`.             │
│  - Two modes: scrape (Markdown) and scrape_json (LLM).      │
│  - Retries transient failures, maps to typed exceptions.    │
└──────────────────────────────────────────────────────────────┘
```

### Hard layering rules

- **Routes** (`api/`) contain no business logic. They call services and return
  response schemas.
- **Services** (`services/`) orchestrate. They never import `firecrawl`.
- **Scrapers** own the interface. `FirecrawlDarazScraper` calls
  `FirecrawlAdapter`. Only `scrapers/firecrawl.py` imports `firecrawl`.
- **Parsers** (`parsers/`) are pure: Markdown in, dict out. No I/O, no
  network, no globals, no Pydantic validation (that belongs to the service).
- **Models** (`models/`) hold internal Pydantic domain models.
- **Schemas** (`schemas/`) hold FastAPI request/response DTOs.
- **Core** (`core/`) holds settings, exceptions, and logging.

### Forbidden patterns

- `import firecrawl` anywhere outside `scrapers/firecrawl.py`.
- `httpx.get(...)` inside a parser.
- Returning raw dicts from API routes (must be Pydantic models).
- Business logic inside route handlers.
- Global mutable state.
- `print()` for debugging (use `logging`).

---

## 2. Data Flow

### 2.1 Search (deterministic path)

```
GET /api/v1/products/search?q=...
   │
   ▼
search_products_endpoint (api/search.py)
   │  validates query params via FastAPI Query
   ▼
SearchService.search (services/search_service.py)
   │  1. normalise + validate input (empty query, price range)
   │  2. fetch raw Markdown
   ▼
FirecrawlDarazScraper.fetch_search_markdown (scrapers/daraz.py)
   │  builds the Daraz URL, delegates to the adapter
   ▼
FirecrawlAdapter.scrape (scrapers/firecrawl.py)
   │  Firecrawl Markdown mode, with retry + exception mapping
   ▼
Daraz search-results page (Markdown string)
   │
   ▼
parse_search_results (parsers/search_parser.py)
   │  pure regex parsing → untrusted dict
   ▼
Product.model_validate per product (services/search_service.py)
   │  invalid entries are dropped + logged
   ▼
SearchResult envelope (models/search.py)
   │
   ▼
JSON response (schemas/search.SearchResponse == SearchResult)
```

No LLM touches a search result. The entire path is deterministic and testable
against a frozen Markdown fixture.

### 2.2 Product detail (structured-extraction path)

```
GET /api/v1/products/{product_id}
   │
   ▼
ProductService.get_product (Phase 6 — planned)
   │
   ▼
FirecrawlDarazScraper.fetch_product_payload
   │  builds the Daraz product URL,
   │  passes ProductDetails.model_json_schema() + a short prompt
   ▼
FirecrawlAdapter.scrape_json
   │  LLM reads the page and returns a dict matching the schema
   ▼
dict (untrusted)
   │
   ▼
ProductDetails.model_validate   ← the model is the contract
   │  on failure → log PRODUCT_VALIDATION_FAILED, raise ParseError(source="product")
   ▼
ProductDetails response
```

### 2.3 Recommendations

Recommendations are a nested field of the product-detail structured
extraction — the `ProductDetails` schema includes a `recommendations: array`.
`GET /api/v1/products/{product_id}/recommendations` (Phase 7 — planned) reads
that array. No second Firecrawl call is made. When Daraz shows no
recommendation carousel, the array is empty — it is never fabricated.

---

## 3. Extraction Strategy by Page Type (ADR-001)

Two distinct pipelines exist, chosen by **page type**, not by convenience:

| Page type | Firecrawl call | Extraction | Validated by |
|---|---|---|---|
| Search results (`/catalog/?q=...`) | `scrape(url)` → Markdown | Deterministic parser (regex) | `Product.model_validate` |
| Product detail (`/products/...`) | `scrape_json(url, schema=...)` → dict | LLM structured extraction | `ProductDetails.model_validate` |
| Recommendations (nested) | Same as product detail | Same, nested schema | `Recommendation.model_validate` |

**Rules (enforced):**

- Never call `scrape_json` on a search-result page.
- Never call `scrape` (Markdown) on a product detail page and feed it to a
  regex parser.
- Never skip Pydantic validation on structured-extraction output.

Full rationale and consequences: [ARCHITECTURE-DECISIONS.md](ARCHITECTURE-DECISIONS.md) §ADR-001.

---

## 4. Design Principles

### 4.1 Separation of Responsibilities

```
AI reasoning  !=  scraping  !=  data normalisation  !=  API layer
```

The LLM (when present) only handles semantic interpretation and tool calling.
Firecrawl handles content retrieval. Application code handles deterministic
parsing, validation, normalisation, and API responses.

### 4.2 Numeric Fields Must Be Numeric

Presentation formatting is stripped at the parser boundary. `price` is a
`float`, `discount_percentage` is an `int | None`, `coins_save` and
`sold_count` are integers. Missing data is `None` — never invented, never
defaulted to `0` (except genuinely-empty collections like
`recommendations: []`).

### 4.3 The Model Is the Contract

For structured extraction, the JSON Schema passed to Firecrawl is derived
from `ProductDetails.model_json_schema()`. The `extra="forbid"` config adds
`additionalProperties: false`, so the LLM cannot invent undeclared fields.
The schema is a *suggestion*; Pydantic validation is the *enforcement*.

### 4.4 Swappable Transport

Services depend on the `DarazScraper` interface, never on a concrete
implementation. A future Playwright-based scraper would subclass the same
interface and be drop-in replaceable.

---

## 5. Folder Layout

The package lives at `src/daraz_ai_shopping_assistant/` (a `src/` layout).
There is **no** `app/` folder. Tests sit at the repository root, outside
`src/`.

```
src/daraz_ai_shopping_assistant/
├── __init__.py            # __version__
├── main.py                # FastAPI app factory + module-level `app`
│
├── api/
│   ├── __init__.py        # api_router mounted under /api/v1
│   ├── deps.py            # FastAPI dependency providers
│   ├── exception_handlers.py
│   └── search.py          # GET /products/search
│
├── models/
│   ├── __init__.py
│   ├── product.py         # Product, ProductDetails, Seller, Shipping, ...
│   ├── recommendation.py  # Recommendation
│   └── search.py          # SearchFilters, Pagination, SearchResult
│
├── schemas/
│   ├── __init__.py
│   └── search.py          # SearchResponse alias
│
├── services/
│   ├── __init__.py
│   └── search_service.py  # SearchService + search_products
│
├── scrapers/
│   ├── __init__.py
│   ├── base.py            # DarazScraper (ABC)
│   ├── daraz.py           # FirecrawlDarazScraper + URL builders
│   └── firecrawl.py       # FirecrawlAdapter (only firecrawl import)
│
├── parsers/
│   ├── __init__.py
│   └── search_parser.py   # parse_search_results (pure)
│
├── core/
│   ├── __init__.py
│   ├── config.py          # Settings (pydantic-settings)
│   ├── exceptions.py      # DarazScraperError hierarchy
│   └── logging.py         # configure_logging, get_logger
│
└── utils/
    ├── __init__.py
    └── datetime.py        # PKT timezone + pkt_now()
```

### Planned (not yet implemented)

The `Specification.md` target structure anticipates these modules, which do
not exist yet:

- `api/products.py`, `api/chat.py` (chat is a Phase 8 placeholder)
- `services/product_service.py`, `services/recommendation_service.py`
- `parsers/product_parser.py`, `parsers/recommendation_parser.py`
  (validators for structured-extraction payloads)
- `agents/` package — empty until Phase 8 (LangGraph) is activated

---

## 6. MVP Development Order

| Phase | Scope | Status |
|---|---|---|
| 1 | Data models (Pydantic) | ✅ Done |
| 2 | `FirecrawlAdapter` (Markdown + JSON modes) | ✅ Done |
| 3 | Search parser | ✅ Done |
| 4 | `SearchService` | ✅ Done |
| 5 | `GET /api/v1/products/search` | ✅ Done |
| 6 | Product page extraction (`get_product`) | planned |
| 7 | Recommendation extraction (`get_recommendations`) | planned |
| 8 | LangGraph AI layer | placeholder only |

Phases 1–5 constitute the working, fully-deterministic MVP. The AI layer
(Phase 8) must wait until the deterministic backend is proven reliable.

---

## 7. Future Vision

The end state supports natural-language shopping:

```
User: "I need a wireless gaming mouse under Rs. 1000, with good reviews."
   ↓
LangGraph agent understands intent
   ↓
Builds a structured search query
   ↓
Calls SearchService → same deterministic pipeline as today
```

The architecture is designed so these capabilities can be added **without
rewriting** the scraping layer. The LLM is always a consumer of the
deterministic services, never a source of product data.
