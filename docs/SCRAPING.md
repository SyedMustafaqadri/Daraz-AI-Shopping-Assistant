# Scraping Layer

The scraping layer owns **all** external content retrieval and is the single
boundary between the application and Firecrawl. Three modules make it up:

| Module | Role |
|---|---|
| `scrapers/base.py` | `DarazScraper` — the abstract interface services depend on |
| `scrapers/daraz.py` | `FirecrawlDarazScraper` + Daraz URL builders |
| `scrapers/firecrawl.py` | `FirecrawlAdapter` — the **only** module that imports `firecrawl` |

**Contract:** the scraper is intentionally *dumb* about domain concepts. It
returns raw strings (Markdown) and raw dicts (structured payloads). It does
not validate, normalise, or construct Pydantic models — that is the service
layer's job.

---

## 1. `DarazScraper` (base.py)

An ABC with two abstract methods, one per extraction path (see ADR-001):

```python
class DarazScraper(ABC):
    async def fetch_search_markdown(
        self,
        query: str,
        *,
        min_price: float | None = None,
        max_price: float | None = None,
        page: int = 1,
    ) -> str:
        ...

    async def fetch_product_payload(self, product_id: str) -> dict[str, Any]:
        ...
```

- `fetch_search_markdown` → the **Markdown** path (deterministic).
- `fetch_product_payload` → the **structured-extraction** path (LLM). The
  returned dict is *untrusted*; callers must run `ProductDetails.model_validate`.

Services depend on this interface, never on a concrete implementation. A
future Playwright scraper would subclass `DarazScraper` identically and be
drop-in replaceable.

---

## 2. `FirecrawlDarazScraper` (daraz.py)

The production implementation. It knows **Daraz URL syntax** and delegates
HTTP to the adapter.

```python
class FirecrawlDarazScraper(DarazScraper):
    def __init__(self, adapter: FirecrawlAdapter | None = None) -> None:
        self._adapter = adapter or FirecrawlAdapter()
```

The adapter is injectable so tests can supply a mock and so the transport can
be swapped without touching this class's logic.

### URL Builders (pure functions)

`build_search_url` and `build_product_url` are module-level pure functions
exported for direct use and testing.

**Search URL** — `https://www.daraz.pk/catalog/?q=...&price=...&page=...`

| Param | Encoding |
|---|---|
| `q` | URL-encoded; spaces become `%20` (matches Daraz's own rendered URLs) |
| `price` | `-MAX` (upper bound), `MIN-MAX` (range), `MIN-` (lower bound); omitted when neither set |
| `page` | Omitted when `page <= 1` (Daraz's default is page 1) |

Daraz expects integer price values, so fractional bounds are truncated
(`800.5 → 800`), matching the UI behaviour.

```python
>>> build_search_url("gaming mouse", max_price=800)
'https://www.daraz.pk/catalog/?q=gaming%20mouse&price=-800'
>>> build_search_url("gaming mouse", min_price=100, max_price=800, page=2)
'https://www.daraz.pk/catalog/?q=gaming%20mouse&price=100-800&page=2'
```

**Product URL** — `https://www.daraz.pk/products/{product_id}.html`

Daraz renders `/products/{slug}-{id}.html` and redirects the slug-less form
to the canonical URL, so the scraper builds the slug-less form.

```python
>>> build_product_url("i1959941878")
'https://www.daraz.pk/products/i1959941878.html'
```

### The Structured-Extraction Prompt

`_PRODUCT_EXTRACTION_PROMPT` guides the LLM for product pages:

> "Extract structured product details … For fields that are not present, omit
> them entirely — never guess, never invent. Numeric fields must be numbers,
> not formatted strings … currency is always 'PKR'. … use an empty array when
> none are shown."

The schema is derived at call time from
`ProductDetails.model_json_schema()`, so any model change automatically
propagates to the LLM prompt without a second edit.

---

## 3. `FirecrawlAdapter` (firecrawl.py)

A thin, stateless async wrapper around the `AsyncFirecrawl` SDK. Its
responsibilities:

1. Wrap the SDK so no other module imports `firecrawl`.
2. Convert responses into Markdown strings or structured JSON payloads.
3. Retry transient failures (timeouts, rate limits) with **exponential
   backoff**.
4. Map every upstream failure to a typed exception from
   `core/exceptions.py`.

### Two entry points

| Method | Returns | Used for |
|---|---|---|
| `scrape(url)` | `str` (Markdown) | Search-result pages |
| `scrape_json(url, *, schema, prompt=None, max_age_ms=None)` | `dict` | Product-detail pages |

Both share identical retry semantics, exception classification, and
structured logging (`FIRECRAWL_REQUEST`, `FIRECRAWL_RESPONSE`,
`FIRECRAWL_RETRY`), each carrying a `mode` field of `"markdown"` or
`"json"`.

### Configuration

```python
FirecrawlAdapter(
    api_key=None,            # defaults to settings.firecrawl_api_key
    max_retries=None,        # defaults to settings.firecrawl_max_retries
    backoff_base_seconds=1.5,
)
```

Retry delay before attempt `n` is `backoff_base_seconds * 2 ** (n - 1)`.

### Exception Classification

The adapter inspects the exception **class name** and **message** (not
Firecrawl's internal exception hierarchy, which changes between SDK
versions):

| Detection | Mapped to | HTTP |
|---|---|---|
| name/msg contains "timeout" / "timed out" | `ScraperTimeoutError` | 504 |
| "ratelimit" / "rate limit" / "too many requests" / "429" | `UpstreamRateLimitError` | 429 |
| anything else | `ScraperError` | 502 |

Only timeouts and rate limits are retried. A 4xx auth error or bad URL is not
retried — retrying would just burn quota.

### Response Extraction

`_extract_markdown` and `_extract_json` each handle three response shapes so a
minor SDK bump does not break the adapter:

1. **v2 `Document` object** — `.markdown` / `.json` attribute.
2. **v2 dict response** — `result["markdown"]` / `result["data"]["markdown"]`.
3. **v1 wrapped response** — `result.data.markdown` / `result.data.json`.

If no known shape carries content, a `ScraperError` is raised with the
`result_type` in the context.

### Freshness

`max_age_ms` controls reuse of a Firecrawl cache entry: `None` lets
Firecrawl tune reuse per domain (MVP default); `0` forces a live scrape (use
only when a stale read would cause a wrong decision, e.g. an availability
check).

---

## 4. Testability

The scraper and adapter are fully mockable:

- `SearchService(scraper=mock_scraper)` injects a fake `DarazScraper` — no
  network.
- `FirecrawlDarazScraper(adapter=mock_adapter)` injects a fake adapter.
- `FirecrawlAdapter`'s `_client` can be monkeypatched to return canned
  responses for unit tests.

See [TESTING.md](TESTING.md) for the full strategy.
