# Search Parser

`parsers/search_parser.py` is a **pure function** that converts raw Daraz
search-results Markdown into a structured dict. It is the only place in the
codebase that encodes Daraz's Markdown layout.

**Design rules:**

- Pure function: no I/O, no network, no globals.
- Missing fields are `None` — never invented, never defaulted to zero.
- Numeric fields are returned as `int`/`float`, never as formatted strings
  like `"Rs. 579"`.
- No Pydantic validation here. The parser returns a *best-effort* dict; the
  service layer runs `Product.model_validate` on each entry and discards
  failures.

---

## 1. Public Entry Point

```python
def parse_search_results(
    markdown: str,
    *,
    current_page: int | None = None,
    items_per_page: int = 40,
) -> dict[str, object]:
    ...
```

Returns a dict shaped like `SearchResult` minus the fields only the service
knows (`search_query`, `filters`, `scraped_at`):

```python
{
    "total_items_found": int | None,
    "pagination": {"current_page": int, "total_pages": int | None, "items_per_page": int},
    "products": [ { ...Product-shaped dict... }, ... ],
}
```

The pipeline inside:

```
markdown
   │
   ├─► _extract_total_items          → "11084 items found" header
   ├─► _extract_pagination           → page links at page bottom
   │
   ▼
_strip_trailing_sections             → trim at pagination section start
   │
   ▼
_split_into_product_blocks           → one chunk per product card
   │
   ▼
_parse_product_block (per chunk)    → one Product-shaped dict
```

---

## 2. The Regex Patterns

All Daraz-specific patterns live at the top of the module. If Daraz changes
its Markdown structure, adjust **these constants only**.

| Constant | Matches | Example line |
|---|---|---|
| `_ITEMS_FOUND_RE` | "N items found" header | `11084 items found for "Gaming Mouse"` |
| `_PAGINATION_LINK_RE` | Page-number links | `- [2](https://www.daraz.pk/catalog/?...&page=2)` |
| `_IMAGE_LINE_RE` | Standalone image-anchor line | `[![alt](image_url)](product_url)` |
| `_TITLE_LINE_RE` | Standalone title line (not image) | `[Title](product_url "Title")` |
| `_PRODUCT_ID_RE` | ID embedded in product URL | `...-i1959941878.html` |
| `_PRICE_RE` | `Rs.` price line (anchored) | `Rs. 579` |
| `_DISCOUNT_RE` | `NN% Off` discount | `27% OffCoins save Rs. 29` |
| `_COINS_RE` | Coins-save amount | `Coins save Rs. 29` |
| `_SOLD_RE` | Sold count (incl. K/M) | `184 sold`, `8.1K sold` |
| `_RATING_COUNT_RE` | Bare number in parens | `(40)` |
| `_LOCATION_RE` | Known Daraz location tokens | `Punjab`, `Sindh`, `Islamabad`, ... |
| `_PAGINATION_START_RE` | Start of pagination section | `\n- [N](https://...catalog/?...)` |

### Anchor discipline

`_PRICE_RE` is anchored to line start (`^Rs\.`) so it does **not**
accidentally match the `"Coins save Rs. 29"` substring on the discount line.
`_TITLE_LINE_RE` uses a negative look-ahead `^(?!\[!\[)` so it does not match
image-anchor lines.

### Location ordering

`_LOCATION_RE` is built from `_LOCATIONS` **longest-first** so that
`"Khyber Pakhtunkhwa"` matches before any shorter prefix.

---

## 3. Product Block Splitting

This is the subtle part. Daraz does **not** always render the image anchor
for products below the fold (lazy loading), but the title line is present for
every rendered product. Both shapes must be handled:

```
[![alt](image_url)](product_url)     <- image anchor (OPTIONAL)
[Title](product_url "Title")         <- title line (REQUIRED)
Rs. 579                              <- price (REQUIRED)
27% OffCoins save Rs. 29
184 sold
(40)
Punjab
```

`_split_into_product_blocks` algorithm:

1. Collect the position of every image-anchor line and every title line.
2. Stable-sort by position (image anchors precede title lines for the same
   URL).
3. Deduplicate by product URL — a product's block starts at the **first**
   line (image or title) that references it.
4. Slice the Markdown between consecutive block starts.

The result is one chunk per product card. A chunk that lacks a title line, a
product ID, or a price line is dropped by `_parse_product_block` (which
returns `None`).

---

## 4. Field Extraction (per block)

`_parse_product_block` extracts each field from a single chunk:

| Field | Source | Notes |
|---|---|---|
| `id` | `_PRODUCT_ID_RE` on the title URL | Required — block dropped if absent |
| `title` | title line | Required |
| `url` | title line | Required |
| `image` | image line | Optional — `None` when absent |
| `price` | `_PRICE_RE` | Required — block dropped if absent |
| `currency` | — | Always `"PKR"` |
| `original_price` | — | `None` (not rendered on search cards) |
| `discount_percentage` | `_DISCOUNT_RE` | Optional |
| `coins_save` | `_COINS_RE` | Optional |
| `sold_count` | `_SOLD_RE` + `_parse_short_int` | Handles `8.1K` → 8100 |
| `rating` | — | `None` (search cards omit the star value) |
| `rating_count` | `_RATING_COUNT_RE` | Optional |
| `location` | `_LOCATION_RE` | Optional |

### Abbreviated counts

`_parse_short_int` turns `"8.1K"` → `8100`, `"1,234"` → `1234`,
`"5.3K"` → `5300`. A `K` suffix multiplies by 1,000; `M` by 1,000,000.

---

## 5. Trailing-Section Trimming

`_strip_trailing_sections` removes everything from the pagination section
onward (page links, category listing, footer) before block splitting. This
keeps the final product block from absorbing unrelated footer content.

---

## 6. Adaptation Guide

When Daraz changes its page layout:

1. Open `parsers/search_parser.py`.
2. Identify which constant no longer matches.
3. Update **only** the affected regex constant.
4. Add/adjust a fixture in `tests/fixtures/` and a matching unit test.

Do **not** scatter Daraz-specific strings elsewhere in the codebase. The
parser is the single source of layout knowledge for the search pipeline.

---

## 7. Worked Example (from the fixture)

Given `tests/fixtures/daraz_search_gaming_mouse.md`, the parser produces:

- `total_items_found`: `11084`
- `pagination`: `{ "current_page": 1, "total_pages": 102, "items_per_page": 40 }`
- 8 product dicts, the first being:

```python
{
    "id": "i1959941878",
    "title": "RGB Gaming Mouse with 7 light Wired Mouse ...",
    "url": "https://www.daraz.pk/products/7-i1959941878.html",
    "image": "https://img.drz.lazcdn.com/static/pk/p/1276439c....avif",
    "price": 579.0,
    "currency": "PKR",
    "original_price": None,
    "discount_percentage": 27,
    "coins_save": 29,
    "sold_count": 184,
    "rating": None,
    "rating_count": 40,
    "location": "Punjab",
}
```
