"""Application logging configuration.

Provides a single :func:`configure_logging` entry point that sets up either
a human-friendly console formatter (for development) or a JSON formatter
(for production), plus :func:`get_logger` for module-level loggers.

Event-name convention (see ``Specification.md`` §30):
    Log messages that represent significant lifecycle events begin with an
    uppercase token, e.g. ``SEARCH_STARTED``. Include structured context as
    keyword arguments via ``extra={"ctx": {...}}``.
"""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import MutableMapping
from typing import Any, Literal

from daraz_ai_shopping_assistant.core.config import settings


# ---------------------------------------------------------------------- #
# JSON formatter
# ---------------------------------------------------------------------- #
class JsonFormatter(logging.Formatter):
    """Render log records as single-line JSON objects.

    Includes the standard fields plus any structured context attached under
    ``record.ctx`` (a mapping of key → value).
    """

    def format(self, record: logging.LogRecord) -> str:
        """Format a log record as a JSON string.

        Args:
            record: The log record to format.

        Returns:
            A single-line JSON string.
        """
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, datefmt="%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }

        ctx = getattr(record, "ctx", None)
        if isinstance(ctx, MutableMapping) and ctx:
            payload["ctx"] = dict(ctx)

        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=False, default=str)


# ---------------------------------------------------------------------- #
# Console formatter
# ---------------------------------------------------------------------- #
class ConsoleFormatter(logging.Formatter):
    """Human-readable formatter with optional ``ctx`` rendering.

    Example output::

        2026-09-15 10:49:06 | INFO  | daraz.service.search | SEARCH_STARTED
            ctx: query='gaming mouse', page=1
    """

    _FMT = "%(asctime)s | %(levelname)-5s | %(name)s | %(message)s"
    _DATEFMT = "%Y-%m-%d %H:%M:%S"

    def __init__(self) -> None:
        """Initialise the formatter with the module-level format template."""
        super().__init__(fmt=self._FMT, datefmt=self._DATEFMT)

    def format(self, record: logging.LogRecord) -> str:
        """Format a log record for console output.

        Args:
            record: The log record to format.

        Returns:
            A multi-line, human-readable string.
        """
        base = super().format(record)
        ctx = getattr(record, "ctx", None)
        if isinstance(ctx, MutableMapping) and ctx:
            ctx_str = ", ".join(f"{k}={v!r}" for k, v in ctx.items())
            base = f"{base}\n    ctx: {ctx_str}"
        return base


# ---------------------------------------------------------------------- #
# Public API
# ---------------------------------------------------------------------- #
def configure_logging(
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] | None = None,
    fmt: Literal["console", "json"] | None = None,
) -> None:
    """Configure the root logger for the application.

    Idempotent: calling it more than once replaces the existing handler set
    rather than stacking handlers.

    Args:
        level: Override for the log level. Falls back to ``settings.log_level``.
        fmt: Override for the formatter. Falls back to ``settings.log_format``.

    Example:
        >>> configure_logging(level="DEBUG", fmt="console")
    """
    resolved_level = (level or settings.log_level).upper()
    resolved_fmt = fmt or settings.log_format

    handler = logging.StreamHandler(stream=sys.stdout)
    if resolved_fmt == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(ConsoleFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(resolved_level)

    # Quiet down noisy third-party loggers unless we are in DEBUG mode.
    if resolved_level != "DEBUG":
        for noisy in ("httpx", "httpcore", "urllib3", "firecrawl"):
            logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Return a module-level logger.

    Always call this at module import time with ``__name__``::

        from daraz_ai_shopping_assistant.core.logging import get_logger
        logger = get_logger(__name__)

    Args:
        name: Logger name, normally ``__name__``.

    Returns:
        A configured :class:`logging.Logger`.
    """
    return logging.getLogger(name)


__all__ = ["ConsoleFormatter", "JsonFormatter", "configure_logging", "get_logger"]
