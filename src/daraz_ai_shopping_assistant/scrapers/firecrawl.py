"""Firecrawl adapter -- the single boundary between this app and Firecrawl.

Responsibilities:
    1. Wrap the ``firecrawl`` SDK so no other module imports it directly.
    2. Convert Firecrawl responses into either raw Markdown strings or
       structured JSON payloads.
    3. Retry transient failures (rate limits, timeouts) with exponential
       backoff, up to ``settings.firecrawl_max_retries`` attempts.
    4. Map every upstream failure to a typed exception from
       :mod:`daraz_ai_shopping_assistant.core.exceptions`.

Two entry points (see ``docs/ARCHITECTURE-DECISIONS.md`` ADR-001):

    - :meth:`FirecrawlAdapter.scrape` -- returns Markdown. Used for **search
      result pages** and any other page where the layout is uniform enough
      for deterministic parsing.
    - :meth:`FirecrawlAdapter.scrape_json` -- returns a dict conforming to a
      JSON Schema. Used **only** for **product detail pages**, where the
      layout is irregular and LLM-driven extraction is more reliable than
      regex.

Rendering options (added after diagnosing an incomplete product snapshot):

    Daraz product pages render several sections (description, specifications,
    reviews) lazily, after the initial document load. A
    default scrape returns only the above-the-fold content, and Firecrawl's
    ``only_main_content`` filter drops even more. The adapter therefore
    supports two options -- ``wait_for_ms`` and ``only_main_content`` -- that
    are threaded straight through to the SDK. See ``daraz.py`` for how they
    are applied to product pages.

Non-responsibilities:
    - This module knows nothing about Daraz, products, or parsers.
    - It does not validate, normalise, or interpret content.

Example:
    >>> adapter = FirecrawlAdapter()
    >>> markdown = await adapter.scrape("https://www.daraz.pk/catalog/?q=mouse")
    >>> markdown.startswith("#")
    True
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from firecrawl import AsyncFirecrawl

from daraz_ai_shopping_assistant.core.config import settings
from daraz_ai_shopping_assistant.core.exceptions import (
    DarazScraperError,
    ScraperError,
    ScraperTimeoutError,
    UpstreamRateLimitError,
)
from daraz_ai_shopping_assistant.core.logging import get_logger

logger = get_logger(__name__)

#: Fallback prompt used when a caller passes ``prompt=None``. Firecrawl's
#: current ``json`` format requires a non-empty ``prompt`` field, so the
#: adapter always supplies one. Kept deliberately generic -- callers that
#: need a tailored instruction should pass their own.
_DEFAULT_JSON_PROMPT = (
    "Extract the requested data from the page according to the provided "
    "schema. Omit fields you cannot determine from the page -- never guess."
)

# ---------------------------------------------------------------------- #
# Exception classification helpers
# ---------------------------------------------------------------------- #
def _exception_signature(exc: BaseException) -> tuple[str, str]:
    """Return the lowercased exception class name and message.

    Both are used together to classify upstream failures without depending
    on Firecrawl's internal exception hierarchy, which changes between SDK
    versions.

    Args:
        exc: The exception to inspect.

    Returns:
        A ``(class_name_lower, message_lower)`` tuple.
    """
    return type(exc).__name__.lower(), str(exc).lower()

def _is_timeout(exc: BaseException) -> bool:
    """Return True when the exception represents a timeout.

    Args:
        exc: The exception to inspect.

    Returns:
        Whether the exception looks like a timeout.
    """
    name, msg = _exception_signature(exc)
    return "timeout" in name or "timed out" in msg or "timeout" in msg

def _is_rate_limit(exc: BaseException) -> bool:
    """Return True when the exception represents a rate limit.

    Args:
        exc: The exception to inspect.

    Returns:
        Whether the exception looks like a 429 / rate-limit response.
    """
    name, msg = _exception_signature(exc)
    return (
        "ratelimit" in name
        or "rate limit" in msg
        or "too many requests" in msg
        or "429" in msg
    )

def _is_retryable(exc: BaseException) -> bool:
    """Return True when the failure is worth retrying.

    Only timeouts and rate limits are retried. A 4xx auth error or a bad
    URL is not retried -- retrying would just burn quota.

    Args:
        exc: The exception to inspect.

    Returns:
        Whether the adapter should retry the request.
    """
    return _is_timeout(exc) or _is_rate_limit(exc)

def _map_exception(exc: BaseException, url: str) -> DarazScraperError:
    """Translate an upstream exception into a typed application exception.

    Args:
        exc: The original exception raised by Firecrawl.
        url: The URL that was being scraped.

    Returns:
        A :class:`DarazScraperError` subclass ready to propagate to the
        service layer (and eventually the API's exception handlers).
    """
    if _is_timeout(exc):
        return ScraperTimeoutError(
            "Firecrawl request timed out.",
            url=url,
        )
    if _is_rate_limit(exc):
        return UpstreamRateLimitError(
            "Firecrawl rate limit exceeded.",
            url=url,
            upstream_status=429,
        )
    return ScraperError(
        f"Firecrawl scrape failed: {exc}",
        url=url,
    )

# ---------------------------------------------------------------------- #
# Response extraction
# ---------------------------------------------------------------------- #
def _extract_markdown(result: Any) -> str:
    """Pull the Markdown string out of a Firecrawl scrape response.

    The v2 SDK returns a ``Document`` object with a ``.markdown`` attribute.
    Older SDK versions and the v1 API return dict-like objects. This helper
    handles all three shapes so that a minor SDK bump does not break the
    adapter.

    Args:
        result: The object returned by ``AsyncFirecrawl.scrape``.

    Returns:
        The Markdown content as a plain string.

    Raises:
        ScraperError: If no Markdown content is present in any known shape.
    """
    # v2 Document object
    markdown = getattr(result, "markdown", None)
    if isinstance(markdown, str) and markdown:
        return markdown

    # v2 dict response
    if isinstance(result, dict):
        markdown = result.get("markdown")
        if isinstance(markdown, str) and markdown:
            return markdown
        data = result.get("data")
        if isinstance(data, dict):
            markdown = data.get("markdown")
            if isinstance(markdown, str) and markdown:
                return markdown

    # v1 wrapped response (result.data.markdown)
    data = getattr(result, "data", None)
    if data is not None:
        markdown = getattr(data, "markdown", None)
        if isinstance(markdown, str) and markdown:
            return markdown

    raise ScraperError(
        "Firecrawl response contained no Markdown content.",
        context={"result_type": type(result).__name__},
    )

def _extract_json(result: Any) -> dict[str, Any]:
    """Pull the structured JSON payload out of a Firecrawl scrape response.

    Mirrors :func:`_extract_markdown` and supports the same three response
    shapes (v2 Document, v2 dict, v1 wrapper).

    Args:
        result: The object returned by ``AsyncFirecrawl.scrape`` when the
            ``json`` format is requested.

    Returns:
        The structured payload as a dictionary.

    Raises:
        ScraperError: If no JSON payload is present in any known shape.
    """
    # v2 Document object
    payload = getattr(result, "json", None)
    if isinstance(payload, dict):
        return payload

    # v2 dict response
    if isinstance(result, dict):
        payload = result.get("json")
        if isinstance(payload, dict):
            return payload
        data = result.get("data")
        if isinstance(data, dict):
            payload = data.get("json")
            if isinstance(payload, dict):
                return payload

    # v1 wrapped response (result.data.json)
    data = getattr(result, "data", None)
    if data is not None:
        payload = getattr(data, "json", None)
        if isinstance(payload, dict):
            return payload

    raise ScraperError(
        "Firecrawl response contained no JSON payload.",
        context={"result_type": type(result).__name__},
    )

# ---------------------------------------------------------------------- #
# Adapter
# ---------------------------------------------------------------------- #
class FirecrawlAdapter:
    """Thin async wrapper around the Firecrawl SDK.

    The adapter is intentionally stateless except for its SDK client and
    configuration. It is safe to instantiate once at module scope or
    inject via FastAPI's dependency system.

    Attributes:
        _client: The underlying ``AsyncFirecrawl`` instance.
        _max_retries: Maximum number of retry attempts after the first try.
        _backoff_base_seconds: Base delay for exponential backoff.
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        max_retries: int | None = None,
        backoff_base_seconds: float = 1.5,
    ) -> None:
        """Initialise the adapter.

        Args:
            api_key: Firecrawl API key. Defaults to ``settings.firecrawl_api_key``.
            max_retries: Retry attempts on transient failures. Defaults to
                ``settings.firecrawl_max_retries``.
            backoff_base_seconds: Base of the exponential backoff. The delay
                before attempt ``n`` is ``backoff_base_seconds * 2 ** (n - 1)``.
        """
        resolved_key = api_key or settings.firecrawl_api_key
        self._client: AsyncFirecrawl = AsyncFirecrawl(api_key=resolved_key)
        self._max_retries: int = (
            settings.firecrawl_max_retries if max_retries is None else max_retries
        )
        self._backoff_base_seconds: float = backoff_base_seconds

    # ------------------------------------------------------------------ #
    # Markdown path -- search result pages and other uniform layouts
    # ------------------------------------------------------------------ #
    async def scrape(
        self,
        url: str,
        *,
        wait_for_ms: int | None = None,
        only_main_content: bool | None = None,
    ) -> str:
        """Fetch a URL and return its content as Markdown.

        Use this for **search result pages** and any other page whose layout
        is uniform enough for deterministic parsing. For product detail
        pages, use :meth:`scrape_json` instead (see ADR-001).

        Retries transient failures (timeouts, rate limits) with exponential
        backoff. Logs ``FIRECRAWL_REQUEST``, ``FIRECRAWL_RESPONSE``, and
        ``FIRECRAWL_RETRY`` events with structured context.

        Args:
            url: The fully-qualified HTTP(S) URL to scrape.
            wait_for_ms: Milliseconds to wait after page load before
                snapshotting. ``None`` uses Firecrawl's default. Raise this
                for pages with lazily-rendered sections.
            only_main_content: When ``True``, Firecrawl strips navigation,
                footers, and other non-main content. ``None`` uses
                Firecrawl's default. Set to ``False`` when the sections you
                need are being classified as non-main and dropped.

        Returns:
            The page content as Markdown.

        Raises:
            ScraperTimeoutError: When Firecrawl times out after all retries.
            UpstreamRateLimitError: When Firecrawl returns 429 after all retries.
            ScraperError: For any other upstream failure or missing content.
        """
        logger.info(
            "FIRECRAWL_REQUEST",
            extra={
                "ctx": {
                    "url": url,
                    "mode": "markdown",
                    "wait_for_ms": wait_for_ms,
                    "only_main_content": only_main_content,
                }
            },
        )
        started_at = time.monotonic()

        # Build the SDK kwargs once -- the shape does not change per attempt.
        scrape_kwargs: dict[str, Any] = {"formats": ["markdown"]}
        if wait_for_ms is not None:
            scrape_kwargs["wait_for"] = wait_for_ms
        if only_main_content is not None:
            scrape_kwargs["only_main_content"] = only_main_content

        last_exc: BaseException | None = None
        attempt = 0

        while attempt <= self._max_retries:
            attempt += 1
            try:
                result = await self._client.scrape(url, **scrape_kwargs)
                markdown = _extract_markdown(result)
            except Exception as exc:
                last_exc = exc
                if _is_retryable(exc) and attempt <= self._max_retries:
                    delay = self._backoff_base_seconds * (2 ** (attempt - 1))
                    logger.warning(
                        "FIRECRAWL_RETRY",
                        extra={
                            "ctx": {
                                "url": url,
                                "mode": "markdown",
                                "attempt": attempt,
                                "max_retries": self._max_retries,
                                "delay_s": round(delay, 2),
                                "error_type": type(exc).__name__,
                            }
                        },
                    )
                    await asyncio.sleep(delay)
                    continue
                raise _map_exception(exc, url) from exc
            else:
                duration_ms = int((time.monotonic() - started_at) * 1000)
                logger.info(
                    "FIRECRAWL_RESPONSE",
                    extra={
                        "ctx": {
                            "url": url,
                            "mode": "markdown",
                            "chars": len(markdown),
                            "duration_ms": duration_ms,
                            "attempts": attempt,
                        }
                    },
                )
                return markdown

        # Defensive: the loop above either returns or raises, so this branch
        # is only reachable if `_max_retries` is negative (config error).
        raise ScraperError(
            "Firecrawl adapter exhausted retries without a definitive result.",
            url=url,
            context={"last_exception": type(last_exc).__name__ if last_exc else None},
        )

    # ------------------------------------------------------------------ #
    # JSON path -- product detail pages only
    # ------------------------------------------------------------------ #
    async def scrape_json(
        self,
        url: str,
        *,
        schema: dict[str, Any],
        prompt: str | None = None,
        max_age_ms: int | None = None,
        wait_for_ms: int | None = None,
        only_main_content: bool | None = None,
    ) -> dict[str, Any]:
        """Fetch a URL and return structured data matching ``schema``.

        Uses Firecrawl's LLM-driven structured extraction. This is the entry
        point for **product detail pages only** (see ADR-001). Do not use it
        for search result pages -- those go through :meth:`scrape` and the
        deterministic Markdown parser.

        Firecrawl's current API expects the ``json`` format as a **dict**
        with three top-level keys: ``type``, ``prompt``, and ``schema``.
        The older ``formats=["json"]`` + ``json_options={...}`` shape is no
        longer accepted.

        Retries transient failures (timeouts, rate limits) with exponential
        backoff, mirroring :meth:`scrape`.

        Args:
            url: The fully-qualified HTTP(S) URL to scrape.
            schema: A JSON Schema (dict) describing the shape of the payload
                Firecrawl should produce. For Pydantic models, call
                ``Model.model_json_schema()`` to obtain this dict.
            prompt: Optional extraction instruction to guide the LLM. When
                ``None``, a generic default is used (Firecrawl requires a
                non-empty prompt field).
            max_age_ms: Maximum age, in milliseconds, of a reused Firecrawl
                cache entry. ``None`` lets Firecrawl choose. ``0`` forces a
                live scrape (see ``references/freshness-and-liveness.md``).
            wait_for_ms: Milliseconds to wait after page load before
                snapshotting. Raise this for pages with lazily-rendered
                sections -- Daraz product descriptions, specifications,
                and reviews -- are below the fold.
            only_main_content: When ``True``, Firecrawl strips navigation,
                footers, and other non-main content. Set to ``False`` on
                pages where the sections you need are being classified as
                non-main and dropped.

        Returns:
            A dict conforming to ``schema``.

        Raises:
            ScraperTimeoutError: When Firecrawl times out after all retries.
            UpstreamRateLimitError: When Firecrawl returns 429 after all retries.
            ScraperError: For any other upstream failure or missing payload.
        """
        logger.info(
            "FIRECRAWL_REQUEST",
            extra={
                "ctx": {
                    "url": url,
                    "mode": "json",
                    "wait_for_ms": wait_for_ms,
                    "only_main_content": only_main_content,
                }
            },
        )
        started_at = time.monotonic()

        # Firecrawl's json format requires all three keys at the top level
        # of the format dict. prompt must be non-empty; fall back to the
        # default when the caller did not supply one.
        resolved_prompt = prompt if prompt else _DEFAULT_JSON_PROMPT
        json_format: dict[str, Any] = {
            "type": "json",
            "prompt": resolved_prompt,
            "schema": schema,
        }

        # Build the SDK kwargs once -- the shape does not change per attempt.
        scrape_kwargs: dict[str, Any] = {"formats": [json_format]}
        if max_age_ms is not None:
            scrape_kwargs["max_age"] = max_age_ms
        if wait_for_ms is not None:
            scrape_kwargs["wait_for"] = wait_for_ms
        if only_main_content is not None:
            scrape_kwargs["only_main_content"] = only_main_content

        last_exc: BaseException | None = None
        attempt = 0

        while attempt <= self._max_retries:
            attempt += 1
            try:
                result = await self._client.scrape(url, **scrape_kwargs)
                payload = _extract_json(result)
            except Exception as exc:
                last_exc = exc
                if _is_retryable(exc) and attempt <= self._max_retries:
                    delay = self._backoff_base_seconds * (2 ** (attempt - 1))
                    logger.warning(
                        "FIRECRAWL_RETRY",
                        extra={
                            "ctx": {
                                "url": url,
                                "mode": "json",
                                "attempt": attempt,
                                "max_retries": self._max_retries,
                                "delay_s": round(delay, 2),
                                "error_type": type(exc).__name__,
                            }
                        },
                    )
                    await asyncio.sleep(delay)
                    continue
                raise _map_exception(exc, url) from exc
            else:
                duration_ms = int((time.monotonic() - started_at) * 1000)
                logger.info(
                    "FIRECRAWL_RESPONSE",
                    extra={
                        "ctx": {
                            "url": url,
                            "mode": "json",
                            "keys": sorted(payload.keys()),
                            "duration_ms": duration_ms,
                            "attempts": attempt,
                        }
                    },
                )
                return payload

        # Defensive -- see :meth:`scrape`.
        raise ScraperError(
            "Firecrawl adapter exhausted retries without a definitive result.",
            url=url,
            context={"last_exception": type(last_exc).__name__ if last_exc else None},
        )

__all__ = ["FirecrawlAdapter"]
