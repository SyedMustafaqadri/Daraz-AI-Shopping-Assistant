"""Application settings loaded from environment variables / .env.

Uses pydantic-settings so that every configuration value is typed,
validated at import time, and documented in one place.

Example:
    >>> from daraz_ai_shopping_assistant.core.config import settings
    >>> settings.firecrawl_api_key
    'fc-...'
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central configuration for the Daraz AI Shopping Assistant backend.

    All values are read from environment variables or a .env file at the
    repository root. Nested keys are not used -- everything is flat and
    prefixed where necessary to avoid collisions.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ------------------------------------------------------------------ #
    # Application
    # ------------------------------------------------------------------ #
    app_name: str = Field(
        default="Daraz AI Shopping Assistant",
        description="Human-readable application name used in logs and OpenAPI.",
    )
    app_env: Literal["dev", "staging", "prod"] = Field(
        default="dev",
        description="Runtime environment. Controls log verbosity and debug flags.",
    )
    app_debug: bool = Field(
        default=False,
        description="Enable debug mode. Never enable in production.",
    )

    # ------------------------------------------------------------------ #
    # API
    # ------------------------------------------------------------------ #
    api_v1_prefix: str = Field(
        default="/api/v1",
        description="URL prefix for all versioned API routes.",
    )
    api_host: str = Field(
        default="0.0.0.0",
        description="Host interface for the ASGI server.",
    )
    api_port: int = Field(
        default=8000,
        ge=1,
        le=65535,
        description="Port for the ASGI server.",
    )

    # ------------------------------------------------------------------ #
    # Firecrawl
    # ------------------------------------------------------------------ #
    # NOTE: `default=""` is deliberate.
    #
    # pydantic-settings populates this field from the environment, but mypy
    # cannot see that -- it only sees the constructor signature. If we
    # declare the field as `Field(...)` (required), mypy emits
    # "Missing named argument" on every `Settings()` call. By giving it a
    # sentinel default of "" and enforcing non-empty in the validator
    # below, we get:
    #   - mypy-clean construction
    #   - identical runtime behaviour (ValidationError on missing/blank)
    firecrawl_api_key: str = Field(
        default="",
        description="Firecrawl API key. Required. Loaded from FIRECRAWL_API_KEY.",
    )
    firecrawl_base_url: str = Field(
        default="https://api.firecrawl.dev",
        description="Base URL for the Firecrawl API. Override for self-hosting.",
    )
    firecrawl_timeout_seconds: float = Field(
        default=30.0,
        gt=0,
        description="Per-request timeout for Firecrawl calls, in seconds.",
    )
    firecrawl_max_retries: int = Field(
        default=2,
        ge=0,
        le=5,
        description="Number of retries on transient Firecrawl failures.",
    )

    # ------------------------------------------------------------------ #
    # LLM (chat layer)
    # ------------------------------------------------------------------ #
    google_api_key: str = Field(
        default="",
        description="Google API key for Gemini. Loaded from GOOGLE_API_KEY.",
    )
    llm_model: str = Field(
        default="gemini-2.5-flash",
        description="LangChain model identifier for the agent layer.",
    )
    llm_temperature: float = Field(
        default=0.0,
        ge=0.0,
        le=2.0,
        description="Sampling temperature. 0.0 for deterministic classification.",
    )

    # ------------------------------------------------------------------ #
    # Daraz
    # ------------------------------------------------------------------ #
    daraz_base_url: str = Field(
        default="https://www.daraz.pk",
        description="Canonical Daraz Pakistan base URL.",
    )
    daraz_search_path: str = Field(
        default="/catalog/",
        description="Path for the Daraz search/catalog endpoint.",
    )
    daraz_default_currency: str = Field(
        default="PKR",
        description="Currency code attached to all prices.",
    )
    daraz_items_per_page: int = Field(
        default=40,
        ge=1,
        description="Observed items per page on Daraz search results.",
    )

    # ------------------------------------------------------------------ #
    # Scraper behaviour
    # ------------------------------------------------------------------ #
    scraper_user_agent: str = Field(
        default=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        ),
        description="User-Agent sent to Firecrawl / Daraz.",
    )
    scraper_default_page: int = Field(
        default=1,
        ge=1,
        description="Default page number for search requests.",
    )

    # ------------------------------------------------------------------ #
    # Scrape store (local JSON persistence)
    # ------------------------------------------------------------------ #
    scrape_store_path: str = Field(
        default="data/scrape_store.json",
        description=(
            "Path to the JSON file that persists validated scrape "
            "payloads. Relative paths resolve from the process working "
            "directory. The parent directory is created on startup."
        ),
    )
    scrape_store_search_ttl_seconds: int = Field(
        default=6 * 3600,
        ge=1,
        description="TTL for persisted search-result payloads, in seconds.",
    )
    scrape_store_product_ttl_seconds: int = Field(
        default=24 * 3600,
        ge=1,
        description="TTL for persisted product-detail payloads, in seconds.",
    )

    # ------------------------------------------------------------------ #
    # Voice -- Deepgram STT
    # ------------------------------------------------------------------ #
    # Both voice keys are optional at import time; the voice WebSocket
    # endpoint validates them at connection time so the rest of the app
    # (search, product, chat) works without them.
    deepgram_api_key: str = Field(
        default="",
        description="Deepgram API key. Required for the voice endpoint.",
    )
    deepgram_model: str = Field(
        default="nova-3",
        description="Deepgram STT model identifier.",
    )
    deepgram_language: str = Field(
        default="multi",
        description=(
            "Deepgram language hint. 'multi' handles English/Roman-Urdu "
            "code-switching; use 'en' if you observe misdetections."
        ),
    )
    stt_sample_rate: int = Field(
        default=16000,
        ge=8000,
        le=48000,
        description="Sample rate the browser must send audio at, in Hz.",
    )

    # ------------------------------------------------------------------ #
    # Voice -- ElevenLabs TTS
    # ------------------------------------------------------------------ #
    elevenlabs_api_key: str = Field(
        default="",
        description="ElevenLabs API key. Required for the voice endpoint.",
    )
    elevenlabs_voice_id: str = Field(
        default="21m00Tcm4TlvDq8ikWAM",
        description=(
            "ElevenLabs voice identifier. The default is the premade "
            "'Rachel' voice -- clean and neutral for shopping replies."
        ),
    )
    elevenlabs_model_id: str = Field(
        default="eleven_flash_v2_5",
        description=(
            "ElevenLabs model identifier. 'eleven_flash_v2_5' trades a "
            "little prosody quality for sub-100 ms first-byte latency."
        ),
    )
    tts_sample_rate: int = Field(
        default=24000,
        ge=8000,
        le=48000,
        description="Sample rate ElevenLabs returns audio at, in Hz.",
    )
    voice_prewarm_fillers: bool = Field(
        default=False,
        description=(
            "Pre-synthesize the latency-masking filler phrases at "
            "startup. Costs a few ElevenLabs characters per boot."
        ),
    )

    # ------------------------------------------------------------------ #
    # Logging
    # ------------------------------------------------------------------ #
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO",
        description="Root log level. DEBUG is verbose and should be dev-only.",
    )
    log_format: Literal["console", "json"] = Field(
        default="console",
        description="Log output format. Use 'json' in production.",
    )

    # ------------------------------------------------------------------ #
    # Validators
    # ------------------------------------------------------------------ #
    @field_validator("firecrawl_api_key", mode="before")
    @classmethod
    def _validate_firecrawl_key(cls, value: object) -> str:
        """Reject missing, empty, or whitespace-only Firecrawl keys early.

        Runs in ``mode="before"`` so that a missing environment variable
        (which arrives here as ``None``) is caught before pydantic tries to
        coerce it to ``str``. Without this, a missing key produces an
        ``AttributeError`` instead of the expected ``ValidationError``.

        Args:
            value: Raw value read from the environment, or ``None`` when the
                variable is unset.

        Returns:
            The stripped API key.

        Raises:
            ValueError: If the key is missing, non-string, empty, or
                whitespace-only.
        """
        if value is None:
            raise ValueError(
                "FIRECRAWL_API_KEY is required and must not be empty. "
                "Add it to your .env file."
            )
        if not isinstance(value, str):
            raise ValueError(
                f"FIRECRAWL_API_KEY must be a string, got {type(value).__name__}."
            )
        stripped = value.strip()
        if not stripped:
            raise ValueError(
                "FIRECRAWL_API_KEY is required and must not be empty. "
                "Add it to your .env file."
            )
        return stripped

    @field_validator("daraz_base_url")
    @classmethod
    def _strip_trailing_slash(cls, value: str) -> str:
        """Normalise base URLs by removing any trailing slash.

        Args:
            value: Raw base URL.

        Returns:
            Base URL without a trailing slash.
        """
        return value.rstrip("/")

@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached Settings instance.

    Caching avoids re-parsing the environment on every import while still
    allowing tests to call ``get_settings.cache_clear()`` when they need to
    reload configuration.

    Returns:
        The singleton Settings instance.
    """
    return Settings()

# Module-level singleton -- the preferred import target for the rest of the app.
settings: Settings = get_settings()
