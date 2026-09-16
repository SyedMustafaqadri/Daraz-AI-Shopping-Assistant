# Developer Workflow

Covers environment setup, the `uv` command surface, and commit discipline.
The authoritative agent-facing rules live in [`../AGENTS.md`](../AGENTS.md);
this document is the day-to-day how-to.

---

## 1. Prerequisites

- **Python 3.14** — pinned via `.python-version`.
- **uv** — the *only* package manager for this project. Never use raw
  `pip`, `pip install`, or `python -m venv`.

All commands are run through `uv run ...` (or after activating `.venv/`
manually).

---

## 2. First-Time Setup

```bash
# 1. Clone / open the repo, then sync the locked environment.
uv sync

# 2. Create a .env at the repository root.
#    (See CONFIGURATION.md for every variable.)
#    FIRECRAWL_API_KEY=fc-...

# 3. Sanity-check: run the tests.
uv run pytest

# 4. Run the server.
uv run uvicorn daraz_ai_shopping_assistant.main:app --reload --host 0.0.0.0 --port 8000
```

Open <http://localhost:8000/docs> in development.

---

## 3. `uv` Command Reference

| Task | Command |
|---|---|
| Sync environment from `uv.lock` | `uv sync` |
| Add a runtime dependency | `uv add <package>` |
| Add a dev dependency | `uv add --dev <package>` |
| Pin the Python version | `uv python pin 3.14` |
| Run the FastAPI server | `uv run uvicorn daraz_ai_shopping_assistant.main:app --reload` |
| Run tests | `uv run pytest` |
| Lint / format | `uv run ruff check .` · `uv run ruff format .` |
| Type-check | `uv run mypy src` |

> **Entry point is `daraz_ai_shopping_assistant.main:app`**, never
> `app.main:app` — this is a `src/` layout.

---

## 4. Tooling Configuration

### Ruff

Configured in `pyproject.toml`:

- `line-length = 100`, `target-version = "py314"`.
- Lint rules: `E, F, W, I, N, UP, B, C4, SIM, RUF`.
- FastAPI DI callables (`Depends`, `Query`, `Path`, ...) are whitelisted for
  the "call in default" rule (`flake8-bugbear`).
- Test files ignore `DTZ001` (naive datetimes) and `S101` (asserts).

### mypy

- `strict = true`, `python_version = "3.14"`, `mypy_path = "src"`.
- `firecrawl.*` is marked `ignore_missing_imports` (the SDK ships without
  stubs).

### pytest

- `testpaths = ["tests"]`, `pythonpath = ["src"]`.
- `asyncio_mode = "auto"` — async tests run without explicit markers.
- Custom marker: `integration` (tests that hit real upstream services).
- `addopts` includes `-ra --strict-markers --strict-config`.

---

## 5. Commit Discipline

- **One logical change per commit.**
- Conventional-Commits messages: `type(scope): summary`.

```
feat(parser): extract discount percentage from search markdown
fix(scraper): handle Firecrawl 429 with retry
test(api): assert error body does not leak internal context
docs: add scraping-layer reference
```

- Never commit `.env`, secrets, `.venv/`, or scraped HTML dumps.
- Update `README.md` whenever a new env var or setup step is added.

---

## 6. Before Submitting Code

Mirror the AGENT.md checklist:

- [ ] Package path is `daraz_ai_shopping_assistant.*` (no `app.*`).
- [ ] Layering respected (route → service → scraper → adapter).
- [ ] All numeric fields are numeric; missing data is `null`.
- [ ] No `import firecrawl` outside `scrapers/firecrawl.py`.
- [ ] No Playwright usage (unless explicitly requested).
- [ ] Public functions are typed and docstringed.
- [ ] Within the approved dependency list — new deps flagged to the user.
- [ ] `uv run ruff check .` and `uv run mypy src` pass.
- [ ] `uv run pytest` passes.

---

## 7. Non-Goals — Stop and Ask

If a task seems to require any of the following, stop and ask the user first
(see AGENT.md §3 and Spec §3):

- Custom recommendation algorithm
- Vector DB / embeddings / Qdrant
- Redis / caching
- Authentication or payments
- Price tracking / notifications
- Review sentiment analysis
- Multi-marketplace (only Daraz.pk in MVP)
- Frontend code
- Database persistence (until requested)
- Async job queues (Celery, RQ)

Also ask before: adding a dependency, creating files outside the §8 layout,
changing a Pydantic schema, or introducing Playwright.
