"""Local persistence for scrape payloads.

A single JSON file backs the store. Nothing in this package depends on
services, scrapers, or agents -- it is a leaf that higher layers call into.

Public API:
    - ScrapeStore: load, read, write, and prune a JSON-backed store.
"""

from __future__ import annotations

from daraz_ai_shopping_assistant.storage.json_store import ScrapeStore

__all__ = ["ScrapeStore"]
