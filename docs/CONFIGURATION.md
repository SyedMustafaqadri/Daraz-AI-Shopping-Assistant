# Configuration

All runtime configuration is centralised in `core/config.py` as a
pydantic-settings `Settings` model. Values are read from environment
variables or a `.env` file at the repository root. Every value is typed and
validated at import time.

> **Rule:** all secrets remain outside source code. Load them from `.env`,
> never commit `.env`.

---

## 1. Accessing Settings

```python
from daraz_ai_shopping_assistant.core.config import settings
settings.firecrawl_api_key
```

`settings` is a module-level singleton (lazily built via
`get_settings()` with `@lru_cache`). Tests that need to reload
configuration can call `get_settings.cache_clear()`.

---

## 2. `.env` Example

```ini
# Required — the adapter will not start without it.
FIRECRAWL_API_KEY=fc-...

# Optional overrides (defaults shown).
FIRECRAWL_BASE_URL=https://api.firecrawl.dev
FIRECRAWL_TIMEOUT_SECONDS=30
FIRECRAWL_MAX_RETRIES=2

APP_NAME=Daraz AI Shopping Assistant
APP_ENV=dev
APP_DEBUG=false

LOG_LEVEL=INFO
LOG_FORMAT=console
```

Future env vars (not yet wired): `LLM_API_KEY`, `DATABASE_URL`.

---

## 3. Field Reference

### Application

| Field | Env var | Default | Notes |
|---|---|---|---|
| `app_name` | `APP_NAME` | `"Daraz AI Shopping Assistant"` | Used in logs and OpenAPI |
| `app_env` | `APP_ENV` | `"dev"` | `dev` \| `staging` \| `prod`; controls docs + CORS |
| `app_debug` | `APP_DEBUG` | `false` | Never enable in production |

### API

| Field | Env var | Default | Notes |
|---|---|---|---|
| `api_v1_prefix` | `API_V1_PREFIX` | `"/api/v1"` | URL prefix for versioned routes |
| `api_host` | `API_HOST` | `"0.0.0.0"` | ASGI host interface |
| `api_port` | `API_PORT` | `8000` | ASGI port, `1–65535` |

### Firecrawl

| Field | Env var | Default | Notes |
|---|---|---|---|
| `firecrawl_api_key` | `FIRECRAWL_API_KEY` | `""` (required) | Validated non-empty; see below |
| `firecrawl_base_url` | `FIRECRAWL_BASE_URL` | `"https://api.firecrawl.dev"` | Override for self-hosting |
| `firecrawl_timeout_seconds` | `FIRECRAWL_TIMEOUT_SECONDS` | `30.0` | Per-request timeout, `>0` |
| `firecrawl_max_retries` | `FIRECRAWL_MAX_RETRIES` | `2` | Retries on transient failures, `0–5` |

**The API key validator** (`_validate_firecrawl_key`, `mode="before"`)
rejects a missing, empty, or whitespace-only key early. It catches a missing
env var (which arrives as `None`) **before** Pydantic tries to coerce it, so
a missing key produces a clean `ValidationError` rather than an
`AttributeError`. The field default is deliberately `""` (not required) so
mypy sees a valid constructor signature while runtime behaviour is
identical.

### Daraz

| Field | Env var | Default | Notes |
|---|---|---|---|
| `daraz_base_url` | `DARAZ_BASE_URL` | `"https://www.daraz.pk"` | Trailing slash stripped by a validator |
| `daraz_search_path` | `DARAZ_SEARCH_PATH` | `"/catalog/"` | Search/catalog endpoint path |
| `daraz_default_currency` | `DARAZ_DEFAULT_CURRENCY` | `"PKR"` | Currency code attached to prices |
| `daraz_items_per_page` | `DARAZ_ITEMS_PER_PAGE` | `40` | Observed items per page |

### Scraper Behaviour

| Field | Env var | Default | Notes |
|---|---|---|---|
| `scraper_user_agent` | `SCRAPER_USER_AGENT` | Chrome 126 UA string | Sent to Firecrawl/Daraz |
| `scraper_default_page` | `SCRAPER_DEFAULT_PAGE` | `1` | Default search page, `≥1` |

### Logging

| Field | Env var | Default | Notes |
|---|---|---|---|
| `log_level` | `LOG_LEVEL` | `"INFO"` | `DEBUG` should be dev-only |
| `log_format` | `LOG_FORMAT` | `"console"` | Use `"json"` in production |

---

## 4. Behaviour Notes

- **Case-insensitive:** `model_config` sets `case_sensitive=False`, so
  `firecrawl_api_key` is read from `FIRECRAWL_API_KEY`,
  `Firecrawl_api_key`, or any casing.
- **Extra keys ignored:** `extra="ignore"` means unknown env vars do not
  raise — useful when the deployment environment sets unrelated variables.
- **Trailing slash normalised:** `daraz_base_url` is `rstrip("/")`-ed so the
  URL builders never produce a double slash.
- **CORS:** in `APP_ENV=dev` the app adds a permissive CORS middleware
  (wildcard origin, credentials disabled). Outside `dev` no CORS middleware
  is registered.
