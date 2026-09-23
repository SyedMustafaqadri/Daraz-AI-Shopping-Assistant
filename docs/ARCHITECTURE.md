# Architecture

The backend is a strict, dependency-injected pipeline. Each layer knows only
the layer directly below it, and the Pydantic model is the single trusted
contract before a response reaches a client.

---

## 1. Layering

```

API route (api/)
-> service (services/)
-> scraper (scrapers/)
-> Firecrawl adapter (scrapers/firecrawl.py)
-> Daraz.pk / Firecrawl
|
parser (parsers/) when the source is Markdown search HTML
|
Pydantic model validation (models/)
|
JSON response over FastAPI

Side layers:
storage/     -- local JSON persistence for scrape payloads (services call it)
agents/      -- LangGraph chat layer (sits above services, calls them only)

```

### Hard layering rules

- Routes own HTTP validation and response serialization; they do not contain business logic.
- Services coordinate fetch, parse, validate, normalise, and persist flows.
- `FirecrawlDarazScraper` owns the Daraz-specific interface; `FirecrawlAdapter` is the only module that imports `firecrawl`.
- Parser functions are pure: Markdown in, dict out.
- Pydantic models are the contract: raw data is never exposed directly to clients.
- `storage/ScrapeStore` is called ONLY by the service layer. Routes, scrapers, parsers, and agents never touch it directly.
- The chat agent sits above the backend and calls the validated services, never bypassing them.

---

## 2. Data flow

### 2.1 Search path

```

GET /api/v1/products/search?q=...
|
v
search_products_endpoint
|
v
SearchService.search
|  1. build cache key from query + filters + page
|  2. store.get(key)
|     - hit  -> SearchResult.model_validate(payload)  -> return
|     - miss -> continue
v
FirecrawlDarazScraper.fetch_search_markdown
|
v
FirecrawlAdapter.scrape (Markdown mode)
|
v
parse_search_results (pure parser)
|
v
Product.model_validate for each item
|
v
store.set(key, kind="search", payload, ttl=search_ttl)  (only on success)
|
v
SearchResult + pagination envelope

```

This path is deterministic and fully testable with frozen Markdown fixtures.

### 2.2 Product detail path

```

GET /api/v1/products/{product_id}
|
v
ProductService.get_product
|  1. build cache key from product_id
|  2. store.get(key)
|     - hit  -> ProductDetails.model_validate(payload) -> return
|     - miss -> continue
v
FirecrawlDarazScraper.fetch_product_payload
|  uses ProductDetails.model_json_schema() + prompt
v
FirecrawlAdapter.scrape_json
|
v
ProductDetails.model_validate(payload)
|
v
store.set(key, kind="product", payload, ttl=product_ttl)
|
v
validated ProductDetails response

```

The product-detail flow uses Firecrawl structured extraction rather than
regex parsing.

### 2.4 Chat path

```

POST /api/v1/chat          POST /api/v1/chat/stream
|                            |
v                            v
ChatService.chat           ChatService.chat_stream
|  1. resolve conversation_id (client's or new UUID)
|  2. build initial AgentState (one HumanMessage)
|  3. graph.ainvoke / graph.astream_events
|       config={"configurable": {"thread_id": conversation_id}}
|  4. extract last AIMessage as reply
v
ChatResponse / SSE frames

```

---

## 3. Extraction strategy by page type (ADR-001)

| Page type            | Firecrawl mode          | Extraction method              | Validation                        |
|----------------------|-------------------------|--------------------------------|-----------------------------------|
| Search results       | Markdown scrape         | deterministic parser           | `Product.model_validate`          |
| Product detail       | JSON schema extraction  | structured extraction          | `ProductDetails.model_validate`   |

This keeps the search layer deterministic while allowing the irregular
product-detail page to be handled by a schema-driven extraction model.

---

## 4. Agent layer

The chat endpoint is implemented in the `agents/` package and runs over the
same validated backend services as the REST routes.

```

POST /api/v1/chat/stream
|
v
ChatService.chat_stream
|
v
LangGraph graph.astream_events
|     configurable.thread_id = conversation_id
v
+-----------------------------------------------------------------+
|  parse_intent  ->  search / get_product / small_talk             |
|         |             |             |             |             |
|         +-------------+------+------+------+------+             |
|                              v                                  |
|                          respond                                |
+-----------------------------------------------------------------+
|
v
stream of "on_chat_model_stream" events  (filtered to node="respond")
|
v
SSE frames: {"type": "token", "text": "..."} ... {"type": "done", ...}

```

The LLM does not invent product facts. It classifies intent and writes a
plain-language summary using values already returned by the backend services.

### 4.1 Conversation memory

The graph is compiled with a LangGraph checkpointer (`MemorySaver` by
default). State is keyed by `thread_id`, which the service sets to the
`conversation_id`.

- A new process starts with an empty checkpointer.
- Conversations do not survive a restart.
- The checkpointer holds the full message history; the LLM only sees the
  last 16 messages (8 turns) via `trim_messages`.
- Both `parse_intent` and `respond` see the trimmed history, so follow-up
  turns like "show me the second one" resolve correctly.

### 4.2 Streaming

`chat_stream` iterates over `graph.astream_events(..., version="v2")` and
forwards only `on_chat_model_stream` events whose `metadata.langgraph_node`
is `"respond"`. This filter is essential: `parse_intent` also calls the
LLM, and its output is a structured object that must never be streamed to
the client. After the loop, the service fetches the final state via
`graph.aget_state` and emits a single `done` event carrying
`conversation_id`, `intent`, and `error`.

---

## 5. Folder layout

The project uses a src layout:

```

src/daraz_ai_shopping_assistant/
|-- **init**.py
|-- main.py
|-- api/
|   |-- **init**.py
|   |-- chat.py
|   |-- deps.py
|   |-- exception_handlers.py
|   |-- products.py
|   `-- search.py
|-- agents/
|   |-- __init__.py
|   |-- graph.py
|   |-- state.py
|   `-- tools.py
|-- core/
|   |-- **init**.py
|   |-- config.py
|   |-- exceptions.py
|   `-- logging.py
|-- models/
|   |-- __init__.py
|   |-- product.py
|   `-- search.py
|-- parsers/
|   |-- **init**.py
|   `-- search_parser.py
|-- schemas/
|   |-- __init__.py
|   |-- chat.py
|   |-- product.py
|   `-- search.py
|-- scrapers/
|   |-- **init**.py
|   |-- base.py
|   |-- daraz.py
|   `-- firecrawl.py
|-- services/
|   |-- __init__.py
|   |-- chat_service.py
|   |-- product_service.py
|   `-- search_service.py
|-- storage/
|   |-- **init**.py
|   `-- json_store.py
|-- utils/
|   |-- __init__.py
|   `-- datetime.py
`-- **init**.py

```

This is the active implementation. There is no root-level app/ package and
no placeholder-only product-service layer.

---

## 6. Design principles

### 6.1 Numeric fields stay numeric

The project normalises values at the model boundary. `price` is a `float`,
`discount_percentage` is `int | None`, and missing values stay `None`
unless a collection is legitimately empty.

### 6.2 The model is the contract

The app validates all product and search payloads with Pydantic before
returning them to clients. This is especially important for Firecrawl
structured extraction, where the response is untrusted until the model
validates it.

### 6.3 The agent is bounded

The LangGraph layer is allowed to classify intent and summarise tool
results. It is not allowed to bypass the product services or fabricate raw
product facts. See ADR-002.

### 6.4 No direct Firecrawl access outside the adapter

Only `src/daraz_ai_shopping_assistant/scrapers/firecrawl.py` imports the
Firecrawl SDK. Services and routes depend on the scraper interface instead
of the provider SDK itself.

### 6.5 The store is a service-layer concern

`storage/ScrapeStore` is a leaf module. It is loaded once at startup,
attached to the service singletons, and read/written ONLY by the search
and product services. Nothing above the service layer knows the store
exists. Swapping the JSON file for a database would be a single-layer
change.

---

## 7. MVP Development Order

| Phase | Scope                                                        | Status |
|-------|--------------------------------------------------------------|--------|
| 1     | Data models (Pydantic)                                       | Done   |
| 2     | `FirecrawlAdapter` (Markdown + JSON modes)                   | Done   |
| 3     | Search parser                                                | Done   |
| 4     | `SearchService` + `FirecrawlDarazScraper`                    | Done   |
| 5     | `GET /api/v1/products/search`                                | Done   |
| 6     | Product page extraction (`ProductService.get_product`)       | Done   |
| 8     | LangGraph AI layer (`POST /api/v1/chat`)                     | Done   |
| 9     | Conversation memory + SSE streaming + JSON scrape store      | Done   |

All phases implemented and verified end-to-end against live Daraz.

---

## 8. Future Vision

The end state supports natural-language shopping:

```

User: "I need a wireless gaming mouse under Rs. 1000, with good reviews."
|
v
LangGraph agent understands intent
|
v
Builds a structured search query
|
v
Calls SearchService -> same deterministic pipeline as today

```

The architecture is designed so these capabilities can be added **without
rewriting** the scraping layer. The LLM is always a consumer of the
deterministic services, never a source of product data.
