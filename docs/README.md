# Daraz AI Shopping Assistant — Backend Documentation

This folder holds the engineering documentation for the **Daraz AI Shopping
Assistant — Backend**. The authoritative specification lives in
[`../Specification.md`](../Specification.md); agent guardrails live in
[`../AGENTS.md`](../AGENTS.md).

The implementation is active and verified through Phase 8: the backend includes
search, product detail retrieval, and a LangGraph-based chat
layer on top of the deterministic business services.

> Read `Specification.md` first for the project contract. Use this folder to
> understand the implemented behaviour in detail.

---

## Document index

| Document | What it covers |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | layered architecture, data flow, folder layout |
| [API-REFERENCE.md](API-REFERENCE.md) | endpoint contracts, examples, response conventions |
| [CONFIGURATION.md](CONFIGURATION.md) | environment variables and settings |
| [ERRORS-AND-LOGGING.md](ERRORS-AND-LOGGING.md) | exception mapping and structured logging |
| [TESTING.md](TESTING.md) | unit and API testing strategy |
| [WORKFLOW.md](WORKFLOW.md) | uv commands, tooling, commit discipline |
| [ARCHITECTURE-DECISIONS.md](ARCHITECTURE-DECISIONS.md) | ADR-001 and ADR-002 |

---

## Quick start

Prerequisites: Python 3.14 and uv.

```bash
uv sync
copy .env.example .env
uv run uvicorn daraz_ai_shopping_assistant.main:app --reload --host 0.0.0.0 --port 8000
```

At runtime, the required keys are:

- `FIRECRAWL_API_KEY`
- `GOOGLE_API_KEY` for the chat endpoint

The app serves OpenAPI docs at `/docs` in development.

---

## One-line mental model

```text
Route → Service → Scraper → FirecrawlAdapter → Firecrawl / Daraz.pk
              ↓
           Parser (search only)
              ↓
         Pydantic validation
              ↓
        JSON response to the client
```

The product and chat layers sit on top of the same validated backend services.
The LLM is not allowed to replace the core product pipeline; it merely routes
and summarises results from validated tool output.
