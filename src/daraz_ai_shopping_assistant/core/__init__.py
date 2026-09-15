"""Core utilities: configuration, logging, and custom exceptions."""

from daraz_ai_shopping_assistant.core.config import settings
from daraz_ai_shopping_assistant.core.exceptions import (
    DarazScraperError,
    ParseError,
    ProductNotFoundError,
    ScraperError,
    ScraperTimeoutError,
    UpstreamRateLimitError,
)
from daraz_ai_shopping_assistant.core.logging import configure_logging, get_logger

__all__ = [
    "DarazScraperError",
    "ParseError",
    "ProductNotFoundError",
    "ScraperError",
    "ScraperTimeoutError",
    "UpstreamRateLimitError",
    "configure_logging",
    "get_logger",
    "settings",
]
