#!/usr/bin/env python3
"""Diagnostic: dump a Daraz search Markdown and report what the parser sees.

Run this if a search returns fewer products than expected. It scrapes the
search page once, saves the raw Markdown to tests/fixtures/, and prints a
summary of the internal structure the parser relies on.

Usage:
    uv run python scripts/diagnose_search.py
    uv run python scripts/diagnose_search.py --query "gaming mouse"

Costs one Firecrawl credit. The saved Markdown can be inspected manually
if the summary reveals a structural mismatch.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path
from typing import Final

from daraz_ai_shopping_assistant.core.config import settings
from daraz_ai_shopping_assistant.core.logging import configure_logging
from daraz_ai_shopping_assistant.parsers.search_parser import (
    _IMAGE_LINE_RE,
    _TITLE_LINE_RE,
    parse_search_results,
)
from daraz_ai_shopping_assistant.scrapers.firecrawl import FirecrawlAdapter

REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
FIXTURES_DIR: Final[Path] = REPO_ROOT / "tests" / "fixtures"

def _summarize(markdown: str) -> None:
    """Print a structural summary of the scraped Markdown.

    Args:
        markdown: The raw Markdown returned by Firecrawl.
    """
    product_urls_total = len(
        re.findall(r"https?://www\.daraz\.pk/products/[^)\"\s]+\.html", markdown)
    )
    image_anchors = _IMAGE_LINE_RE.findall(markdown)
    title_lines = _TITLE_LINE_RE.findall(markdown)
    unique_urls = {
        url
        for _, url in image_anchors
    } | {
        url
        for _, url in title_lines
    }

    print(f"Total Markdown characters : {len(markdown):,}")
    print(f"Product URLs (all forms)  : {product_urls_total}")
    print(f"Image anchor lines        : {len(image_anchors)}")
    print(f"Title lines               : {len(title_lines)}")
    print(f"Unique product URLs       : {len(unique_urls)}")

    parsed = parse_search_results(markdown, current_page=1)
    products = parsed["products"]
    print(f"Parser extracted products : {len(products)}")
    print(f"Total items reported      : {parsed['total_items_found']}")

    print()
    print("First 5 product URLs found in the Markdown:")
    seen: set[str] = set()
    count = 0
    for line in markdown.splitlines():
        if "/products/" not in line:
            continue
        for match in re.finditer(
            r"https?://www\.daraz\.pk/products/[^)\"\s]+\.html", line
        ):
            url = match.group(0)
            if url in seen:
                continue
            seen.add(url)
            print(f"  {url}")
            count += 1
            if count >= 5:
                break
        if count >= 5:
            break

async def main() -> int:
    """Entry point.

    Returns:
        Process exit code.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", default="plastic Chair", help="Search query.")
    parser.add_argument("--page", type=int, default=1, help="Page number.")
    args = parser.parse_args()

    configure_logging(level="INFO", fmt="console")
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)

    adapter = FirecrawlAdapter()
    url = (
        f"{settings.daraz_base_url}{settings.daraz_search_path}"
        f"?q={args.query.replace(' ', '%20')}&page={args.page}"
    )
    print(f"Scraping: {url}")
    markdown = await adapter.scrape(url)

    slug = re.sub(r"[^a-z0-9]+", "_", args.query.lower()).strip("_")
    destination = FIXTURES_DIR / f"daraz_search_{slug}.md"
    destination.write_text(markdown, encoding="utf-8")
    print(f"Saved to: {destination.relative_to(REPO_ROOT)}")
    print()

    _summarize(markdown)
    return 0

if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
