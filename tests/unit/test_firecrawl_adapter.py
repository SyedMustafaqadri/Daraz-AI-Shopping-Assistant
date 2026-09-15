"""Unit tests for :mod:`daraz_ai_shopping_assistant.scrapers.firecrawl`.

The Firecrawl SDK client is fully mocked, so these tests never touch the
network and never require an API key. They verify:

    - Happy-path Markdown and JSON extraction from multiple SDK response shapes.
    - Exception classification (timeout, rate limit, generic).
    - Retry behaviour with exponential backoff.
    - Structured logging events for both modes.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from daraz_ai_shopping_assistant.core.exceptions import (
    ScraperError,
    ScraperTimeoutError,
    UpstreamRateLimitError,
)
from daraz_ai_shopping_assistant.scrapers.firecrawl import (
    FirecrawlAdapter,
    _extract_json,
    _extract_markdown,
    _is_rate_limit,
    _is_retryable,
    _is_timeout,
    _map_exception,
)


# ---------------------------------------------------------------------- #
# Test doubles
# ---------------------------------------------------------------------- #
class FakeFirecrawlTimeout(Exception):  # noqa: N818
    """Stand-in for the SDK's timeout exception."""

class FakeFirecrawlRateLimit(Exception):  # noqa: N818
    """Stand-in for the SDK's rate-limit exception."""

FakeFirecrawlTimeout.__name__ = "FirecrawlTimeoutError"
FakeFirecrawlRateLimit.__name__ = "RateLimitError"

def _document(markdown: str) -> Any:
    """Return a fake v2 Document object carrying only Markdown.

    Args:
        markdown: Markdown content to embed.

    Returns:
        A simple object with a ``.markdown`` attribute.
    """
    doc = MagicMock()
    doc.markdown = markdown
    doc.json = None
    return doc

def _json_document(payload: dict[str, Any]) -> Any:
    """Return a fake v2 Document object carrying a JSON payload.

    Args:
        payload: The structured payload to embed.

    Returns:
        A simple object with a ``.json`` attribute and no ``.markdown``.
    """
    doc = MagicMock()
    doc.markdown = None
    doc.json = payload
    return doc

# ---------------------------------------------------------------------- #
# _extract_markdown
# ---------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "result",
    [
        _document("# hello"),
        {"markdown": "# hello"},
        {"data": {"markdown": "# hello"}},
    ],
    ids=["v2-document", "v2-dict", "v1-wrapped-dict"],
)
def test_extract_markdown_handles_known_shapes(result: Any) -> None:
    """All three supported response shapes must yield the Markdown string."""
    assert _extract_markdown(result) == "# hello"

def test_extract_markdown_v1_attribute_shape() -> None:
    """A response with ``.data.markdown`` (v1 style) must be handled."""
    outer = MagicMock()
    outer.markdown = None
    inner = MagicMock()
    inner.markdown = "# v1"
    outer.data = inner
    assert _extract_markdown(outer) == "# v1"

def test_extract_markdown_missing_content_raises() -> None:
    """A response with no Markdown anywhere must raise ScraperError."""
    with pytest.raises(ScraperError):
        _extract_markdown({"foo": "bar"})

# ---------------------------------------------------------------------- #
# _extract_json
# ---------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "result",
    [
        _json_document({"title": "Mouse", "price": 579}),
        {"json": {"title": "Mouse", "price": 579}},
        {"data": {"json": {"title": "Mouse", "price": 579}}},
    ],
    ids=["v2-document", "v2-dict", "v1-wrapped-dict"],
)
def test_extract_json_handles_known_shapes(result: Any) -> None:
    """All three supported response shapes must yield the JSON payload."""
    assert _extract_json(result) == {"title": "Mouse", "price": 579}

def test_extract_json_v1_attribute_shape() -> None:
    """A response with ``.data.json`` (v1 style) must be handled."""
    outer = MagicMock()
    outer.json = None
    inner = MagicMock()
    inner.json = {"id": "i1"}
    outer.data = inner
    assert _extract_json(outer) == {"id": "i1"}

def test_extract_json_missing_payload_raises() -> None:
    """A response with no JSON anywhere must raise ScraperError."""
    with pytest.raises(ScraperError):
        _extract_json({"foo": "bar"})

def test_extract_json_does_not_confuse_markdown_only_response() -> None:
    """A Markdown-only response must raise ScraperError when JSON is requested."""
    with pytest.raises(ScraperError):
        _extract_json(_document("# hello"))

# ---------------------------------------------------------------------- #
# Exception classification
# ---------------------------------------------------------------------- #
def test_is_timeout_by_class_name() -> None:
    """An exception whose class name contains 'timeout' is a timeout."""
    assert _is_timeout(FakeFirecrawlTimeout("anything"))

def test_is_timeout_by_message() -> None:
    """An exception whose message mentions 'timed out' is a timeout."""
    assert _is_timeout(RuntimeError("Request timed out after 30s"))

def test_is_rate_limit_by_class_name() -> None:
    """An exception whose class name contains 'ratelimit' is a rate limit."""
    assert _is_rate_limit(FakeFirecrawlRateLimit("nope"))

def test_is_rate_limit_by_message() -> None:
    """A 429 mention in the message classifies as a rate limit."""
    assert _is_rate_limit(RuntimeError("HTTP 429 Too Many Requests"))

def test_is_retryable_positive() -> None:
    """Timeouts and rate limits are retryable."""
    assert _is_retryable(FakeFirecrawlTimeout("boom"))
    assert _is_retryable(FakeFirecrawlRateLimit("boom"))

def test_is_retryable_negative() -> None:
    """Generic failures are not retryable."""
    assert not _is_retryable(ValueError("bad input"))

@pytest.mark.parametrize(
    ("exc", "expected_cls"),
    [
        (FakeFirecrawlTimeout("x"), ScraperTimeoutError),
        (FakeFirecrawlRateLimit("x"), UpstreamRateLimitError),
        (ValueError("x"), ScraperError),
    ],
    ids=["timeout", "rate-limit", "generic"],
)
def test_map_exception(exc: BaseException, expected_cls: type[Exception]) -> None:
    """Each upstream exception maps to the correct typed exception."""
    mapped = _map_exception(exc, "https://example.com")
    assert isinstance(mapped, expected_cls)
    assert "https://example.com" in str(mapped)

# ---------------------------------------------------------------------- #
# Fixtures
# ---------------------------------------------------------------------- #
@pytest.fixture()
def patched_client() -> Any:
    """Patch ``AsyncFirecrawl`` in the adapter module.

    Yields:
        The mocked ``AsyncFirecrawl`` class, whose ``.return_value`` is the
        client instance used by the adapter under test.
    """
    with patch(
        "daraz_ai_shopping_assistant.scrapers.firecrawl.AsyncFirecrawl"
    ) as mock_cls:
        yield mock_cls

# ---------------------------------------------------------------------- #
# FirecrawlAdapter.scrape — happy path
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio()
async def test_scrape_returns_markdown(patched_client: Any) -> None:
    """A successful scrape returns the Markdown string unchanged."""
    client = patched_client.return_value
    client.scrape = AsyncMock(return_value=_document("# Daraz"))

    adapter = FirecrawlAdapter(api_key="fc-test", max_retries=0)
    result = await adapter.scrape("https://example.com")

    assert result == "# Daraz"
    client.scrape.assert_awaited_once_with("https://example.com", formats=["markdown"])

@pytest.mark.asyncio()
async def test_scrape_passes_api_key_to_client(patched_client: Any) -> None:
    """The adapter forwards its resolved API key to the SDK client."""
    FirecrawlAdapter(api_key="fc-abc123", max_retries=0)
    patched_client.assert_called_once_with(api_key="fc-abc123")

# ---------------------------------------------------------------------- #
# FirecrawlAdapter.scrape — retries
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio()
async def test_scrape_retries_on_rate_limit(patched_client: Any) -> None:
    """A rate-limit on the first attempt is retried and eventually succeeds."""
    client = patched_client.return_value
    client.scrape = AsyncMock(
        side_effect=[FakeFirecrawlRateLimit("429"), _document("# ok")]
    )

    adapter = FirecrawlAdapter(
        api_key="fc-test", max_retries=2, backoff_base_seconds=0.0
    )
    result = await adapter.scrape("https://example.com")

    assert result == "# ok"
    assert client.scrape.await_count == 2

@pytest.mark.asyncio()
async def test_scrape_gives_up_after_max_retries(patched_client: Any) -> None:
    """Exhausting retries raises UpstreamRateLimitError."""
    client = patched_client.return_value
    client.scrape = AsyncMock(side_effect=FakeFirecrawlRateLimit("429"))

    adapter = FirecrawlAdapter(
        api_key="fc-test", max_retries=2, backoff_base_seconds=0.0
    )

    with pytest.raises(UpstreamRateLimitError):
        await adapter.scrape("https://example.com")

    assert client.scrape.await_count == 3  # initial + 2 retries

@pytest.mark.asyncio()
async def test_scrape_does_not_retry_on_generic_error(patched_client: Any) -> None:
    """A non-retryable failure aborts immediately."""
    client = patched_client.return_value
    client.scrape = AsyncMock(side_effect=ValueError("bad input"))

    adapter = FirecrawlAdapter(
        api_key="fc-test", max_retries=3, backoff_base_seconds=0.0
    )

    with pytest.raises(ScraperError):
        await adapter.scrape("https://example.com")

    assert client.scrape.await_count == 1

@pytest.mark.asyncio()
async def test_scrape_timeout_after_retries_maps_correctly(
    patched_client: Any,
) -> None:
    """A persistent timeout maps to ScraperTimeoutError."""
    client = patched_client.return_value
    client.scrape = AsyncMock(side_effect=FakeFirecrawlTimeout("timed out"))

    adapter = FirecrawlAdapter(
        api_key="fc-test", max_retries=1, backoff_base_seconds=0.0
    )

    with pytest.raises(ScraperTimeoutError):
        await adapter.scrape("https://example.com")

# ---------------------------------------------------------------------- #
# FirecrawlAdapter.scrape_json — happy path
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio()
async def test_scrape_json_returns_payload(patched_client: Any) -> None:
    """A successful JSON scrape returns the structured payload."""
    client = patched_client.return_value
    payload = {"id": "i1", "title": "Mouse", "price": 579.0}
    client.scrape = AsyncMock(return_value=_json_document(payload))

    adapter = FirecrawlAdapter(api_key="fc-test", max_retries=0)
    schema = {"type": "object", "properties": {"id": {"type": "string"}}}
    result = await adapter.scrape_json("https://example.com", schema=schema)

    assert result == payload

@pytest.mark.asyncio()
async def test_scrape_json_passes_schema_and_prompt(patched_client: Any) -> None:
    """The schema and prompt are forwarded to the SDK as json_options."""
    client = patched_client.return_value
    client.scrape = AsyncMock(return_value=_json_document({"ok": True}))

    adapter = FirecrawlAdapter(api_key="fc-test", max_retries=0)
    schema = {"type": "object"}
    await adapter.scrape_json(
        "https://example.com", schema=schema, prompt="Extract product info"
    )

    client.scrape.assert_awaited_once()
    _, kwargs = client.scrape.call_args
    assert kwargs["formats"] == ["json"]
    assert kwargs["json_options"]["schema"] == schema
    assert kwargs["json_options"]["prompt"] == "Extract product info"

@pytest.mark.asyncio()
async def test_scrape_json_omits_prompt_when_not_supplied(patched_client: Any) -> None:
    """When no prompt is given, json_options must only contain the schema."""
    client = patched_client.return_value
    client.scrape = AsyncMock(return_value=_json_document({"ok": True}))

    adapter = FirecrawlAdapter(api_key="fc-test", max_retries=0)
    schema = {"type": "object"}
    await adapter.scrape_json("https://example.com", schema=schema)

    _, kwargs = client.scrape.call_args
    assert "prompt" not in kwargs["json_options"]

@pytest.mark.asyncio()
async def test_scrape_json_forwards_max_age(patched_client: Any) -> None:
    """``max_age_ms`` is forwarded to the SDK as ``max_age``."""
    client = patched_client.return_value
    client.scrape = AsyncMock(return_value=_json_document({"ok": True}))

    adapter = FirecrawlAdapter(api_key="fc-test", max_retries=0)
    await adapter.scrape_json(
        "https://example.com", schema={"type": "object"}, max_age_ms=0
    )

    _, kwargs = client.scrape.call_args
    assert kwargs["max_age"] == 0

# ---------------------------------------------------------------------- #
# FirecrawlAdapter.scrape_json — retries and errors
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio()
async def test_scrape_json_retries_on_rate_limit(patched_client: Any) -> None:
    """A rate-limit on the JSON path is retried and eventually succeeds."""
    client = patched_client.return_value
    client.scrape = AsyncMock(
        side_effect=[FakeFirecrawlRateLimit("429"), _json_document({"ok": True})]
    )

    adapter = FirecrawlAdapter(
        api_key="fc-test", max_retries=2, backoff_base_seconds=0.0
    )
    result = await adapter.scrape_json(
        "https://example.com", schema={"type": "object"}
    )

    assert result == {"ok": True}
    assert client.scrape.await_count == 2

@pytest.mark.asyncio()
async def test_scrape_json_missing_payload_raises_scraper_error(
    patched_client: Any,
) -> None:
    """A response with no JSON payload must raise ScraperError."""
    client = patched_client.return_value
    client.scrape = AsyncMock(return_value=_document("# only markdown"))

    adapter = FirecrawlAdapter(api_key="fc-test", max_retries=0)
    with pytest.raises(ScraperError):
        await adapter.scrape_json("https://example.com", schema={"type": "object"})

# ---------------------------------------------------------------------- #
# FirecrawlAdapter — logging
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio()
async def test_scrape_logs_request_and_response(
    patched_client: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """FIRECRAWL_REQUEST and FIRECRAWL_RESPONSE events must be emitted."""
    client = patched_client.return_value
    client.scrape = AsyncMock(return_value=_document("# ok"))

    adapter = FirecrawlAdapter(api_key="fc-test", max_retries=0)

    with caplog.at_level("INFO", logger="daraz_ai_shopping_assistant.scrapers.firecrawl"):
        await adapter.scrape("https://example.com")

    messages = [record.message for record in caplog.records]
    assert "FIRECRAWL_REQUEST" in messages
    assert "FIRECRAWL_RESPONSE" in messages

@pytest.mark.asyncio()
async def test_scrape_logs_retry(
    patched_client: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A retry must emit FIRECRAWL_RETRY at WARNING level."""
    client = patched_client.return_value
    client.scrape = AsyncMock(
        side_effect=[FakeFirecrawlRateLimit("429"), _document("# ok")]
    )

    adapter = FirecrawlAdapter(
        api_key="fc-test", max_retries=1, backoff_base_seconds=0.0
    )

    with caplog.at_level("WARNING", logger="daraz_ai_shopping_assistant.scrapers.firecrawl"):
        await adapter.scrape("https://example.com")

    retries = [r for r in caplog.records if r.message == "FIRECRAWL_RETRY"]
    assert len(retries) == 1

@pytest.mark.asyncio()
async def test_scrape_json_logs_request_with_json_mode(
    patched_client: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The JSON path must log with mode='json' in the request context."""
    client = patched_client.return_value
    client.scrape = AsyncMock(return_value=_json_document({"ok": True}))

    adapter = FirecrawlAdapter(api_key="fc-test", max_retries=0)

    with caplog.at_level("INFO", logger="daraz_ai_shopping_assistant.scrapers.firecrawl"):
        await adapter.scrape_json("https://example.com", schema={"type": "object"})

    request_records = [r for r in caplog.records if r.message == "FIRECRAWL_REQUEST"]
    assert len(request_records) == 1
    assert request_records[0].ctx["mode"] == "json"  # type: ignore[attr-defined]
