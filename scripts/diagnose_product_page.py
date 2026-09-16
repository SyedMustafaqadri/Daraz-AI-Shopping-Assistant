#!/usr/bin/env python3
"""Diagnose what Firecrawl actually returns for a Daraz product page.

The structured-extraction path (ADR-001) can silently return empty
``specifications``, ``reviews``, and ``recommendations`` when the sections
simply are not present in Firecrawl's snapshot of the page. Rewriting the
extraction prompt cannot fix a missing-snapshot problem -- only the
diagnostic can tell you which of the two is happening.

This script:

    1. Scrapes the product page in Markdown mode, applying the same
       rendering options that ``FirecrawlDarazScraper`` uses for product
       pages (``wait_for`` and ``only_main_content=False``).
    2. Saves the raw Markdown to ``tests/fixtures/`` so it can be inspected
       by hand.
    3. Reports whether each expected section heading appears.
    4. Lists every Markdown heading (``#``, ``##``, ...) actually present
       in the snapshot, so you can see what Daraz calls its sections when
       the expected names do not match.
    5. Prints a character-count breakdown of where the content sits.

Costs one Firecrawl credit.

Usage:
    uv run python scripts/diagnose_product_page.py
    uv run python scripts/diagnose_product_page.py --product-id i927677133
    uv run python scripts/diagnose_product_page.py --no-render-options
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path
from typing import Final

from daraz_ai_shopping_assistant.core.logging import configure_logging
from daraz_ai_shopping_assistant.scrapers.daraz import build_product_url
from daraz_ai_shopping_assistant.scrapers.firecrawl import FirecrawlAdapter

REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
FIXTURES_DIR: Final[Path] = REPO_ROOT / "tests" / "fixtures"

#: Section headings (case-insensitive substrings) we look for in the
#: Markdown. Add to this list when a new section starts showing up empty.
EXPECTED_HEADINGS: Final[tuple[str, ...]] = (
    "Product Description",
    "Product details",
    "Specifications",
    "Ratings & Reviews",
    "Ratings and Reviews",
    "Recommended for you",
    "You may also like",
    "Similar products",
)

#: Rendering options applied to the product scrape. These MUST match the
#: values used by ``FirecrawlDarazScraper.fetch_product_payload`` so the
#: diagnostic reflects production behaviour.
_WAIT_FOR_MS: Final[int] = 5000
_ONLY_MAIN_CONTENT: Final[bool] = False

#: Matches any Markdown heading line, capturing the level and text.
_HEADING_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?P<hashes>#{1,6})\s+(?P<text>.+?)\s*$",
    re.MULTILINE,
)

def _find_heading(markdown: str, needle: str) -> tuple[int, str] | None:
    """Return the first line index and trimmed line containing ``needle``.

    Args:
        markdown: Full Markdown of the product page.
        needle: Case-insensitive substring to search for.

    Returns:
        A ``(line_number, line_text)`` tuple, or ``None`` if not found.
    """
    pattern = re.compile(re.escape(needle), re.IGNORECASE)
    for index, line in enumerate(markdown.splitlines(), start=1):
        if pattern.search(line):
            return index, line.strip()[:160]
    return None

def _print_expected_heading_scan(markdown: str) -> int:
    """Scan for the specific headings we expect on a product page.

    Args:
        markdown: Full Markdown of the product page.

    Returns:
        The number of expected headings found.
    """
    print("Expected heading scan:")
    found = 0
    for heading in EXPECTED_HEADINGS:
        hit = _find_heading(markdown, heading)
        if hit:
            line_no, line_text = hit
            print(f"  [FOUND] {heading!r:36} at line {line_no}: {line_text}")
            found += 1
        else:
            print(f"  [ --  ] {heading!r}")
    return found

def _print_actual_headings(markdown: str, limit: int = 40) -> None:
    """Print every Markdown heading actually present in the snapshot.

    This is the critical diagnostic: when the expected heading names do not
    match, this list tells you what Daraz actually calls its sections, so
    you can add the right strings to ``EXPECTED_HEADINGS`` or fix the
    extraction prompt.

    Args:
        markdown: Full Markdown of the product page.
        limit: Maximum number of headings to print.
    """
    matches = list(_HEADING_RE.finditer(markdown))
    print(f"Actual headings present in snapshot ({len(matches)} total):")
    if not matches:
        print("  (no Markdown headings found at all)")
        return

    for match in matches[:limit]:
        level = len(match.group("hashes"))
        text = match.group("text")[:100]
        line_no = markdown[: match.start()].count("\n") + 1
        print(f"  line {line_no:>5}  {'#' * level} {text}")
    if len(matches) > limit:
        print(f"  ... and {len(matches) - limit} more")

def _print_content_breakdown(markdown: str) -> None:
    """Print a rough map of where the page's characters sit.

    Splits the Markdown into eight equal chunks by line count and prints
    the size of each. Useful for spotting whether the page content is
    concentrated in the first 10% (top-of-page only) or spread throughout.

    Args:
        markdown: Full Markdown of the product page.
    """
    lines = markdown.splitlines()
    if not lines:
        return

    total = len(lines)
    chunks = 8
    chunk_size = max(1, total // chunks)

    print("Content density map (lines per eighth):")
    for i in range(chunks):
        start = i * chunk_size
        end = total if i == chunks - 1 else (i + 1) * chunk_size
        segment = "\n".join(lines[start:end])
        chars = len(segment)
        bar = "#" * min(50, chars // 200)
        print(f"  [{start:>4}-{end:>4}]  {chars:>7,} chars  {bar}")

def _print_interpretation(found_count: int) -> None:
    """Print the interpretation guide tailored to the result.

    Args:
        found_count: Number of expected headings found in the snapshot.
    """
    print("Interpretation:")
    if found_count >= 4:
        print("  Sections ARE in the snapshot. The extraction prompt is")
        print("  missing them -> tighten the prompt in scrapers/daraz.py.")
    elif found_count >= 1:
        print("  Some sections are in the snapshot, others are not. The")
        print("  page is partially rendered. Raise wait_for_ms and re-run.")
    else:
        print("  No sections are in the snapshot. The page is not being")
        print("  fully rendered. Options:")
        print("    1. Raise wait_for_ms (try 8000 or 10000).")
        print("    2. Add a scroll action via Firecrawl's actions parameter.")
        print("    3. Confirm the page has those sections at all by")
        print("       opening it in a browser.")

def _summarise(markdown: str) -> None:
    """Print the full structural summary of the scraped Markdown.

    Args:
        markdown: The raw Markdown returned by Firecrawl.
    """
    print(f"Total Markdown characters : {len(markdown):,}")
    print(f"Total lines              : {markdown.count(chr(10)):,}")
    print()

    found_count = _print_expected_heading_scan(markdown)
    print()
    print(f"Expected headings found: {found_count}/{len(EXPECTED_HEADINGS)}")
    print()

    _print_actual_headings(markdown)
    print()

    _print_content_breakdown(markdown)
    print()

    _print_interpretation(found_count)

async def main() -> int:
    """Entry point.

    Returns:
        Process exit code.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--product-id",
        default="i927677133",
        help="Daraz product ID to diagnose.",
    )
    parser.add_argument(
        "--no-render-options",
        action="store_true",
        help=(
            "Scrape without wait_for / only_main_content. Useful for "
            "comparing the raw default snapshot against the rendered one."
        ),
    )
    args = parser.parse_args()

    configure_logging(level="INFO", fmt="console")
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)

    url = build_product_url(args.product_id)

    if args.no_render_options:
        wait_for_ms: int | None = None
        only_main_content: bool | None = None
        suffix = "_default"
    else:
        wait_for_ms = _WAIT_FOR_MS
        only_main_content = _ONLY_MAIN_CONTENT
        suffix = "_rendered"

    print(f"Scraping: {url}")
    print(f"  wait_for_ms       = {wait_for_ms}")
    print(f"  only_main_content = {only_main_content}")
    print()

    adapter = FirecrawlAdapter()
    markdown = await adapter.scrape(
        url,
        wait_for_ms=wait_for_ms,
        only_main_content=only_main_content,
    )

    destination = FIXTURES_DIR / f"daraz_product_{args.product_id}{suffix}.md"
    destination.write_text(markdown, encoding="utf-8")
    print(f"Saved to: {destination.relative_to(REPO_ROOT)}")
    print()

    _summarise(markdown)
    return 0

if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
