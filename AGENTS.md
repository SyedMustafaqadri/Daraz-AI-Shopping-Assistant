# AGENT.md — AI Agent Instructions for Daraz AI Shopping Assistant Backend

> **Audience:** AI coding agents (Claude Code, Cursor, Copilot, Aider, Codex, etc.) working on this repository.
> **Read this file first.** It overrides generic defaults. Follow it precisely.
> **Companion document:** `Specification.md` — the single source of truth for architecture, schemas, and workflows.

---

## 1. Project Identity

You are working on the **Daraz AI Shopping Assistant — Backend**.

The system:

- Searches **Daraz.pk** products via natural or structured queries.
- Scrapes Daraz using **Firecrawl**.
- Parses scraped Markdown into **validated Pydantic models**.
- Exposes clean REST APIs via **FastAPI**.
- Extracts **Daraz's own recommendations** from product pages.
- Will later add a **LangGraph** agent layer on top of the deterministic backend.

**The deterministic backend must work perfectly before any AI layer is added.**

---

## 2. Prime Directives

When working on this repo, you MUST:

1. **Follow `Specification.md` as the single source of truth.** If code and spec conflict, the spec wins — unless the user explicitly overrides it in the current task.
2. **Never invent product data.** Prices, IDs, URLs, ratings, seller info must come from scraped content or be `null`.
3. **Never skip Pydantic validation.** Every product object returned by an API route must pass through a Pydantic model.
4. **Never bypass the `DarazScraper` interface.** API routes → services → `DarazScraper` → `FirecrawlAdapter`. Never call Firecrawl directly from a route or service.
5. **Never install libraries outside the approved list** (§7). Ask before adding new dependencies.
6. **Never use Playwright** unless the user explicitly requests it (Spec §5). It is installed but **must not be used**.
7. **Never add Redis, Qdrant, Postgres, or background job infrastructure** in MVP (Spec §3).
8. **Ask before creating files** outside the structure in §8 of this document, unless the user explicitly requests them.
9. **Prefer small, focused, testable functions** over large monolithic ones.
10. **Write code like a senior engineer** — typed, documented, error-handled, no placeholders.

---

## 3. Non-Goals (Do NOT Implement)

If a task appears to require any of the following, **stop and ask the user**:

- Custom recommendation algorithm
- Vector databases / embeddings / Qdrant
- Redis / caching layer
- User authentication or payments
- Price tracking / notifications
- Review sentiment analysis
- Multi-marketplace support (only Daraz.pk)
- Frontend code
- Database persistence (until explicitly requested)
- Async job queues (Celery, RQ, etc.)

---

## 4. Critical — Package Layout

**This project uses a `src/` layout.** There is **NO** `app/` folder.

- **Package name:** `daraz_ai_shopping_assistant`
- **Package location:** `src/daraz_ai_shopping_assistant/`
- **FastAPI entry point:** `daraz_ai_shopping_assistant.main:app`
- **Run command:**

```bash
  uv run uvicorn daraz_ai_shopping_assistant.main:app --reload --host 0.0.0.0 --port 8000
```

### Forbidden patterns

- ❌ Creating an `app/` folder at the repository root.
- ❌ Writing `app.main:app` in any command, docstring, or README.
- ❌ Importing like `from app.models.product import Product`.
- ❌ Placing business logic at the repository root outside `src/daraz_ai_shopping_assistant/`.

### Correct import style

```
from daraz_ai_shopping_assistant.models.product import Product
from daraz_ai_shopping_assistant.services.search_service import search_products
from daraz_ai_shopping_assistant.core.config import settings
```

---

## 5. Architecture Rules

### Layering (strict — do not violate)

```
API Route  →  Service  →  Scraper  →  FirecrawlAdapter  →  Firecrawl API
                ↓
             Parser
                ↓
        Pydantic Models
```

### Hard rules

- **Routes** (`api/`) must contain **no business logic**. They call services and return response schemas.
- **Services** (`services/`) orchestrate: build URL → call scraper → call parser → validate → return. They do **not** know Firecrawl exists.
- **Scrapers** (`scrapers/`) own the interface. `DarazScraper` calls `FirecrawlAdapter`. `FirecrawlAdapter` is the **only** place that imports `firecrawl`.
- **Parsers** (`parsers/`) are **pure functions**: Markdown in → dict/Pydantic out. No I/O, no network.
- **Models** (`models/`) hold Pydantic domain models (internal).
- **Schemas** (`schemas/`) hold FastAPI request/response DTOs (may inherit from models).
- **Core** (`core/config.py`) holds settings loaded from `.env`.

### Forbidden patterns

- ❌ `import firecrawl` inside a service, route, or parser.
- ❌ `httpx.get(...)` inside a parser.
- ❌ Returning raw dicts from API routes (must be Pydantic models).
- ❌ Business logic inside FastAPI route handlers.
- ❌ Global mutable state.
- ❌ `print()` for debugging (use `logging`).

---

## 6. Data Rules

### Numeric fields must be numeric

| Field ↕▾ | Type ↕▾ | Example ↕▾ |
|---|---|---|
| −`price` | `float` | `579.0` |
| −`discount_percentage` | `int | None` | `27` |
| −`coins_save` | `float | None` | `29.0` |
| −`sold_count` | `int | None` | `184` |
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
{ "coins_save": 29.0 }
```

### Missing data

Always `null`. **Never** fabricate. **Never** guess. **Never** default to `0` unless the field is genuinely zero (e.g., `recommendations: []`).

---

## 7. Approved Dependencies

Only these may be used without asking the user. This list reflects what is **actually installed** in `.venv/`.

### Runtime (MVP)

```
fastapi
uvicorn[standard]
pydantic
pydantic-settings
python-dotenv
httpx
firecrawl-py
```

### AI layer (Phase 8 only)

```
langgraph
langchain-core
langchain-google-genai   # currently installed provider
```

If a different LLM provider is required (OpenAI, Anthropic, Groq, etc.), **ask the user first** and swap the `langchain-*` provider package accordingly.

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

Playwright is present in the environment but **must not be used** (Spec §5). If the user asks for it, confirm before writing any import.

### Rules

- Do not add a new dependency without asking the user first.
- Do not add Redis, Qdrant, Postgres, Celery, RQ, or any queue/vector-store library in MVP.

---

## 8. Folder & File Conventions

The package lives at `src/daraz_ai_shopping_assistant/`. Build files **inside** that package:

```
src/
└── daraz_ai_shopping_assistant/
    ├── __init__.py
    ├── main.py                # FastAPI app entry point
    │
    ├── api/
    │   ├── __init__.py
    │   ├── search.py
    │   ├── products.py
    │   └── chat.py            # placeholder until Phase 8
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
    │   ├── product_parser.py
    │   └── recommendation_parser.py
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

Tests live at the repository root, **outside** `src/`:

```
tests/
├── __init__.py
├── unit/
├── integration/
├── api/
└── fixtures/
```

### Rules

- `models/` = Pydantic domain models (internal).
- `schemas/` = FastAPI request/response DTOs (may inherit from models).
- Do **not** create a top-level `app/` folder.
- Do **not** create new top-level packages outside `src/daraz_ai_shopping_assistant/` without asking.
- Every new Python package folder must include an `__init__.py`.

---

## 9. Pydantic Rules

- Every model subclasses `pydantic.BaseModel`.
- Use `model_config = ConfigDict(extra="forbid")` on response models to catch parser drift.
- Use `Field(..., description="...")` on all non-obvious fields.
- `float | None` and `int | None` for optional numerics — **not** `Optional[float]` (prefer PEP 604).
- Never use `Any` unless unavoidable; document why.
- Run `model_validate()` on all parser output. On failure, log `PRODUCT_VALIDATION_FAILED` and skip that product (do not fail the whole request).

---

## 10. Parsing Rules

- Parsers are **pure** — no I/O, no network, no globals.
- Input: raw Firecrawl Markdown (string).
- Output: `list[dict]` or an unvalidated `SearchResult` — **do not** return Pydantic models from parsers; validate in the service.
- Regex over fragile string splitting where possible.
- Every parser must be **testable with a Markdown fixture** in `tests/fixtures/`.
- If a field is not found, return `None` — do not skip the product unless `id`, `title`, or `url` is missing.

---

## 11. Error Handling

Use the HTTP mapping from Spec §29:

| Situation ↕▾ | HTTP ↕▾ |
|---|---|
| −Invalid request params | 400 |
| −Product not found | 404 |
| −Rate limited (Firecrawl/Daraz) | 429 |
| −Upstream scraping failure | 502 |
| Unexpected | 500 |
⚙

- Never leak stack traces. FastAPI exception handlers return `{"detail": "..."}` only.
- Log full exception server-side with `logger.exception(...)`.
- Define custom exceptions in `core/exceptions.py`:

- `ScraperError`
- `ScraperTimeoutError`
- `ParseError`
- `ProductNotFoundError`
- `UpstreamRateLimitError`

---

## 12. Logging Events

Emit these exact event names as log message prefixes (Spec §30):

```
SEARCH_STARTED
FIRECRAWL_REQUEST
FIRECRAWL_RESPONSE
PARSING_STARTED
PRODUCTS_EXTRACTED
PRODUCT_VALIDATION_FAILED
SEARCH_COMPLETED
PRODUCT_FETCH_STARTED
PRODUCT_FETCH_COMPLETED
RECOMMENDATIONS_EXTRACTED
```

Include structured context: `query`, `page`, `count`, `duration_ms`, `error_type`.

**Never log:** API keys, full HTML, user PII.

---

## 13. LLM / AI Layer Rules (Phase 8+)

Until Phase 8 is explicitly activated by the user:

- Do **not** import LangGraph, `langchain-core`, or `langchain-google-genai` in production code paths.
- Do **not** create working code in `agents/*.py` beyond empty placeholder modules.
- Do **not** add `POST /api/v1/chat` implementation.

When Phase 8 begins:

- LLM is used **only** for: query understanding, title normalization, feature extraction, category classification.
- LLM must **never** fabricate: prices, IDs, URLs, ratings, review counts, seller info.
- All LLM outputs that touch product data must pass through Pydantic validation.
- LangGraph tools call **services**, never scrapers or Firecrawl directly.

---

## 14. Testing Rules

- Every parser has at least one unit test with a Markdown fixture.
- Every service has at least one integration test (Firecrawl may be mocked).
- Every API route has a `TestClient` test.
- Use `pytest-asyncio` for async tests (`@pytest.mark.asyncio`).
- Test names: `test_<unit>_<condition>_<expected>`.
- Do **not** hit real Daraz/Firecrawl in unit tests. Use fixtures + `respx` or `httpx.MockTransport`.
- Aim for ≥ 80% coverage on parsers and services.

---

## 15. Tooling — uv

The project is managed with **uv**. All commands must go through `uv`:

```
# Add a runtime dependency (ask first)
uv add <package>

# Add a dev dependency
uv add --dev <package>

# Sync environment from uv.lock
uv sync

# Run the FastAPI server
uv run uvicorn daraz_ai_shopping_assistant.main:app --reload

# Run tests
uv run pytest

# Lint
uv run ruff check .

# Type check
uv run mypy src
```

Do **not** use raw `pip`, `pip install`, or `python -m venv`. Use `uv` exclusively.

---

## 16. Commit / Change Discipline

- One logical change per commit.
- Commit messages: `type(scope): summary` (Conventional Commits).

- `feat(parser): extract discount percentage from search markdown`
- `fix(scraper): handle Firecrawl 429 with retry`
- Never commit `.env`, secrets, `.venv/`, or scraped HTML dumps.
- Update `README.md` if a new env var or setup step is added.

---

## 17. When You Are Unsure

Ask the user. Specifically, ask before:

- Adding a new dependency
- Creating files outside §8 of this document
- Changing an existing Pydantic schema
- Introducing Playwright (which is installed but forbidden)
- Implementing anything from the Non-Goals list
- Choosing between two valid architectural approaches
- Renaming or moving the `daraz_ai_shopping_assistant` package

Do **not** guess. Do **not** silently improvise. Do **not** add "TODO: implement" placeholders.

---

## 18. Quick Reference — Spec Sections

| Topic ↕▾ | Spec § ↕▾ |
|---|---|
| −Architecture | 4 |
| −Tech stack | 5 |
| −Search flow | 7 |
| −Search response schema | 8 |
| Product schema | 9, 10 |
| Data type rules | 11 |
| Missing data | 12 |
| Detailed product | 14 |
| Recommendations | 15, 16 |
| LLM usage | 17 |
| Scraper interface | 19 |
| Firecrawl adapter | 20 |
| **Current project structure** | **21** |
| Endpoints | 22–25 |
| Error handling | 29 |
| Logging | 30 |
| Env vars | 31 |
| Testing | 32 |
| MVP order | 33 |
| Definition of Done | 34 |
| **Installed dependencies** | **36** |
| **uv command reference** | **37** |
| **Package entry point** | **38** |
| **Environment / version notes** | **39** |
⚙

---

## 19. Agent Checklist Before Every Response

Before you output code, verify:

- □  
Is the package path `daraz_ai_shopping_assistant.*` (no `app.*`)?
- □  
Does this respect the layering in §5?
- □  
Are all numeric fields actually numeric?
- □  
Is missing data `null` (never fabricated)?
- □  
Are all public functions typed and docstringed?
- □  
Is this within the approved dependency list (§7)?
- □  
Is this in the correct folder per §8?
- □  
Does this violate any Non-Goal (§3)?
- □  
Am I avoiding Playwright (§7)?
- □  
Have I asked the user when uncertain?

If any box is unchecked, fix it before responding.

---

**End of AGENT.md**

