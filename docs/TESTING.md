# Testing

The test suite lives at `tests/` (repository root, **outside** `src/`). It is
layered so that no test ever touches the real Daraz/Firecrawl network.

```
tests/
├── __init__.py
├── conftest.py           # sets FIRECRAWL_API_KEY dummy before any import
├── fixtures/
│   └── daraz_search_gaming_mouse.md   # frozen Markdown fixture
├── unit/
│   ├── test_core_config.py
│   ├── test_core_exceptions.py
│   ├── test_core_logging.py
│   ├── test_daraz_url_builder.py
│   ├── test_firecrawl_adapter.py
│   ├── test_firecrawl_daraz_scraper.py
│   ├── test_models_product.py
│   ├── test_models_recommendation.py
│   ├── test_models_search.py
│   ├── test_search_parser.py
│   └── test_search_service.py
└── api/
    ├── test_app.py
    └── test_search_endpoint.py
```

---

## 1. Strategy by Layer

| Layer | What is tested | How |
|---|---|---|
| **Unit** (config, exceptions, logging) | Settings validation, exception attributes, formatter output | Direct construction + `pytest` assertions |
| **Unit** (models) | Pydantic field constraints, validators (`url`, `scraped_at` tz-aware, price range) | Build valid/invalid instances, assert `ValidationError` |
| **Unit** (parser) | Regex extraction against a frozen fixture | `parse_search_results` on `tests/fixtures/daraz_search_gaming_mouse.md` |
| **Unit** (URL builders) | `build_search_url` / `build_product_url` string output | Pure-function asserts |
| **Unit** (adapter) | Both modes, retry + exception mapping | Monkeypatch `FirecrawlAdapter._client` with an async mock; no network |
| **Unit** (scraper) | `FirecrawlDarazScraper` delegates to the adapter | Inject a mock `FirecrawlAdapter` |
| **Service** | `SearchService.search` end-to-end | Inject a mock `DarazScraper` returning canned Markdown |
| **API** | Routes serialise and forward params correctly | `fastapi.testclient.TestClient` + dependency override with a mock service |

**Key principle:** the scraper is always mocked at or above the service
boundary. Unit tests never hit the network.

---

## 2. `conftest.py` — the Dummy API Key

`tests/conftest.py` sets `FIRECRAWL_API_KEY` to `fc-test-key` **before** any
test module is imported. This is because importing the FastAPI app triggers
`core.config.settings`, which fails at module-load time if the key is
missing.

```python
os.environ.setdefault("FIRECRAWL_API_KEY", "fc-test-key")
```

The value is never used against real Firecrawl (the scraper is always
mocked). Tests that want to exercise the *"missing key"* validation path use
`monkeypatch.delenv` and construct `Settings` with `_env_file=None` to bypass
the default.

---

## 3. Service-Test Pattern

`SearchService` takes an injected scraper. The standard pattern:

```python
def _make_scraper(markdown: str) -> MagicMock:
    scraper = MagicMock()
    scraper.fetch_search_markdown = AsyncMock(return_value=markdown)
    return scraper

service = SearchService(scraper=_make_scraper(SEARCH_MARKDOWN))
result = await service.search("gaming mouse", max_price=800)
```

This tests the full orchestration (fetch → parse → validate → envelope) with
a canned Markdown string and no I/O.

### Representative assertions

- Happy path: `len(result.products) == 2`, first product `id == "i111"`,
  `price == 579.0`, `location == "Punjab"`.
- `scraped_at` is timezone-aware and uses the `PKT` offset.
- Filters/page are forwarded to the scraper verbatim.
- Query whitespace is stripped before use.
- An empty page returns an empty (but valid) `SearchResult` and logs
  `SEARCH_EMPTY`.
- Invalid products (empty `id`, negative `price`) are **dropped**, with one
  `PRODUCT_VALIDATION_FAILED` log carrying the offending index.
- `search("   ")` raises `InvalidRequestError` and the scraper is **not**
  called.
- `min_price > max_price` raises a `ValidationError` from `SearchFilters`.

---

## 4. API-Test Pattern

`test_search_endpoint.py` wires a fresh app with a mocked service via
FastAPI's dependency-override mechanism:

```python
app = create_app()
app.dependency_overrides[get_search_service_dep] = lambda: mock_service
client = TestClient(app)
```

Coverage:

| Scenario | Expected |
|---|---|
| Valid query | `200`, fully-populated body, correct product fields |
| All params forwarded | `mock_service.search` awaited with exact args |
| Omitted optional params | Reach service as `None` / `1` |
| Missing `q` | `422` (FastAPI validation) |
| Blank `q` | `422` |
| Negative `min_price` | `422` |
| `page = 0` | `422` |
| `InvalidRequestError` raised | `400` with detail message |
| `ScraperError` raised | `502` |
| `UpstreamRateLimitError` raised | `429` |
| `ScraperTimeoutError` raised | `504` |
| Error with internal context | Response body is `{"detail": ...}` only — no URL or `upstream_status` leaks |

---

## 5. Adapter Tests (No Network)

`test_firecrawl_adapter.py` monkeypatches the adapter's `_client`:

```python
adapter._client = MagicMock()
adapter._client.scrape = AsyncMock(return_value=canned_document)
```

It verifies:

- `scrape` returns the Markdown string for all three response shapes
  (v2 Document, v2 dict, v1 wrapper).
- `scrape_json` returns the JSON payload.
- A transient timeout/rate-limit is retried with backoff and then raised as
  the correct exception type.
- A non-retryable failure raises immediately.
- `FIRECRAWL_REQUEST` / `FIRECRAWL_RESPONSE` / `FIRECRAWL_RETRY` are logged
  with the right `mode`.

---

## 6. Running the Suite

```bash
# Run everything (unit + api). Unit tests never hit the network.
uv run pytest

# Verbose
uv run pytest -v

# Coverage
uv run pytest --cov=daraz_ai_shopping_assistant --cov-report=term-missing

# Only integration tests (these hit real services — require a live key)
uv run pytest -m integration

# Deselect integration (the default CI behaviour)
uv run pytest -m "not integration"
```

`pyproject.toml` configures `asyncio_mode = "auto"`, so async tests need only
`@pytest.mark.asyncio()` (or run under the default). The `integration`
marker is for tests that genuinely call Daraz/Firecrawl.

### Goals

- ≥ 80% coverage on parsers and services.
- Every parser has at least one fixture-backed unit test.
- Every service has at least one integration-style test (scraper mocked).
- Every API route has a `TestClient` test.

---

## 7. Fixtures

`tests/fixtures/daraz_search_gaming_mouse.md` is a frozen copy of a real
Daraz search-results page (Markdown mode). It contains 8 product cards,
pagination links to page 102, and a trailing category section — the last
case deliberately exercises `_strip_trailing_sections`.

When adapting to a Daraz layout change (see [PARSING.md](PARSING.md) §6),
refresh or add a fixture here and update the matching parser test.
