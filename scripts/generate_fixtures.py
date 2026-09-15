#!/usr/bin/env python3
"""Generate Markdown fixtures from live Daraz pages via Firecrawl.

Run this **once per page shape** you need to parse. The saved Markdown is
then used by unit tests, so tests never hit the network and never burn
Firecrawl quota.

Usage:
    # 1. Make sure FIRECRAWL_API_KEY is set in .env
    # 2. Run from the repository root:
    uv run python scripts/generate_fixtures.py

The script will:
    - Scrape the Daraz gaming-mouse search page (max price Rs. 800).
    - Save the raw Markdown to ``tests/fixtures/daraz_search_gaming_mouse.md``.
    - Print the file size and a short preview.

Re-running overwrites the existing fixture. If you only want to add a new
fixture, add a new entry to ``FIXTURES`` below instead of rerunning for
the same URL — the point is to keep Daraz requests minimal.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Final

from daraz_ai_shopping_assistant.core.config import settings
from daraz_ai_shopping_assistant.core.logging import configure_logging, get_logger
from daraz_ai_shopping_assistant.scrapers.firecrawl import FirecrawlAdapter

logger = get_logger(__name__)

REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
FIXTURES_DIR: Final[Path] = REPO_ROOT / "tests" / "fixtures"

# Map: output filename → URL to scrape.
# Add new entries as you build parsers for new page shapes.
FIXTURES: Final[dict[str, str]] = {
    "daraz_search_gaming_mouse.md": (
        f"{settings.daraz_base_url}{settings.daraz_search_path}"
        "?q=Gaming%20Mouse&price=-800"
    ),
}

async def _generate_one(adapter: FirecrawlAdapter, filename: str, url: str) -> None:
    """Scrape a single URL and write the Markdown to a fixture file.

    Args:
        adapter: An initialised :class:`FirecrawlAdapter`.
        filename: Destination filename under ``tests/fixtures/``.
        url: The URL to scrape.
    """
    logger.info("FIXTURE_STARTED", extra={"ctx": {"file": filename, "url": url}})

    markdown = await adapter.scrape(url)
    destination = FIXTURES_DIR / filename
    destination.write_text(markdown, encoding="utf-8")

    size_kb = len(markdown) / 1024
    logger.info(
        "FIXTURE_SAVED",
        extra={
            "ctx": {
                "file": str(destination.relative_to(REPO_ROOT)),
                "chars": len(markdown),
                "size_kb": round(size_kb, 1),
            }
        },
    )
    print(f"  → {destination.relative_to(REPO_ROOT)}  ({size_kb:.1f} KB)")
    print(f"    preview: {markdown[:120].replace(chr(10), ' ')}...")

async def main() -> int:
    """Entry point — generate every fixture in ``FIXTURES``.

    Returns:
        Process exit code (0 on success, 1 on any failure).
    """
    configure_logging(level="INFO", fmt="console")
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Generating {len(FIXTURES)} fixture(s) into {FIXTURES_DIR}\n")
    adapter = FirecrawlAdapter()

    failures = 0
    for filename, url in FIXTURES.items():
        try:
            await _generate_one(adapter, filename, url)
        except Exception as exc:
            logger.error(
                "FIXTURE_FAILED",
                extra={"ctx": {"file": filename, "url": url, "error": str(exc)}},
            )
            print(f"  ✗ {filename}: {exc}", file=sys.stderr)
            failures += 1

    print()
    if failures:
        print(f"Completed with {failures} failure(s).", file=sys.stderr)
        return 1
    print("All fixtures generated.")
    return 0

if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
