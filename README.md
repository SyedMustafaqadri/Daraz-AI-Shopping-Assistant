# Daraz AI Shopping Assistant

The backend is implemented and verified for the Daraz.pk shopping workflow
through Phase 8, plus conversation memory, streaming, and a local scrape
store.

## Status

This project now includes:

- deterministic Daraz product search via Firecrawl Markdown scraping
- product detail retrieval using structured extraction plus Pydantic validation
- recommendation extraction from the same product payload
- a LangGraph-based chat endpoint with conversation memory
- Server-Sent Events streaming for the chat endpoint
- local JSON persistence of scrape payloads (search + product)
- strict FastAPI routing, typed error mapping, and validation across the stack

## Architecture at a glance

The service follows the project contract:

- API route -> service -> scraper -> Firecrawl adapter -> Daraz.pk
- search pages stay in deterministic Markdown parsing
- product and recommendation pages use Firecrawl structured extraction validated by Pydantic
- the chat layer runs only on top of validated service output and never invents product facts
- successful scrape payloads are stored in a local JSON file and served on repeat requests

## Package layout

The app uses a src-layout package:

- src/daraz_ai_shopping_assistant/
- main.py, api/, models/, schemas/, services/, scrapers/, parsers/,
  agents/, storage/, core/, utils/

There is no top-level app/ package.

## Prerequisites

- Python 3.14 (pinned via `.python-version`)
- uv (the only package manager used by this project)

## Quick start

1. Install dependencies and sync the repo environment:

```bash
uv sync
```

2. Copy the example environment file and fill in the required values:

```
copy .env.example .env
```

Required values:

- FIRECRAWL_API_KEY -- required for all scraping endpoints
- GOOGLE_API_KEY -- required only for the chat endpoints

3. Start the API server:

```
uv run uvicorn daraz_ai_shopping_assistant.main:app --reload --host 0.0.0.0 --port 8000
```

4. Open the docs (available when APP_ENV=dev):

- [http://localhost:8000/docs](http://localhost:8000/docs)
- [http://localhost:8000/redoc](http://localhost:8000/redoc)

## Core endpoints

- GET  /api/v1/products/search
- GET  /api/v1/products/{product_id}
- GET  /api/v1/products/{product_id}/recommendations
- POST /api/v1/chat           -- non-streaming JSON reply
- POST /api/v1/chat/stream    -- Server-Sent Events token stream

## Local scrape store

Successful search and product-detail scrapes are written to
`data/scrape_store.json`. A repeat request for the same key (same query +
filters + page, or same product id) returns the stored payload without
calling Firecrawl. The file is gitignored. Delete it to force a cold cache.

Default TTLs: 6 hours for search results, 24 hours for product details.
Override with `SCRAPE_STORE_SEARCH_TTL_SECONDS` and
`SCRAPE_STORE_PRODUCT_TTL_SECONDS` in `.env`.

## Conversation memory

The chat layer persists conversation state in memory, keyed by a
`conversation_id` you send in the request body. Omit it on the first turn;
the server generates one and returns it in the response. Send it back on
the next turn to continue the same conversation. State does not survive a
process restart -- each server run starts clean.

## Validation and checks

Run the project checks with uv:

```
uv run pytest tests/unit/ tests/api/ -v
uv run ruff check .
uv run mypy src
```

## Project docs

- Specification.md -- authoritative product requirements and architecture contract
- AGENTS.md -- AI agent guardrails for this repository
- [docs/README.md](https://docs/README.md) -- documentation index
- [docs/ARCHITECTURE.md](https://docs/ARCHITECTURE.md) -- layered architecture and data flow
- [docs/ARCHITECTURE-DECISIONS.md](https://docs/ARCHITECTURE-DECISIONS.md) -- ADR-001 and ADR-002
- [docs/API-REFERENCE.md](https://docs/API-REFERENCE.md) -- endpoint reference
- [docs/DATA-MODELS.md](https://docs/DATA-MODELS.md) -- Pydantic model reference
- [docs/CONFIGURATION.md](https://docs/CONFIGURATION.md) -- environment variables and settings
- [docs/SCRAPING.md](https://docs/SCRAPING.md) -- scraper and adapter internals
- [docs/PARSING.md](https://docs/PARSING.md) -- search parser internals
- [docs/ERRORS-AND-LOGGING.md](https://docs/ERRORS-AND-LOGGING.md) -- exceptions and structured logging
- [docs/TESTING.md](https://docs/TESTING.md) -- test approach and fixtures
- [docs/WORKFLOW.md](https://docs/WORKFLOW.md) -- uv commands, tooling, commit discipline

## Notes

- Product data is never invented; missing values stay null.
- Search pages are parsed deterministically; product pages are not.
- The LLM is bounded to intent routing and response generation; it does not
bypass validation or source-of-truth services.
- Every chat response includes a structured `recommended_products` list so
clients can render the top results without parsing the LLM's prose.

