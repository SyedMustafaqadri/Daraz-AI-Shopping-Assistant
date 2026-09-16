"""Search endpoint schemas.

For the MVP, the search response shape is identical to the domain model
``SearchResult``. Rather than duplicate every field, we expose an alias.
This keeps a single source of truth for the search-response contract.

If the API ever needs to reshape the response (drop fields, add links,
wrap in an envelope), subclass or replace this alias -- the rest of the
codebase keeps using the domain model.
"""

from __future__ import annotations

from daraz_ai_shopping_assistant.models.search import SearchResult

#: Alias exposed to the API layer for the search response body.
SearchResponse = SearchResult

__all__ = ["SearchResponse"]
