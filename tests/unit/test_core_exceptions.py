"""Unit tests for ``daraz_ai_shopping_assistant.core.exceptions``."""

from __future__ import annotations

import pytest

from daraz_ai_shopping_assistant.core.exceptions import (
    DarazScraperError,
    InvalidRequestError,
    ParseError,
    ProductNotFoundError,
    ScraperError,
    ScraperTimeoutError,
    UpstreamRateLimitError,
)


def test_base_exception_str_without_context() -> None:
    """A base exception with no context returns its message verbatim."""
    exc = DarazScraperError("something went wrong")
    assert str(exc) == "something went wrong"


def test_base_exception_str_with_context() -> None:
    """Context is appended in ``key=value`` form."""
    exc = DarazScraperError("boom", context={"query": "mouse", "page": 1})
    text = str(exc)
    assert "boom" in text
    assert "query='mouse'" in text
    assert "page=1" in text


@pytest.mark.parametrize(
    ("exc_cls", "expected_status"),
    [
        (InvalidRequestError, 400),
        (ProductNotFoundError, 404),
        (UpstreamRateLimitError, 429),
        (ScraperError, 502),
        (ScraperTimeoutError, 504),
        (ParseError, 502),
        (DarazScraperError, 500),
    ],
)
def test_http_status_mapping(
    exc_cls: type[DarazScraperError], expected_status: int
) -> None:
    """Each exception class exposes the documented HTTP status."""
    assert exc_cls.http_status == expected_status


def test_scraper_error_captures_url_and_status() -> None:
    """ScraperError stores url and upstream_status and echoes them in str()."""
    exc = ScraperError("bad gateway", url="https://example.com", upstream_status=502)
    assert exc.url == "https://example.com"
    assert exc.upstream_status == 502
    text = str(exc)
    assert "url='https://example.com'" in text
    assert "upstream_status=502" in text


def test_parse_error_records_source() -> None:
    """ParseError records the failing parser's source tag."""
    exc = ParseError("could not extract price", source="search")
    assert exc.source == "search"
    assert "source='search'" in str(exc)


def test_exceptions_are_catchable_via_base() -> None:
    """All custom exceptions derive from DarazScraperError."""
    for exc_cls in (
        InvalidRequestError,
        ProductNotFoundError,
        UpstreamRateLimitError,
        ScraperError,
        ScraperTimeoutError,
        ParseError,
    ):
        assert issubclass(exc_cls, DarazScraperError)
