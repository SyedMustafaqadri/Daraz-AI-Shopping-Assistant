# Daraz AI Shopping Assistant

The backend is implemented and verified for the Daraz.pk shopping workflow through Phase 8.

## Status

This project now includes:

- deterministic Daraz product search via Firecrawl Markdown scraping
- product detail retrieval using structured extraction plus Pydantic validation
- recommendation extraction from the same product payload
- a LangGraph-based chat endpoint for natural-language shopping intents
- strict FastAPI routing, typed error mapping, and validation across the stack

## Architecture at a glance

The service follows the project contract:

- API route → service → scraper → Firecrawl adapter → Daraz.pk
- search pages stay in deterministic Markdown parsing
- product and recommendation pages use Firecrawl structured extraction validated by Pydantic
- the chat layer runs only on top of validated service output and never invents product facts

## Package layout

The app uses a src-layout package:

- src/daraz_ai_shopping_assistant/
- main.py, api/, models/, schemas/, services/, scrapers/, parsers/, agents/, core/

There is no top-level app/ package.

## Quick start

1. Install dependencies and sync the repo environment:

```bash
uv sync
```

2. Copy the example environment file and fill in the required values:

```bash
copy .env.example .env
```

Required values:

- FIRECRAWL_API_KEY
- GOOGLE_API_KEY when using the chat endpoint

3. Start the API server:

```bash
uv run uvicorn daraz_ai_shopping_assistant.main:app --reload --host 0.0.0.0 --port 8000
```

4. Open the docs:

- http://localhost:8000/docs
- http://localhost:8000/redoc

## Core endpoints

- GET /api/v1/products/search
- GET /api/v1/products/{product_id}
- GET /api/v1/products/{product_id}/recommendations
- POST /api/v1/chat

## Validation and checks

Run the project checks with uv:

```bash
uv run pytest tests/unit/ tests/api/ -v
uv run ruff check .
uv run mypy src
```

## Project docs

- Specification.md — authoritative product requirements and architecture contract
- docs/README.md — documentation index
- docs/API-REFERENCE.md — endpoint reference
- docs/ARCHITECTURE.md — layered architecture and data flow
- docs/ARCHITECTURE-DECISIONS.md — ADRs
- docs/CONFIGURATION.md — environment variables and settings
- docs/ERRORS-AND-LOGGING.md — exceptions and logging
- docs/TESTING.md — test approach and fixtures

## Notes

- Product data is never invented; missing values stay null.
- Search pages are parsed deterministically; product pages are not.
- The LLM is bounded to intent routing and response generation; it does not bypass validation or source-of-truth services.
