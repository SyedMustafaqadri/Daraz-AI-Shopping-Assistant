"""Unit tests for ``daraz_ai_shopping_assistant.core.logging``."""

from __future__ import annotations

import json
import logging

import pytest

from daraz_ai_shopping_assistant.core.logging import (
    ConsoleFormatter,
    JsonFormatter,
    configure_logging,
    get_logger,
)


def _make_record(
    msg: str,
    *,
    level: int = logging.INFO,
    ctx: dict[str, object] | None = None,
) -> logging.LogRecord:
    """Build a LogRecord with optional structured context.

    Args:
        msg: Log message.
        level: Logging level.
        ctx: Optional structured context attached under ``record.ctx``.

    Returns:
        A configured :class:`logging.LogRecord`.
    """
    record = logging.LogRecord(
        name="test.logger",
        level=level,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=(),
        exc_info=None,
    )
    if ctx is not None:
        record.ctx = ctx  # type: ignore[attr-defined]
    return record


def test_json_formatter_emits_valid_json() -> None:
    """JsonFormatter output must be parseable JSON with expected keys."""
    formatter = JsonFormatter()
    record = _make_record("SEARCH_STARTED", ctx={"query": "mouse", "page": 1})
    payload = json.loads(formatter.format(record))

    assert payload["level"] == "INFO"
    assert payload["logger"] == "test.logger"
    assert payload["msg"] == "SEARCH_STARTED"
    assert payload["ctx"] == {"query": "mouse", "page": 1}


def test_json_formatter_without_ctx() -> None:
    """JsonFormatter must not include a 'ctx' key when none was supplied."""
    formatter = JsonFormatter()
    payload = json.loads(formatter.format(_make_record("plain message")))
    assert "ctx" not in payload


def test_json_formatter_includes_exception() -> None:
    """JsonFormatter must serialise exception info under 'exc'."""
    formatter = JsonFormatter()
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        record = logging.LogRecord(
            name="t",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="failed",
            args=(),
            exc_info=sys.exc_info(),
        )
    payload = json.loads(formatter.format(record))
    assert "exc" in payload
    assert "ValueError" in payload["exc"]


def test_console_formatter_multiline_with_ctx() -> None:
    """ConsoleFormatter must render ctx on its own indented line."""
    formatter = ConsoleFormatter()
    text = formatter.format(_make_record("SEARCH_STARTED", ctx={"page": 1}))
    assert "SEARCH_STARTED" in text
    assert "ctx: page=1" in text
    assert "\n" in text


def test_console_formatter_without_ctx() -> None:
    """ConsoleFormatter must not append a ctx line when none was supplied."""
    formatter = ConsoleFormatter()
    text = formatter.format(_make_record("plain"))
    assert "ctx:" not in text


def test_configure_logging_is_idempotent() -> None:
    """Repeated calls must not stack handlers on the root logger."""
    configure_logging(level="INFO", fmt="console")
    configure_logging(level="INFO", fmt="console")
    root = logging.getLogger()
    assert len(root.handlers) == 1
    assert root.level == logging.INFO


def test_configure_logging_switches_format() -> None:
    """Switching to JSON must replace the handler's formatter."""
    configure_logging(level="DEBUG", fmt="json")
    root = logging.getLogger()
    assert isinstance(root.handlers[0].formatter, JsonFormatter)
    assert root.level == logging.DEBUG


def test_get_logger_returns_named_logger() -> None:
    """get_logger must return a Logger with the requested name."""
    logger = get_logger("daraz.test")
    assert isinstance(logger, logging.Logger)
    assert logger.name == "daraz.test"


@pytest.fixture(autouse=True)
def _restore_logging() -> None:
    """Restore a sane logging state after each test."""
    yield
    configure_logging(level="INFO", fmt="console")
