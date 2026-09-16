# Architecture

The backend is a strict, dependency-injected pipeline. Each layer knows only
the layer directly below it, and the Pydantic model is the single trusted
contract before a response reaches a client.

---

## 1. Layering

```
API route (api/)
  → service (services/)
  → scraper (scrapers/)
  → Firecrawl adapter (scrapers/firecrawl.py)
  → Daraz.pk / Firecrawl
      ↓
  parser (parsers/) when the source is Markdown search HTML
      ↓
  Pydantic model validation (models/)
      ↓
  JSON response over FastAPI
```

### Hard layering rules

- Routes own HTTP validation and response serialization; they do not contain business logic.
- Services coordinate fetch, parse, validate, and normalise flows.
- `FirecrawlDarazScraper` owns the Daraz-specific interface; `FirecrawlAdapter` is the only module that imports `firecrawl`.
- Parser functions are pure: Markdown in, dict out.
- Pydantic models are the contract: raw data is never exposed directly to clients.
- The chat agent sits above the backend and calls the validated services, never bypassing them.

---

## 2. Data flow

### 2.1 Search path

```
GET /api/v1/products/search?q=...
   │
   ▼
search_products_endpoint
   │
   ▼
SearchService.search
   │  normalise query + validate filters
   ▼
FirecrawlDarazScraper.fetch_search_markdown
   │
   ▼
FirecrawlAdapter.scrape (Markdown mode)
   │
   ▼
parse_search_results (pure parser)
   │
   ▼
Product.model_validate for each item
   │
   ▼
SearchResult + pagination envelope
```

This path is deterministic and fully testable with frozen Markdown fixtures.

### 2.2 Product detail path

```
GET /api/v1/products/{product_id}
   │
   ▼
ProductService.get_product
   │
   ▼
FirecrawlDarazScraper.fetch_product_payload
   │  uses ProductDetails.model_json_schema() + prompt
   ▼
FirecrawlAdapter.scrape_json
   │
   ▼
ProductDetails.model_validate(payload)
   │
   ▼
validated ProductDetails response
```

The product-detail flow uses Firecrawl structured extraction rather than regex parsing.

### 2.3 Recommendations path

```
GET /api/v1/products/{product_id}/recommendations
   │
   ▼
ProductService.get_recommendations
   │
   ▼
load the same product payload already fetched by get_product
   │
   ▼
return Recommendation[] (possibly empty)
```

No second Firecrawl call is made. Recommendations are sourced from the product payload itself.

---

## 3. Extraction strategy by page type (ADR-001)

| Page type | Firecrawl mode | Extraction method | Validation |
|---|---|---|---|
| Search results | Markdown scrape | deterministic parser | `Product.model_validate` |
| Product detail | JSON schema extraction | structured extraction | `ProductDetails.model_validate` |
| Recommendation carousel | same as product detail | nested structured extraction | `Recommendation.model_validate` |

This keeps the search layer deterministic while allowing the irregular product-detail page to be handled by a schema-driven extraction model.

---

## 4. Agent layer

The chat endpoint is implemented in the `agents/` package and runs over the same
validated backend services as the REST routes.

```
POST /api/v1/chat
   │
   ▼
ChatService.chat
   │
   ▼
LangGraph graph (parse_intent → tool → respond)
   │
   ▼
search_products / get_product / get_recommendations tools
   │
   ▼
validated response + natural-language reply
```

The LLM does not invent product facts. It classifies intent and writes a plain-language summary using values already returned by the backend services.

---

## 5. Folder layout

The project uses a src layout:

```
src/daraz_ai_shopping_assistant/
├── __init__.py
├── main.py
├── api/
│   ├── __init__.py
│   ├── chat.py
│   ├── deps.py
│   ├── exception_handlers.py
│   ├── products.py
│   └── search.py
├── agents/
│   ├── __init__.py
│   ├── graph.py
│   ├── state.py
│   └── tools.py
├── core/
│   ├── __init__.py
│   ├── config.py
│   ├── exceptions.py
│   └── logging.py
├── models/
│   ├── __init__.py
│   ├── product.py
│   ├── recommendation.py
│   └── search.py
├── parsers/
│   ├── __init__.py
│   └── search_parser.py
├── schemas/
│   ├── __init__.py
│   ├── chat.py
│   ├── product.py
│   └── search.py
├── scrapers/
│   ├── __init__.py
│   ├── base.py
│   ├── daraz.py
│   └── firecrawl.py
├── services/
│   ├── __init__.py
│   ├── chat_service.py
│   ├── product_service.py
│   └── search_service.py
├── utils/
│   ├── __init__.py
│   └── datetime.py
└── __init__.py
```

This is the active implementation. There is no root-level app/ package and no placeholder-only product-service layer.

---

## 6. Design principles

### 6.1 Numeric fields stay numeric

The project normalises values at the model boundary. `price` is a `float`, `discount_percentage` is `int | None`, and missing values stay `None` unless a collection such as `recommendations` is legitimately empty.

### 6.2 The model is the contract

The app validates all product and search payloads with Pydantic before returning them to clients. This is especially important for Firecrawl structured extraction, where the response is untrusted until the model validates it.

### 6.3 The agent is bounded

The LangGraph layer is allowed to classify intent and summarise tool results. It is not allowed to bypass the product services or fabricate raw product facts.

### 6.4 No direct Firecrawl access outside the adapter

Only `src/daraz_ai_shopping_assistant/scrapers/firecrawl.py` imports the Firecrawl SDK. Services and routes depend on the scraper interface instead of the provider SDK itself.

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
