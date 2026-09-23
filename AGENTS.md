# AGENTS.md -- AI Agent Instructions for Daraz AI Shopping Assistant Backend

> Audience: AI coding agents (Claude Code, Cursor, Copilot, Aider, Codex, etc.)
> working on this repository.
> Read this file first. It overrides generic defaults. Follow it precisely.
> Companion document: `Specification.md` -- the single source of truth for
> architecture, schemas, and workflows.

---

## 1. Project Identity

You are working on the Daraz AI Shopping Assistant -- Backend.

The system:

- Searches Daraz.pk products via natural or structured queries.
- Scrapes Daraz using Firecrawl.
- Parses scraped Markdown into validated Pydantic models.
- Exposes clean REST APIs via FastAPI.
- Runs a bounded LangGraph chat layer on top of the deterministic services.
- Persists scrape payloads to a local JSON file for repeat reads.

Status: Phases 1-9 complete. All endpoints work end-to-end against live
Daraz. The deterministic backend remains the source of truth for every
product fact; the AI layer is a consumer, never a producer of data.

---

## 2. Prime Directives

When working on this repo, you MUST:

1. Follow `Specification.md` as the single source of truth. If code and spec
   conflict, the spec wins -- unless the user explicitly overrides it in the
   current task.
2. Never invent product data. Prices, IDs, URLs, ratings, seller info must
   come from scraped content or be `null`.
3. Never skip Pydantic validation. Every product object returned by an API
   route must pass through a Pydantic model.
4. Never bypass the `DarazScraper` interface. API routes -> services ->
   `DarazScraper` -> `FirecrawlAdapter`. Never call Firecrawl directly from
   a route or service.
5. Never install libraries outside the approved list (Section 7). Ask before
   adding new dependencies.
6. Never use Playwright unless the user explicitly requests it (Spec Section
   5). It is installed but must not be used.
7. Never add Redis, Qdrant, Postgres, or background job infrastructure in
   MVP (Spec Section 3).
8. Ask before creating files outside the structure in Section 8 of this
   document, unless the user explicitly requests them.
9. Prefer small, focused, testable functions over large monolithic ones.
10. Write code like a senior engineer -- typed, documented, error-handled,
    no placeholders.

---

## 3. Non-Goals (Do NOT Implement)

If a task appears to require any of the following, stop and ask the user:

- Vector databases / embeddings / Qdrant
- Redis / caching layer (the local scrape store is not a caching layer;
  it is local durability for scrape payloads and is explicitly allowed)
- User authentication or payments
- Price tracking / notifications
- Review sentiment analysis
- Multi-marketplace support (only Daraz.pk)
- Frontend code
- Database persistence (until explicitly requested -- the JSON scrape store
  is not a database and is explicitly allowed)
- Async job queues (Celery, RQ, etc.)

---

## 4. Critical -- Package Layout

This project uses a `src/` layout. There is NO `app/` folder.

- Package name: `daraz_ai_shopping_assistant`
- Package location: `src/daraz_ai_shopping_assistant/`
- FastAPI entry point: `daraz_ai_shopping_assistant.main:app`
- Run command:

```bash
uv run uvicorn daraz_ai_shopping_assistant.main:app --reload --host 0.0.0.0 --port 8000
```

### Forbidden patterns

- DO NOT create an `app/` folder at the repository root.
- DO NOT write `app.main:app` in any command, docstring, or README.
- DO NOT import like `from app.models.product import Product`.
- DO NOT place business logic at the repository root outside
`src/daraz_ai_shopping_assistant/`.

### Correct import style

```
from daraz_ai_shopping_assistant.models.product import Product
from daraz_ai_shopping_assistant.services.search_service import search_products
from daraz_ai_shopping_assistant.core.config import settings
```

---

## 5. Architecture Rules

### Layering (strict -- do not violate)

```
API Route  ->  Service  ->  Scraper  ->  FirecrawlAdapter  ->  Firecrawl API
                  |
                  v
               Parser
                  |
                  v
            Pydantic Models

Side layers:
  storage/  -- local JSON persistence; called ONLY by services
  agents/   -- LangGraph chat layer; calls services, never scrapers
```

### Hard rules

- Routes (`api/`) must contain no business logic. They call services and
return response schemas.
- Services (`services/`) orchestrate: build URL -> call scraper -> call
parser -> validate -> persist -> return. They do not know Firecrawl exists.
- Scrapers (`scrapers/`) own the interface. `DarazScraper` calls
`FirecrawlAdapter`. `FirecrawlAdapter` is the only place that imports
`firecrawl`.
- Parsers (`parsers/`) are pure functions: Markdown in -> dict out. No I/O,
no network.
- Models (`models/`) hold Pydantic domain models (internal).
- Schemas (`schemas/`) hold FastAPI request/response DTOs (may inherit from
models).
- Core (`core/config.py`) holds settings loaded from `.env`.
- Storage (`storage/`) holds local persistence. It is called ONLY by the
service layer. Routes, scrapers, parsers, and the agent layer must never
import `storage/` directly.

### Forbidden patterns

- DO NOT `import firecrawl` inside a service, route, or parser.
- DO NOT use `httpx.get(...)` inside a parser.
- DO NOT return raw dicts from API routes (must be Pydantic models).
- DO NOT put business logic inside FastAPI route handlers.
- DO NOT use global mutable state outside the process-wide singletons
(settings, services, store, checkpointer, compiled graph).
- DO NOT use `print()` for debugging (use `logging`).
- DO NOT touch `storage/` from outside the service layer.

---

## 6. Data Rules

### Numeric fields must be numeric

| Field ↕▾ | Type ↕▾ | Example ↕▾ |
|---|---|---|
| −`price` | `float` | `579.0` |
| `discount_percentage` | `int | None` | `27` |
| `coins_save` | `int | None` | `29` |
| `sold_count` | `int | None` | `184` |
| `rating` | `float | None` | `4.6` |
| `rating_count` | `int | None` | `40` |
⚙

### Banned

```
{ "price": "Rs. 579" }
{ "discount": "27% Off" }
{ "coins_save": "Rs. 29" }
```

### Required

```
{ "price": 579.0, "currency": "PKR" }
{ "discount_percentage": 27 }
{ "coins_save": 29 }
```

### Missing data

Always `null`. Never fabricate. Never guess. Never default to `0` unless the
field is genuinely zero.

---

## 7. Approved Dependencies

Only these may be used without asking the user. This list reflects what is
actually installed in `.venv/`.

### Runtime

```
fastapi
uvicorn[standard]
pydantic
pydantic-settings
python-dotenv
httpx
firecrawl-py
```

### Chat layer (active)

```
langgraph
langchain-core
langchain-google-genai   # currently installed provider
```

If a different LLM provider is required (OpenAI, Anthropic, Groq, etc.),
ask the user first and swap the `langchain-*` provider package accordingly.

### Dev

```
pytest
pytest-asyncio
pytest-cov
ruff
mypy
pre-commit
```

### Installed but FORBIDDEN without explicit user request

```
playwright
```

Playwright is present in the environment but must not be used (Spec Section
5). If the user asks for it, confirm before writing any import.

### Rules

- Do not add a new dependency without asking the user first.
- Do not add Redis, Qdrant, Postgres, Celery, RQ, or any queue/vector-store
library in MVP. The JSON scrape store is explicitly allowed.

---

## 8. Folder & File Conventions

The package lives at `src/daraz_ai_shopping_assistant/`. This is the current
(verified) tree:

```
src/
`-- daraz_ai_shopping_assistant/
    |-- __init__.py
    |-- main.py                # FastAPI app entry point + lifespan
    |
    |-- api/
    |   |-- __init__.py        # api_router; search_router registered FIRST
    |   |-- chat.py            # /chat and /chat/stream
    |   |-- deps.py
    |   |-- exception_handlers.py
    |   |-- products.py
    |   `-- search.py
    |
    |-- models/
    |   |-- __init__.py
    |   |-- product.py         # Product, ProductDetails, Seller, Shipping,
    |   |                      # ProductVariant, Review
    |   `-- search.py          # SearchResult, SearchFilters, Pagination
    |
    |-- schemas/
    |   |-- __init__.py
    |   |-- chat.py            # ChatRequest, ChatResponse
    |   |-- product.py         # ProductDetailsResponse alias
    |   `-- search.py          # SearchResponse alias
    |
    |-- services/
    |   |-- __init__.py        # re-exports search + product; NOT chat (cycle)
    |   |-- chat_service.py    # facade over the LangGraph agent
    |   |-- product_service.py
    |   `-- search_service.py
    |
    |-- scrapers/
    |   |-- __init__.py
    |   |-- base.py            # abstract DarazScraper
    |   |-- daraz.py           # FirecrawlDarazScraper + URL builders + prompt
    |   `-- firecrawl.py       # FirecrawlAdapter -- ONLY importer of firecrawl
    |
    |-- parsers/
    |   |-- __init__.py
    |   `-- search_parser.py   # pure function: Markdown -> dict
    |
    |-- agents/
    |   |-- __init__.py
    |   |-- graph.py           # StateGraph, build_graph, get_compiled_graph
    |   |-- state.py           # AgentState, ParsedIntent, IntentType
    |   `-- tools.py           # thin wrappers around services
    |
    |-- storage/
    |   |-- __init__.py
    |   `-- json_store.py      # ScrapeStore -- local JSON persistence
    |
    |-- core/
    |   |-- __init__.py
    |   |-- config.py          # pydantic-settings Settings
    |   |-- exceptions.py      # DarazScraperError hierarchy
    |   `-- logging.py         # JSON + console formatters
    |
    `-- utils/
        |-- __init__.py
        `-- datetime.py        # PKT timezone helpers
```

Tests live at the repository root, outside `src/`:

```
tests/
|-- __init__.py
|-- conftest.py           # sets a dummy FIRECRAWL_API_KEY before any import
|-- unit/
|   |-- __init__.py
|   |-- test_agent_graph.py
|   |-- test_agent_state.py
|   |-- test_agent_tools.py
|   |-- test_chat_service.py
|   |-- test_core_config.py
|   |-- test_core_exceptions.py
|   |-- test_core_logging.py
|   |-- test_daraz_url_builder.py
|   |-- test_firecrawl_adapter.py
|   |-- test_firecrawl_daraz_scraper.py
|   |-- test_models_product.py
|   |-- test_models_search.py
|   |-- test_product_service.py
|   |-- test_scrape_store.py
|   |-- test_search_parser.py
|   `-- test_search_service.py
|-- api/
|   |-- __init__.py
|   |-- test_app.py
|   |-- test_chat_endpoint.py
|   |-- test_chat_stream.py
|   |-- test_product_endpoint.py
|   |-- test_product_endpoint_errors.py
|   `-- test_search_endpoint.py
|-- integration/          # reserved; not yet populated
`-- fixtures/
    |-- daraz_product_i927677133.md
    |-- daraz_product_i927677133_rendered.md
    `-- daraz_search_gaming_mouse.md
```

### Rules

- `models/` = Pydantic domain models (internal).
- `schemas/` = FastAPI request/response DTOs (may inherit from models).
- Do not create a top-level `app/` folder.
- Do not create new top-level packages outside
`src/daraz_ai_shopping_assistant/` without asking.
- Every new Python package folder must include an `__init__.py`.
- `services/__init__.py` deliberately does NOT re-export `ChatService` (import
cycle: `services/__init__ -> chat_service -> agents.graph -> agents.tools
-> services.product_service -> services/__init__`). Import it directly from
`daraz_ai_shopping_assistant.services.chat_service`.
- `api/__init__.py` registers `search_router` before `products_router` so
the fixed path `/products/search` wins over the parameterised
`/products/{product_id}`.
- `storage/` is a leaf. It must not import from `services/`, `agents/`,
`scrapers/`, or `parsers/`.

---

## 9. Pydantic Rules

- Every model subclasses `pydantic.BaseModel`.
- Use `model_config = ConfigDict(extra="forbid")` on response models to catch
parser drift.
- Use `Field(..., description="...")` on all non-obvious fields.
- `float | None` and `int | None` for optional numerics -- not
`Optional[float]` (prefer PEP 604).
- Never use `Any` unless unavoidable; document why.
- Run `model_validate()` on all parser output. On failure, log
`PRODUCT_VALIDATION_FAILED` and skip that product (do not fail the whole
request).

---

## 10. Parsing Rules

- Parsers are pure -- no I/O, no network, no globals.
- Input: raw Firecrawl Markdown (string).
- Output: `list[dict]` or an unvalidated dict -- do not return Pydantic models
from parsers; validate in the service.
- Regex over fragile string splitting where possible.
- Every parser must be testable with a Markdown fixture in `tests/fixtures/`.
- If a field is not found, return `None` -- do not skip the product unless
`id`, `title`, or `url` is missing.

---

## 11. Error Handling

Use the HTTP mapping from Spec Section 29:

| Situation ↕▾ | HTTP ↕▾ |
|---|---|
| −Invalid request params | 400 |
| Product not found | 404 |
| Rate limited (Firecrawl/Daraz) | 429 |
| Upstream scraping failure | 502 |
| Upstream timeout | 504 |
| Unexpected | 500 |
⚙

- Never leak stack traces. FastAPI exception handlers return
`{"detail": "..."}` only.
- Log full exception server-side with `logger.exception(...)`.
- Custom exceptions live in `core/exceptions.py`:

- `DarazScraperError` (base, 500)
- `InvalidRequestError` (400)
- `ProductNotFoundError` (404)
- `UpstreamRateLimitError` (429)
- `ScraperError` (502)
- `ScraperTimeoutError` (504)
- `ParseError` (502)

---

## 12. Logging Events

Emit these exact event names as log message prefixes (Spec Section 30). The
list below is the complete set currently emitted by the codebase.

App lifecycle (main.py):

```
APP_STARTED
APP_STOPPED
```

Search pipeline (SearchService):

```
SEARCH_STARTED
SEARCH_STORE_HIT
PRODUCTS_EXTRACTED
PRODUCT_VALIDATION_FAILED
SEARCH_COMPLETED
SEARCH_EMPTY
```

Product pipeline (ProductService):

```
PRODUCT_STORE_HIT
PRODUCT_FETCH_STARTED
PRODUCT_FETCH_COMPLETED
PRODUCT_FETCH_UNEXPECTED_ERROR
PRODUCT_VALIDATION_FAILED
PRODUCT_ID_MISSING_PREFIX
PRODUCT_ID_MISMATCH
```

Daraz scraper (FirecrawlDarazScraper):

```
DARAZ_SEARCH_FETCH
DARAZ_PRODUCT_FETCH
DARAZ_PRODUCT_PAYLOAD_SHAPE
```

Firecrawl adapter (FirecrawlAdapter):

```
FIRECRAWL_REQUEST
FIRECRAWL_RESPONSE
FIRECRAWL_RETRY
```

Local store (ScrapeStore, called from services and the app lifespan):

```
SCRAPE_STORE_CREATED
SCRAPE_STORE_LOADED
SCRAPE_STORE_LOAD_FAILED
SCRAPE_STORE_WRITE_FAILED
SCRAPE_STORE_LARGE
SCRAPE_STORE_PRUNED
SCRAPE_STORE_PRUNE_FAILED
```

Chat / agent layer:

```
CHAT_STARTED
CHAT_COMPLETED
CHAT_STREAM_STATE_LOOKUP_FAILED
CHAT_STREAM_FAILED
AGENT_INTENT_PARSED
AGENT_INTENT_PARSE_FAILED
AGENT_TOOL_SEARCH
AGENT_TOOL_GET_PRODUCT
AGENT_TOOL_FAILED
AGENT_RESPONSE_FAILED
```

API exception handlers:

```
API_ERROR
API_UNHANDLED_EXCEPTION
```

Include structured context via `extra={"ctx": {...}}`. Common keys: `query`,
`page`, `count`, `duration_ms`, `error_type`, `mode` (`"markdown"` or
`"json"`), `product_id`, `url`, `conversation_id`, `recommended_count`.

Never log: API keys, full HTML, user PII, or full store payloads.

---

## 13. Chat Layer Rules

The chat layer is implemented. The rules below describe how it behaves and
how new work on it must proceed. See ADR-002 in
`docs/ARCHITECTURE-DECISIONS.md` for the full rationale.

### The LLM does exactly two things

1. Classify the user's intent into a structured `ParsedIntent`
(`search`, `get_product`, `small_talk`).
2. Phrase a plain-language reply from the tool result.

Everything else in the chat pipeline is deterministic Python.

### Hard rules for any change to the agent layer

- The LLM may not see raw HTML or Markdown, ever. If a new node needs
product data, it gets it from a tool result, not from a scrape.
- The LLM may not extract product fields. Extraction stays in the scraper
and service layers.
- The LLM may not fabricate prices, IDs, URLs, ratings, review counts, or
seller info. Every product fact in a chat reply must trace back to a
Pydantic-validated `Product` or `ProductDetails` object.
- Intent parsing must use `with_structured_output(ParsedIntent)`. Free-text
classification is forbidden -- the graph routes on the structured object,
not on a regex over the LLM's prose.
- Tools call services, never scrapers and never Firecrawl directly.
- All tool results that flow back into the graph must already have passed
through Pydantic validation in the service layer.
- `DarazScraperError` raised inside a tool node is caught by the graph and
recorded on `AgentState.error`. Do not let it propagate to the API layer
from a tool.
- `ChatService` absorbs tool failures and reports them on
`ChatResponse.error`. Unhandled LLM errors (bad API key, network) still
propagate to FastAPI's global handlers.

### Conversation memory

- The graph is compiled with a checkpointer (`MemorySaver` in production).
- State is keyed by `thread_id`, set to the resolved `conversation_id`.
- The checkpointer holds full history; the LLM only sees the last 16
messages (8 turns) via `trim_messages`.
- State does NOT persist across process restarts by design.

### Streaming

- `ChatService.chat_stream` iterates `graph.astream_events(..., version="v2")`.
- Only tokens with `metadata.langgraph_node == "respond"` are forwarded.
Filtering by node is mandatory -- `parse_intent` also calls the LLM and
its output must never reach the client.
- The final `done` event carries `conversation_id`, `intent`,
and `error`.

### Adding new graph nodes

Follow the existing pattern in `agents/graph.py`:

- Node functions are closures over `resolved_llm` and live inside
`build_graph`.
- Nodes return a partial state dict (`{"intent": ...}`, `{"tool_result": ...}`,
`{"error": ...}`, or `{"messages": [...]}`).
- Routing is a pure function that reads `state["intent"]` and returns a node
name.
- Do not add tool-calling to the LLM. Routing is explicit and deterministic
because the graph already knows the intent.

---

## 14. Storage Layer Rules

`storage/ScrapeStore` is a leaf module that holds validated scrape payloads
in a JSON file on disk.

- Called ONLY by the service layer. Routes, scrapers, parsers, and agents
must never import `storage/`.
- The store is loaded once at app startup and attached to the service
singletons. Nothing else reads or writes the file.
- Writes are atomic (`tempfile` + `os.replace`).
- A missing, corrupt, or empty file is treated as an empty store -- the
app never crashes on bad persisted state.
- Expired entries are pruned at load and on shutdown.
- No eviction policy exists. Do not add one without asking. A one-shot
size warning is emitted at 10 MB.

---

## 15. Testing Rules

- Every parser has at least one unit test with a Markdown fixture.
- Every service has at least one service-layer test with the scraper mocked.
- `ScrapeStore` has full coverage: load, get, set, prune, corrupt-file
tolerance, atomicity.
- Every API route has a `TestClient` test with the service mocked via
`app.dependency_overrides`.
- The LLM is never called in tests. Use the `FakeLLM` pattern from
`tests/unit/test_agent_graph.py`, or mock `ChatService.chat` at the
endpoint boundary.
- Use `pytest-asyncio` for async tests (`@pytest.mark.asyncio`).
- Test names: `test_<unit>_<condition>_<expected>`.
- Do not hit real Daraz/Firecrawl in unit tests. Use fixtures + mocks.
- Tests that genuinely call Daraz/Firecrawl must be marked
`@pytest.mark.integration` and are deselected by default.
- Aim for >= 80% coverage on parsers and services.

---

## 16. Tooling -- uv

The project is managed with uv. All commands must go through `uv`:

```
uv add <package>             # ask first
uv add --dev <package>
uv sync
uv run uvicorn daraz_ai_shopping_assistant.main:app --reload
uv run pytest
uv run ruff check .
uv run mypy src
```

Do not use raw `pip`, `pip install`, or `python -m venv`. Use `uv`
exclusively.

---

## 17. Commit / Change Discipline

- One logical change per commit.
- Commit messages: `type(scope): summary` (Conventional Commits).

Examples:

- `feat(parser): extract discount percentage from search markdown`
- `fix(scraper): handle Firecrawl 429 with retry`
- `feat(chat): add SSE streaming endpoint`
- Never commit `.env`, secrets, `.venv/`, scraped HTML dumps, or the local
`data/` directory.
- Update `README.md` if a new env var or setup step is added.

---

## 18. When You Are Unsure

Ask the user. Specifically, ask before:

- Adding a new dependency
- Creating files outside Section 8 of this document
- Changing an existing Pydantic schema
- Introducing Playwright (which is installed but forbidden)
- Implementing anything from the Non-Goals list
- Choosing between two valid architectural approaches
- Renaming or moving the `daraz_ai_shopping_assistant` package
- Expanding the LLM's role beyond intent parsing and reply phrasing
- Adding an eviction policy to the scrape store

Do not guess. Do not silently improvise. Do not add "TODO: implement"
placeholders.

---

## 19. Quick Reference -- Spec Sections

| Topic ↕▾ | Spec Section ↕▾ |
|---|---|
| −Architecture | 4 |
| −Tech stack | 5 |
| −Search flow | 7 |
| −Search response schema | 8 |
| −Product schema | 9, 10 |
| −Data type rules | 11 |
| Missing data | 12 |
| Detailed product | 14 |
| LLM usage | 17 |
| Scraper interface | 19 |
| Firecrawl adapter | 20 |
| Current project structure | 21 |
| Endpoints | 22-25 |
| Error handling | 29 |
| Logging | 30 |
| Env vars | 31 |
| Testing | 32 |
| MVP order | 33 |
| Definition of Done | 34 |
| Installed dependencies | 36 |
| uv command reference | 37 |
| Package entry point | 38 |
| Environment / version notes | 39 |
| Architecture Decision Records | 40 |
| Chat Layer | 41 |
| Streaming & Conversation Memory | 42 |
⚙

---

## 20. Agent Checklist Before Every Response

Before you output code, verify:

- □  
Is the package path `daraz_ai_shopping_assistant.*` (no `app.*`)?
- □  
Does this respect the layering in Section 5?
- □  
Are all numeric fields actually numeric?
- □  
Is missing data `null` (never fabricated)?
- □  
Are all public functions typed and docstringed?
- □  
Is this within the approved dependency list (Section 7)?
- □  
Is this in the correct folder per Section 8?
- □  
Does this violate any Non-Goal (Section 3)?
- □  
Am I avoiding Playwright (Section 7)?
- □  
Am I avoiding direct `storage/` imports outside the service layer?
- □  
Have I asked the user when uncertain?

If any box is unchecked, fix it before responding.

---

End of AGENTS.md.
