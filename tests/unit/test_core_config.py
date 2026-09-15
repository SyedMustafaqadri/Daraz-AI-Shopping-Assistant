"""Unit tests for ``daraz_ai_shopping_assistant.core.config``.

These tests verify defaults, validation, and the ``get_settings`` cache.
They deliberately override environment variables to exercise the validators.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from daraz_ai_shopping_assistant.core.config import Settings, get_settings


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove every FIRECRAWL/DARAZ/APP env var that could bleed in.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    for key in (
        "FIRECRAWL_API_KEY",
        "FIRECRAWL_BASE_URL",
        "DARAZ_BASE_URL",
        "APP_ENV",
        "APP_DEBUG",
        "LOG_LEVEL",
        "LOG_FORMAT",
    ):
        monkeypatch.delenv(key, raising=False)


def test_settings_requires_firecrawl_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing FIRECRAWL_API_KEY must raise a ValidationError."""
    _clear_env(monkeypatch)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_settings_rejects_blank_firecrawl_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A whitespace-only FIRECRAWL_API_KEY must be rejected."""
    _clear_env(monkeypatch)
    monkeypatch.setenv("FIRECRAWL_API_KEY", "   ")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_settings_strips_firecrawl_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Leading/trailing whitespace must be stripped from the API key."""
    _clear_env(monkeypatch)
    monkeypatch.setenv("FIRECRAWL_API_KEY", "  fc-abc123  ")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.firecrawl_api_key == "fc-abc123"


def test_settings_strips_trailing_slash_on_base_urls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Daraz base URL must have no trailing slash after validation."""
    _clear_env(monkeypatch)
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-abc123")
    monkeypatch.setenv("DARAZ_BASE_URL", "https://www.daraz.pk/")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.daraz_base_url == "https://www.daraz.pk"


def test_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Documented defaults must hold when only the API key is set."""
    _clear_env(monkeypatch)
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-abc123")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.app_env == "dev"
    assert settings.app_debug is False
    assert settings.api_v1_prefix == "/api/v1"
    assert settings.api_port == 8000
    assert settings.daraz_default_currency == "PKR"
    assert settings.daraz_items_per_page == 40
    assert settings.log_level == "INFO"
    assert settings.log_format == "console"


def test_get_settings_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    """``get_settings`` must return the same instance on repeated calls."""
    _clear_env(monkeypatch)
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-cachetest")
    get_settings.cache_clear()
    first = get_settings()
    second = get_settings()
    assert first is second
    get_settings.cache_clear()
