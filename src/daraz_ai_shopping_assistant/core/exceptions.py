"""Custom exception hierarchy for the Daraz AI Shopping Assistant.

Every exception raised by application code must derive from
:class:`DarazScraperError` (for scraping/parsing failures) or a FastAPI-native
exception. This lets API-level exception handlers map failures to HTTP
status codes without inspecting exception messages.

HTTP mapping (see ``Specification.md`` Section 29):

    ===========================  =====
    Exception                    HTTP
    ===========================  =====
    InvalidRequestError          400
    ProductNotFoundError         404
    UpstreamRateLimitError       429
    ScraperError                 502
    ScraperTimeoutError          504
    ParseError                   502
    VoiceError                   500 (used only by the WebSocket endpoint)
    DarazScraperError            500
    ===========================  =====
"""

from __future__ import annotations


class DarazScraperError(Exception):
    """Base class for all application-specific exceptions.

    Attributes:
        message: Human-readable description of the failure.
        context: Optional structured context (query, url, product_id, ...).
    """

    http_status: int = 500

    def __init__(
        self, message: str, *, context: dict[str, object] | None = None
    ) -> None:
        """Initialise the exception.

        Args:
            message: Human-readable description of the failure.
            context: Optional structured context for logging.
        """
        super().__init__(message)
        self.message = message
        self.context: dict[str, object] = context or {}

    def __str__(self) -> str:
        """Return a readable representation including context if present."""
        if not self.context:
            return self.message
        parts = ", ".join(f"{k}={v!r}" for k, v in self.context.items())
        return f"{self.message} ({parts})"

# ---------------------------------------------------------------------- #
# Request / validation errors
# ---------------------------------------------------------------------- #
class InvalidRequestError(DarazScraperError):
    """Raised when a caller supplies invalid request parameters.

    Maps to HTTP 400.
    """

    http_status = 400

# ---------------------------------------------------------------------- #
# Not-found errors
# ---------------------------------------------------------------------- #
class ProductNotFoundError(DarazScraperError):
    """Raised when a requested Daraz product cannot be found.

    Maps to HTTP 404.
    """

    http_status = 404

# ---------------------------------------------------------------------- #
# Upstream / scraping errors
# ---------------------------------------------------------------------- #
class ScraperError(DarazScraperError):
    """Raised when the scraper fails to retrieve or return content.

    Maps to HTTP 502 (bad gateway) because the failure is upstream.

    Attributes:
        url: The URL that failed, when known.
        upstream_status: The upstream HTTP status code, when known.
    """

    http_status = 502

    def __init__(
        self,
        message: str,
        *,
        url: str | None = None,
        upstream_status: int | None = None,
        context: dict[str, object] | None = None,
    ) -> None:
        """Initialise the exception with optional upstream metadata.

        Args:
            message: Human-readable description of the failure.
            url: The URL that failed, when known.
            upstream_status: The upstream HTTP status code, when known.
            context: Additional structured context for logging.
        """
        merged: dict[str, object] = dict(context or {})
        if url is not None:
            merged["url"] = url
        if upstream_status is not None:
            merged["upstream_status"] = upstream_status
        super().__init__(message, context=merged)
        self.url = url
        self.upstream_status = upstream_status

class ScraperTimeoutError(ScraperError):
    """Raised when Firecrawl or Daraz does not respond within the timeout.

    Maps to HTTP 504 (gateway timeout).
    """

    http_status = 504

class UpstreamRateLimitError(ScraperError):
    """Raised when Firecrawl or Daraz rate-limits the request.

    Maps to HTTP 429.
    """

    http_status = 429

# ---------------------------------------------------------------------- #
# Parsing errors
# ---------------------------------------------------------------------- #
class ParseError(DarazScraperError):
    """Raised when scraped content cannot be parsed into structured data.

    Maps to HTTP 502 because the payload came from an upstream system.

    Attributes:
        source: A short tag identifying which parser failed
            (e.g., ``"search"``, ``"product"``, ``"recommendation"``).
    """

    http_status = 502

    def __init__(
        self,
        message: str,
        *,
        source: str | None = None,
        context: dict[str, object] | None = None,
    ) -> None:
        """Initialise the exception.

        Args:
            message: Human-readable description of the failure.
            source: Which parser produced the failure.
            context: Additional structured context for logging.
        """
        merged: dict[str, object] = dict(context or {})
        if source is not None:
            merged["source"] = source
        super().__init__(message, context=merged)
        self.source = source

# ---------------------------------------------------------------------- #
# Voice errors
# ---------------------------------------------------------------------- #
class VoiceError(DarazScraperError):
    """Raised when the voice pipeline fails.

    The WebSocket endpoint does not use HTTP status codes; this class
    exists for consistency with the rest of the exception hierarchy and
    for use by the voice adapters when a vendor returns an error.
    """

    http_status = 500

__all__ = [
    "DarazScraperError",
    "InvalidRequestError",
    "ParseError",
    "ProductNotFoundError",
    "ScraperError",
    "ScraperTimeoutError",
    "UpstreamRateLimitError",
    "VoiceError",
]
