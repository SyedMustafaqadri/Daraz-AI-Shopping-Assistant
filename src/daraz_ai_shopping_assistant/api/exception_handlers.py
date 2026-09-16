"""Global exception handlers for the FastAPI application.

Maps application exceptions to HTTP responses. Two handlers are registered:

    - daraz_scraper_error_handler -- covers every exception in the
      DarazScraperError hierarchy. Each subclass carries its own
      http_status class attribute (400, 404, 429, 502, 504), so the
      handler is agnostic to the specific failure.

    - unhandled_exception_handler -- catch-all for anything not already
      handled. Returns a generic 500 without leaking internals.

The full exception context is logged server-side but never returned to
the client. See Specification.md Section 29.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from daraz_ai_shopping_assistant.core.exceptions import DarazScraperError
from daraz_ai_shopping_assistant.core.logging import get_logger

logger = get_logger(__name__)


async def daraz_scraper_error_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    """Translate a DarazScraperError into an HTTP JSON response.

    The parameter type is Exception (not DarazScraperError) because
    Starlette's add_exception_handler is typed as accepting a handler that
    takes the base Exception. Callables are contravariant in their
    parameter types, so a handler declared with a narrower parameter is
    rejected by mypy. Widening the parameter and narrowing with isinstance
    at runtime is the canonical Starlette pattern.

    Args:
        request: The incoming HTTP request (used for logging context).
        exc: The exception raised by the service or scraper layer.

    Returns:
        A JSONResponse with {"detail": <message>} and the status code
        defined by exc.http_status.

    Raises:
        Exception: Re-raises any non-DarazScraperError so the catch-all
            handler takes over.
    """
    if not isinstance(exc, DarazScraperError):
        raise exc

    logger.warning(
        "API_ERROR",
        extra={
            "ctx": {
                "path": request.url.path,
                "method": request.method,
                "status": exc.http_status,
                "message": exc.message,
                "context": dict(exc.context),
            }
        },
    )
    return JSONResponse(
        status_code=exc.http_status,
        content={"detail": exc.message},
    )


async def unhandled_exception_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    """Catch-all handler for exceptions without a specific handler.

    Returns a generic 500 response so that stack traces and internal
    details never leak to API consumers. The full traceback is logged.

    Args:
        request: The incoming HTTP request.
        exc: The unhandled exception.

    Returns:
        A JSONResponse with a generic 500 body.
    """
    logger.exception(
        "API_UNHANDLED_EXCEPTION",
        extra={
            "ctx": {
                "path": request.url.path,
                "method": request.method,
                "exception": type(exc).__name__,
            }
        },
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error."},
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Register the application's exception handlers on app.

    Order matters: specific handlers must be registered before the
    catch-all Exception handler so that Starlette dispatches to the most
    specific match first.

    Args:
        app: The FastAPI application instance.
    """
    app.add_exception_handler(DarazScraperError, daraz_scraper_error_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)


__all__ = [
    "daraz_scraper_error_handler",
    "register_exception_handlers",
    "unhandled_exception_handler",
]
