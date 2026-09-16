# Architecture Decision Records

Short, dated records of the decisions that shape the backend. Each ADR
captures what was decided, why it was decided, and what it forbids.

---

## ADR-001 — Search pages use deterministic parsing; product pages use Firecrawl structured extraction

**Date:** 2026-09-15
**Status:** Accepted
**Supersedes:** none

### Context

The search pipeline is high-volume and structurally regular. The same search
page frequently yields the same layout, so regex-based parsing is cheaper,
more testable, and more deterministic than sending the page to an LLM.

Product detail pages are different. Daraz renders specifications, seller data,
variant selectors, and recommendation panels with irregular structure that is
difficult to cover reliably with a regex-only parser. Firecrawl's structured
extraction gives a better fit for those pages while still allowing strict
Pydantic validation afterward.

### Decision

Split the extraction strategy by page type:

| Page type | Adapter method | Extraction |
|---|---|---|
| Search results (`/catalog/?q=...`) | `scrape(url)` | deterministic Markdown parsing |
| Product detail (`/products/...`) | `scrape_json(url, schema=...)` | Firecrawl structured extraction |
| Recommendations (embedded in the product payload) | same as product detail | same structured extraction |

The search path never touches an LLM. Product payloads are validated through
`ProductDetails.model_validate(...)` before they leave the service layer.

### Consequences

Positive:

- search parsing remains deterministic and fixture-backed
- product extraction tolerates page-layout drift better than regex parsing
- all structured output is still gated by Pydantic validation
- the adapter API stays small: Markdown scrape + JSON scrape with shared retry logic

Negative:

- product extraction costs more Firecrawl credits than search scraping
- model output can vary slightly between runs, so validation is mandatory
- failures are harder to unit-test than regex parsing alone

Forbidden:

- calling `scrape_json` on a search-result page
- calling `scrape` on a product page and feeding Markdown into a regex parser
- skipping Pydantic validation on structured extraction output
- allowing the LLM to invent `id`, `url`, `price`, or `currency` values without validation

### References

- Specification.md §14, §17, §20, §40
- docs/ARCHITECTURE.md
- src/daraz_ai_shopping_assistant/services/product_service.py

---

## ADR-002 — The LLM is bounded to intent routing and reply generation; it never owns product data

**Date:** 2026-09-16
**Status:** Accepted

### Context

The project implements a LangGraph chat layer. The agent can perform search,
product lookup, and recommendation lookups by calling the same validated
backend services used by the REST API. That is a powerful design, but it also
creates a risk: if the LLM were allowed to fabricate product facts or rewrite
backend data, the system would lose trustworthiness.

### Decision

The chat agent is intentionally narrow:

- it classifies user intent and extracts route parameters
- it calls the backend services for product and search retrieval
- it uses the tool result as evidence for the final reply
- it never writes or invents Daraz product data on its own

The LLM is therefore a conversational layer, not a source of truth.

### Consequences

Positive:

- the chat endpoint remains grounded in real Daraz data
- product validation stays centralized in the service and model layers
- the agent can safely summarise search results or recommendations without displacing the canonical backend
- the deterministic backend remains the source of truth for all product facts

Negative:

- chat responses depend on the quality of the intent parser and tool results
- the system cannot “guess” missing values; it must say when data is absent

Forbidden:

- letting the LLM fabricate prices, URLs, or IDs
- bypassing `Product.model_validate` or `ProductDetails.model_validate`
- using the agent to replace the scraper or parser pipeline

### References

- src/daraz_ai_shopping_assistant/services/chat_service.py
- src/daraz_ai_shopping_assistant/agents/graph.py
- src/daraz_ai_shopping_assistant/agents/tools.py
- Specification.md §13, §17, §22

