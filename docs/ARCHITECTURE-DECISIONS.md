# Architecture Decision Records

Short, dated records of the decisions that shape the backend. Each ADR
captures *what* was decided, *why*, and *what it forbids*. When a future
change contradicts an ADR, either update the ADR or open a new one.

---

## ADR-001 — Search pages use deterministic parsing; product pages use Firecrawl structured extraction

**Date:** 2026-09-15
**Status:** Accepted
**Supersedes:** none
**Amends:** `Specification.md` §17 (LLM usage)

### Context

The `Specification.md` originally stated (§17) that the LLM must not be
responsible for converting whole webpages into JSON. That rule was written
to prevent non-determinism in the **search-result** pipeline, where the same
page always renders the same layout and a regex-based parser is both cheaper
and more reliable than an LLM call.

Product detail pages are a different shape entirely:

- Specifications are rendered as an irregular key-value table whose column
  count varies by category.
- Seller information sits inside a nested widget with conditional fields.
- Variants (colour, size, bundle) appear as a chip selector whose structure
  changes when only one variant exists.
- Reviews and the recommendation carousel are both variable-length and
  inconsistently rendered.

Writing a deterministic parser for this layout produces fragile, lengthy
regex that breaks whenever Daraz tweaks a template — and cannot gracefully
degrade when a section is missing.

### Decision

Split the extraction strategy by page type:

| Page type | Adapter method | Extraction |
|---|---|---|
| Search results (`/catalog/?q=...`) | `FirecrawlAdapter.scrape(url)` | Deterministic Markdown parser |
| Product detail (`/products/...`) | `FirecrawlAdapter.scrape_json(url, schema=...)` | Firecrawl structured extraction (LLM + JSON Schema) |
| Recommendations (embedded in product page) | `FirecrawlAdapter.scrape_json(...)` | Same structured extraction, nested schema |

The search pipeline remains fully deterministic. No LLM touches a search
result. `Specification.md` §17's "do not blindly convert whole pages to
JSON" rule stands — it just no longer covers the product detail page,
which was never the rule's target.

### Consequences

**Positive:**
- Search parsing stays cheap, deterministic, and testable against a frozen
  Markdown fixture (no per-test LLM cost).
- Product detail extraction survives Daraz layout changes because the LLM
  adapts to the page rather than a regex.
- Firecrawl already ships the JSON-schema extraction primitive; we are not
  adding a new dependency.
- The adapter's public surface stays small: two methods, same retry and
  exception-mapping semantics.

**Negative:**
- Product detail scrapes cost more credits than Markdown scrapes (structured
  extraction is a premium Firecrawl feature).
- Product detail extraction is **non-deterministic**: identical input can
  yield slightly different output across runs. Mitigated by validating the
  response through Pydantic (`ProductDetails.model_validate`), which rejects
  malformed payloads regardless of source.
- Structured extraction failures are harder to debug than parser failures —
  you cannot unit-test the LLM. Mitigation: log the raw payload on
  validation failure; add a fixture-based test for the *schema* using a
  saved sample response.

**Forbidden:**
- ❌ Calling `scrape_json` on a search result page. Search pages must go
  through `scrape` + `search_parser`.
- ❌ Calling `scrape` (Markdown) on a product detail page and feeding it to
  a regex parser. Use `scrape_json` with the `ProductDetails` schema.
- ❌ Letting the LLM produce `id`, `url`, `price`, or `currency` values
  without Pydantic validation. The schema is a suggestion; the model is
  the contract.

### Implementation notes

- Schema source: `ProductDetails.model_json_schema()`. The `extra="forbid"`
  config on the model adds `additionalProperties: false`, which prevents
  the LLM from inventing undeclared fields.
- Prompt: keep short and directive. Example:
  *"Extract the product information. If a field is not present on the page,
  omit it rather than guessing."*
- Validation: the service calls `ProductDetails.model_validate(payload)`. A
  `ValidationError` is caught and re-raised as `ParseError` with
  `source="product"`.
- Freshness: leave `max_age_ms=None` for the MVP so Firecrawl tunes reuse
  per domain. Set to `0` only when a stale read would cause a wrong
  decision (e.g. availability checks).

### References

- `Specification.md` §14 (Detailed Product Schema), §17 (LLM Usage), §20
  (Firecrawl Adapter)
- `agent.md` §5 (layering rules)
- Firecrawl: [structured extraction docs](https://docs.firecrawl.dev/features/extract)
