# Daraz AI Shopping Assistant — Backend Documentation

This folder holds the engineering documentation for the **Daraz AI Shopping
Assistant — Backend**. The single source of truth for architecture and
schemas is [`../Specification.md`](../Specification.md); the agent-facing
rules live in [`../AGENTS.md`](../AGENTS.md). The documents here are the
detailed, reference-grade companion that explains *how* the code works.

The system searches products on **Daraz.pk**, scrapes them through
**Firecrawl**, parses the content into validated **Pydantic models**, and
exposes clean REST endpoints via **FastAPI**. A future LangGraph agent layer
will sit on top of this deterministic backend without changing the scraping
services.

> **Read `Specification.md` first** for the authoritative spec. Use this
> folder to understand the implementation.

---

## Document Index

| Document | What it covers |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | Layered architecture, data flow, design principles, folder layout |
| [DATA-MODELS.md](DATA-MODELS.md) | Every Pydantic model: fields, types, validation rules |
| [API-REFERENCE.md](API-REFERENCE.md) | Endpoints, request/response shapes, error mapping, examples |
| [SCRAPING.md](SCRAPING.md) | `FirecrawlAdapter`, `FirecrawlDarazScraper`, URL builders, retry semantics |
| [PARSING.md](PARSING.md) | The deterministic search parser: regex patterns, block splitting |
| [ERRORS-AND-LOGGING.md](ERRORS-AND-LOGGING.md) | Exception hierarchy, HTTP mapping, structured logging events |
| [CONFIGURATION.md](CONFIGURATION.md) | All `Settings` fields, environment variables, `.env` setup |
| [TESTING.md](TESTING.md) | Testing strategy, layers, how to run the suite |
| [WORKFLOW.md](WORKFLOW.md) | Dev environment setup, `uv` commands, commit discipline |
| [ARCHITECTURE-DECISIONS.md](ARCHITECTURE-DECISIONS.md) | Binding Architecture Decision Records (ADRs) |

---

## Quick Start

Prerequisites: **Python 3.14** and **uv** (the package manager). The project
uses a `src/` layout; the package is `daraz_ai_shopping_assistant` (there is
no `app/` folder).

```bash
# 1. Sync the environment from uv.lock
uv sync

# 2. Create a .env at the repository root with a Firecrawl API key
#    FIRECRAWL_API_KEY=fc-...

# 3. Run the API server
uv run uvicorn daraz_ai_shopping_assistant.main:app --reload --host 0.0.0.0 --port 8000
```

In development (`APP_ENV=dev`), OpenAPI docs are served at
<http://localhost:8000/docs>.

```bash
# 4. Run the test suite
uv run pytest

# 5. Lint and type-check
uv run ruff check .
uv run mypy src
```

Try the live endpoint (requires a valid `FIRECRAWL_API_KEY`):

```
GET /api/v1/products/search?q=gaming%20mouse&max_price=800
```

---

## The One-Line Mental Model

```
Route → Service → Scraper → FirecrawlAdapter → Firecrawl → Daraz.pk
           ↓
        Parser
           ↓
      Pydantic models (the contract)
```

Every layer above the scraper calls the layer below and never reaches past
it. Services never import `firecrawl`; only `scrapers/firecrawl.py` may.
Parsers are pure functions. The Pydantic model is always the final contract
— nothing from the wire ever reaches an API consumer without passing through
`model_validate`.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full picture.
