"""Parser layer.

Parsers convert raw scraped content into untrusted structured dicts.
They are pure functions: no I/O, no network, no globals.

Design contract (see Specification.md Section 17 and agent.md Section 10):

    - Input: raw content from a scraper (Markdown string, or for structured
      extraction, an already-decoded dict).
    - Output: dict or list of dicts. Never a Pydantic model -- the service
      layer runs model_validate to enforce the contract.
    - Missing fields are None. Never invented, never defaulted to zero.
    - Numeric fields are returned as numbers, never as formatted strings.

Public API:
    - parse_search_results: Markdown -> search-result dict.
    - (Phase 6) validate_product_details: structured payload -> dict.
"""

from __future__ import annotations

from daraz_ai_shopping_assistant.parsers.search_parser import parse_search_results

__all__ = ["parse_search_results"]
