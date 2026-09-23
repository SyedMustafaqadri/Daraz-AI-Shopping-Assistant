# Testing

The test suite lives at `tests/` (repository root, outside `src/`). It is
layered so that no test ever touches the real Daraz/Firecrawl network unless
it is explicitly marked `@pytest.mark.integration`.

```

tests/
|-- **init**.py
|-- conftest.py                          # sets a dummy FIRECRAWL_API_KEY
|-- fixtures/
|   |-- daraz_product_i927677133.md
|   |-- daraz_product_i927677133_rendered.md
|   `-- daraz_search_gaming_mouse.md
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
`-- api/
    |-- __init__.py
    |-- test_app.py
    |-- test_chat_endpoint.py
    |-- test_chat_stream.py
    |-- test_product_endpoint.py
    |-- test_product_endpoint_errors.py
    `-- test_search_endpoint.py

```

---

## 1. Strategy by Layer

| Layer                                | What is tested                                                                      | How                                                                  |
|--------------------------------------|-------------------------------------------------------------------------------------|----------------------------------------------------------------------|
| Unit (config, exceptions, logging)   | Settings validation, exception attributes, formatter output                         | Direct construction + `pytest` assertions                            |
| Unit (models)                        | Pydantic field constraints, validators (`url`, `scraped_at` tz-aware, price range)  | Build valid/invalid instances, assert `ValidationError`              |
| Unit (parser)                        | Regex extraction against frozen fixtures                                            | `parse_search_results` on `tests/fixtures/daraz_search_gaming_mouse.md` |
| Unit (URL builders)                  | `build_search_url` / `build_product_url` string output                              | Pure-function asserts                                                |
| Unit (adapter)                       | Both modes, retry + exception mapping, response-shape fallbacks                     | Monkeypatch `FirecrawlAdapter._client` with an async mock            |
| Unit (scraper)                       | `FirecrawlDarazScraper` delegates to the adapter with correct args                  | Inject a mock `FirecrawlAdapter`                                     |
| Unit (storage)                       | `ScrapeStore` load/set/get/prune, atomic writes, corrupt-file tolerance             | `tmp_path` per test; no shared state                                 |
| Service (`SearchService`)            | Fetch -> parse -> validate -> envelope; store hit/miss on both paths                | Inject a mock `DarazScraper` and a temp `ScrapeStore`                |
| Service (`ProductService`)           | Fetch -> validate -> normalise; store hit/miss                                       | Same                                                                 |
| Agent (state, tools, graph)          | Intent schema, tool wrappers, graph routing, error handling                         | Mock the LLM (`FakeLLM`), patch tool functions on the graph module   |
| Service (`ChatService`)              | State construction, reply extraction, error paths                                   | Inject a mock compiled graph via the constructor                     |
| API (all routes)                     | Serialisation, param forwarding, error mapping, SSE framing, no-leak guarantees     | `fastapi.testclient.TestClient` + `app.dependency_overrides`         |

**Key principles:**

- The scraper is always mocked at or above the service boundary.
- The LLM is never called in unit or API tests.
- Unit tests never hit the network.
- The store is always a temporary directory under `tmp_path`.

---

## 2. `conftest.py` -- the Dummy API Key

`tests/conftest.py` sets `FIRECRAWL_API_KEY` to `fc-test-key` before any test
module is imported. This is because importing the FastAPI app triggers
`core.config.settings`, which fails at module-load time if the key is
missing.

```python
os.environ.setdefault("FIRECRAWL_API_KEY", "fc-test-key")
```

The value is never used against real Firecrawl (the scraper is always
mocked). Tests that want to exercise the "missing key" validation path use
`monkeypatch.delenv` and construct `Settings` with `_env_file=None` to
bypass the default.

---

## 3. Service-Test Patterns

Both services take an injected scraper and an optional injected store. The
standard pattern is a small factory that returns a `MagicMock` with an
`AsyncMock` method, plus a `ScrapeStore` rooted in `tmp_path`.

### SearchService

```
def _make_scraper(markdown: str) -> MagicMock:
    scraper = MagicMock()
    scraper.fetch_search_markdown = AsyncMock(return_value=markdown)
    return scraper

service = SearchService(scraper=_make_scraper(SEARCH_MARKDOWN))
result = await service.search("gaming mouse", max_price=800)
```

This tests the full orchestration (fetch -> parse -> validate -> envelope)
with a canned Markdown string and no I/O.

Representative assertions:

- Happy path: `len(result.products) == 2`, first product `id == "i111"`,
`price == 579.0`, `location == "Punjab"`.
- `scraped_at` is timezone-aware and uses the `PKT` offset.
- Filters/page are forwarded to the scraper verbatim.
- Query whitespace is stripped before use.
- An empty page returns an empty (but valid) `SearchResult` and logs
`SEARCH_EMPTY`.
- Invalid products (empty `id`, negative `price`) are dropped, with one
`PRODUCT_VALIDATION_FAILED` log carrying the offending index.
- `search("   ")` raises `InvalidRequestError` and the scraper is not
called.
- `min_price > max_price` raises a `ValidationError` from `SearchFilters`.
- With a store: a second identical request returns without a second scraper
call (`SEARCH_STORE_HIT` in logs, `fetch_search_markdown` awaited once).

### ProductService

```
def _make_scraper(payload: dict) -> MagicMock:
    scraper = MagicMock()
    scraper.fetch_product_payload = AsyncMock(return_value=payload)
    return scraper

service = ProductService(scraper=_make_scraper(PRODUCT_PAYLOAD))
product = await service.get_product("i1959941878")
```

Representative assertions:

- Happy path returns a fully-populated `ProductDetails`.
- An empty payload (`{}` or `None`) raises `ProductNotFoundError`.
- A payload that fails Pydantic validation raises `ParseError(source="product")`
and logs `PRODUCT_VALIDATION_FAILED`.
- Invalid IDs (`""`, `"abc"`, `"1959941878"` missing the `i`) raise
`InvalidRequestError` before the scraper is called.
- A typed scraper error propagates unchanged (not wrapped).
- `_normalise_product_id` repairs a stripped `i` prefix and logs
`PRODUCT_ID_MISSING_PREFIX`.
- With a store: a second request for the same id returns without a second
scraper call (`PRODUCT_STORE_HIT` in logs).

### ScrapeStore

- Load from a missing path creates the parent directory.
- Load from a missing file yields an empty store.
- Load from a corrupt file yields an empty store (no raise).
- Load from a store with expired entries prunes them.
- `set` then `get` returns the payload verbatim.
- A `set` writes the file immediately.
- Expiry enforced on read.
- `prune()` removes expired entries and reports the count.
- Concurrent `set` calls serialise without loss.
- Malformed entries are treated as expired.

---

## 4. API-Test Pattern

Every route is tested with a fresh app and a mocked service wired via
FastAPI's dependency-override mechanism:

```
app = create_app()
app.dependency_overrides[get_search_service_dep] = lambda: mock_service
client = TestClient(app)
```

Each `tests/api/test_*.py` module uses its own fixture that yields a
`TestClient` and clears `app.dependency_overrides` on teardown.

### `test_search_endpoint.py`

| Scenario ↕▾ | Expected ↕▾ |
|---|---|
| −Valid query | `200`, fully-populated body, correct product fields |
| −All params forwarded | `mock_service.search` awaited with exact args |
| −Omitted optional params | Reach service as `None` / `1` |
| −Missing `q` | `422` |
| −Blank `q` | `422` |
| −Negative `min_price` | `422` |
| −`page = 0` | `422` |
| −`InvalidRequestError` raised | `400` with detail message |
| −`ScraperError` raised | `502` |
| −`UpstreamRateLimitError` | `429` |
| −`ScraperTimeoutError` | `504` |
| −Error with internal context | Body is `{"detail": ...}` only -- no URL or `upstream_status` leaks |
⚙

### `test_product_endpoint.py` and `test_product_endpoint_errors.py`

| Scenario ↕▾ | Expected ↕▾ |
|---|---|
| −Valid product id | `200`, serialised `ProductDetails` |
| −Path id not matching `^i\d+$` | `422` |
| −`/products/search` resolves to search endpoint | Search service called, product service NOT called |
| −`InvalidRequestError` raised | `400` |
| −`ProductNotFoundError` raised | `404` |
| −`ParseError` raised | `502` |
| −`ScraperError` raised | `502` |
| −`UpstreamRateLimitError` raised | `429` |
| −`ScraperTimeoutError` raised | `504` |
| −Error with internal context | Body is `{"detail": ...}` only |
⚙

### `test_chat_endpoint.py`

| Scenario ↕▾ | Expected ↕▾ |
|---|---|
| −Happy path with a reply and data | `200`, `reply` / `intent` / `data` present |
| −Small talk (no data) | `200`, `intent == "small_talk"`, `data is None` |
| −Empty message body | `422` |
| −Missing message field | `422` |
| −Message over 2000 chars | `422` |
| −Extra undeclared field | `422` |
| −`InvalidRequestError` raised | `400` |
| −`ScraperError` raised | `502` |
| −`ScraperTimeoutError` raised | `504` |
⚙

### `test_chat_stream.py`

| Scenario ↕▾ | Expected ↕▾ |
|---|---|
| −Stream emits tokens + done | `text/event-stream`; token frames + `done` frame + `[DONE]` |
| −Empty stream | Body ends with `data: [DONE]` |
| −Missing conversation_id accepted | `200` |
| −Empty message body | `422` |
| −Internal error during stream | SSE error frame, then `[DONE]`; still `200` |
⚙

### `test_app.py`

Covers the shell: `/health`, `/`, OpenAPI schema presence, the catch-all
500 handler (no internal detail leaks), and a dependency-override smoke
test.

---

## 5. Adapter Tests (No Network)

`test_firecrawl_adapter.py` monkeypatches the adapter's `_client`:

```
adapter._client = MagicMock()
adapter._client.scrape = AsyncMock(return_value=canned_document)
```

It verifies:

- `scrape` returns the Markdown string for all three response shapes
(v2 Document, v2 dict, v1 wrapper).
- `scrape_json` returns the JSON payload from all three shapes.
- The JSON format dict sent to the SDK has the current Firecrawl shape:
`{"type": "json", "prompt": ..., "schema": ...}` inside the `formats`
list. The legacy `json_options` key is absent.
- The default prompt fallback (`_DEFAULT_JSON_PROMPT`) is used when the
caller passes `prompt=None`.
- `wait_for`, `only_main_content`, and `max_age` are forwarded when set and
omitted when unset.
- A transient timeout or rate limit is retried with exponential backoff and
eventually raised as the correct exception type.
- A non-retryable failure raises immediately (no retry loop).
- `FIRECRAWL_REQUEST` / `FIRECRAWL_RESPONSE` / `FIRECRAWL_RETRY` are logged
with the right `mode` (`"markdown"` or `"json"`).

---

## 6. Mocking the LLM (Agent Tests)

The agent layer is tested without any LLM in the loop. Three patterns are
used, and they cover different layers.

### `FakeLLM` in `test_agent_graph.py`

A hand-written fake that mirrors the two call paths the graph uses:

```
class FakeLLM:
    def __init__(self, parsed_intent, reply_text="..."):
        self._parsed_intent = parsed_intent
        self._reply_text = reply_text

    def with_structured_output(self, _schema):
        return _FakeStructuredRunnable(self._parsed_intent)

    async def ainvoke(self, _messages):
        return AIMessage(content=self._reply_text)
```

The intent node's structured call returns a `ParsedIntent` (or raises, to
exercise the fallback-to-small-talk path). The response node's plain
`ainvoke` returns an `AIMessage`. Tool functions are patched on the graph
module with `unittest.mock.patch`:

```
with patch(
    "daraz_ai_shopping_assistant.agents.graph.search_products_tool",
    new=AsyncMock(return_value={"products": []}),
):
    final = await graph.ainvoke(initial_state)
```

This exercises the real `StateGraph`, real routing, real error handling,
and real state shape -- with zero LLM or network traffic.

### Mocked graph in `test_chat_service.py`

`ChatService` is tested with a `MagicMock` graph whose `ainvoke` is an
`AsyncMock` returning a pre-built final state. This verifies the service
layer only: state construction, reply extraction from the last `AIMessage`,
intent passthrough, and error propagation.

Representative assertions:

- The reply is taken from the last `AIMessage` in the state.
- A populated `tool_result` is returned as `ChatResponse.data`.
- An error in the final state is surfaced on `ChatResponse.error`.
- A state with no `AIMessage` yields the fallback reply string.

### `test_agent_tools.py`

Tool functions are tested with mocked services:

```
service = MagicMock()
service.search = AsyncMock(return_value=SearchResult(...))
with patch("...tools.get_search_service", return_value=service):
    result = await search_products_tool("mouse")
```

This verifies that each tool calls the correct service method with the
correct arguments and returns a JSON-serialisable dict. Typed scraper
errors are asserted to propagate unchanged (the graph node is what
converts them to `AgentState.error`).

---

## 7. Running the Suite

```
# Run everything (unit + api). Unit tests never hit the network.
uv run pytest

# Verbose
uv run pytest -v

# Coverage
uv run pytest --cov=daraz_ai_shopping_assistant --cov-report=term-missing

# Only integration tests (these hit real services -- require a live key)
uv run pytest -m integration

# Deselect integration (the default CI behaviour)
uv run pytest -m "not integration"
```

`pyproject.toml` configures `asyncio_mode = "auto"`, so async tests run
under the default without an explicit marker. The `integration` marker is
reserved for tests that genuinely call Daraz/Firecrawl; none exist yet.

### Goals

- > = 80% coverage on parsers and services.
- Every parser has at least one fixture-backed unit test.
- Every service has at least one service-layer test with the scraper
mocked.
- Every API route has a `TestClient` test with the service mocked.
- Every agent tool and the graph itself have at least one unit test.
- `ScrapeStore` has full coverage of load, set, get, prune, and error
paths.

---

## 8. Fixtures

`tests/fixtures/` holds frozen captures used by unit tests. No test hits
the network; every fixture is committed to the repo.

| Fixture ↕▾ | Used by ↕▾ |
|---|---|
| −`daraz_search_gaming_mouse.md` | `test_search_parser.py`, `test_search_service.py` |
| −`daraz_product_i927677133.md` | Product-page inspection / future parser tests |
| `daraz_product_i927677133_rendered.md` | Product-page inspection after the `wait_for` + `only_main_content=False` render options |
⚙

`daraz_search_gaming_mouse.md` is a frozen copy of a real Daraz search-
results page (Markdown mode). It contains 8 product cards, pagination links
to page 102, and a trailing category section -- the last case deliberately
exercises `_strip_trailing_sections`.

The two product fixtures were generated by `scripts/diagnose_product_page.py`
and show the difference the product-page rendering options make: the
`_rendered` variant contains the section headings that the default scrape
drops.

When adapting to a Daraz layout change (see PARSING.md section
6), refresh or add a fixture here and update the matching parser test.
