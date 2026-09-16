# Configuration

All runtime configuration is centralised in `core/config.py` as a
pydantic-settings `Settings` model. Values are read from environment variables
or from a `.env` file at the repository root. Every value is typed and
validated at import time.

> All secrets stay outside source code. Load them from `.env` and never commit `.env`.

---

## 1. Accessing settings

```python
from daraz_ai_shopping_assistant.core.config import settings
settings.firecrawl_api_key
```

`settings` is a module-level singleton built via `get_settings()` with
`@lru_cache`. Tests that need to reload configuration can call
`get_settings.cache_clear()`.

---

## 2. Example `.env`

```ini
APP_NAME="Daraz AI Shopping Assistant"
APP_ENV=dev
APP_DEBUG=false

API_V1_PREFIX=/api/v1
API_HOST=0.0.0.0
API_PORT=8000

FIRECRAWL_API_KEY=fc-your-key-here
FIRECRAWL_BASE_URL=https://api.firecrawl.dev
FIRECRAWL_TIMEOUT_SECONDS=30
FIRECRAWL_MAX_RETRIES=2

GOOGLE_API_KEY=your-google-api-key-here
LLM_MODEL=gemini-2.5-flash
LLM_TEMPERATURE=0.0

DARAZ_BASE_URL=https://www.daraz.pk
DARAZ_SEARCH_PATH=/catalog/
DARAZ_DEFAULT_CURRENCY=PKR
DARAZ_ITEMS_PER_PAGE=40

SCRAPER_DEFAULT_PAGE=1

LOG_LEVEL=INFO
LOG_FORMAT=console
```

The chat endpoint requires `GOOGLE_API_KEY` and the model settings. The
search and product endpoints only require `FIRECRAWL_API_KEY`.

---

## 3. Field reference

### Application

| Field | Env var | Default | Notes |
|---|---|---|---|
| `app_name` | `APP_NAME` | `"Daraz AI Shopping Assistant"` | used in logs and OpenAPI |
| `app_env` | `APP_ENV` | `"dev"` | `dev` / `staging` / `prod` |
| `app_debug` | `APP_DEBUG` | `false` | never enable in production |

### API

| Field | Env var | Default | Notes |
|---|---|---|---|
| `api_v1_prefix` | `API_V1_PREFIX` | `"/api/v1"` | prefix for versioned routes |
| `api_host` | `API_HOST` | `"0.0.0.0"` | ASGI bind host |
| `api_port` | `API_PORT` | `8000` | ASGI port |

### Firecrawl

| Field | Env var | Default | Notes |
|---|---|---|---|
| `firecrawl_api_key` | `FIRECRAWL_API_KEY` | `""` | required and validated before import-time use |
| `firecrawl_base_url` | `FIRECRAWL_BASE_URL` | `"https://api.firecrawl.dev"` | override for self-hosting |
| `firecrawl_timeout_seconds` | `FIRECRAWL_TIMEOUT_SECONDS` | `30.0` | `> 0` |
| `firecrawl_max_retries` | `FIRECRAWL_MAX_RETRIES` | `2` | `0..5` |

The `firecrawl_api_key` validator runs in `mode="before"` and rejects missing,
empty, or whitespace-only values before Pydantic coerces them.

### LLM / chat

| Field | Env var | Default | Notes |
|---|---|---|---|
| `google_api_key` | `GOOGLE_API_KEY` | `""` | required for the chat endpoint |
| `llm_model` | `LLM_MODEL` | `"gemini-2.5-flash"` | model identifier |
| `llm_temperature` | `LLM_TEMPERATURE` | `0.0` | `0.0..2.0` |

### Daraz

| Field | Env var | Default | Notes |
|---|---|---|---|
| `daraz_base_url` | `DARAZ_BASE_URL` | `"https://www.daraz.pk"` | trailing slash is stripped |
| `daraz_search_path` | `DARAZ_SEARCH_PATH` | `"/catalog/"` | Daraz search path |
| `daraz_default_currency` | `DARAZ_DEFAULT_CURRENCY` | `"PKR"` | default currency code |
| `daraz_items_per_page` | `DARAZ_ITEMS_PER_PAGE` | `40` | observed page size |

### Scraper behaviour

| Field | Env var | Default | Notes |
|---|---|---|---|
| `scraper_user_agent` | `SCRAPER_USER_AGENT` | Chrome 126 UA | used in requests |
| `scraper_default_page` | `SCRAPER_DEFAULT_PAGE` | `1` | minimum `1` |

### Logging

| Field | Env var | Default | Notes |
|---|---|---|---|
| `log_level` | `LOG_LEVEL` | `"INFO"` | `DEBUG` is dev-only |
| `log_format` | `LOG_FORMAT` | `"console"` | use `"json"` in production |

---

## 4. Behaviour notes

- the model uses `case_sensitive=False`, so environment variables are read case-insensitively
- unknown env vars are ignored because `extra="ignore"`
- `daraz_base_url` is normalised by stripping trailing `/`
- in `APP_ENV=dev`, the app adds permissive CORS middleware; outside dev, CORS is not enabled
