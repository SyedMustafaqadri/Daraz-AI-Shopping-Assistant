"""FastAPI application entry point.

Exposes:
    - ``create_app()`` -- factory used by tests and by production.
    - ``app``          -- the module-level instance uvicorn imports.

Run locally:

    uv run uvicorn daraz_ai_shopping_assistant.main:app --reload

Do NOT use ``app.main:app`` -- this project uses a ``src`` layout with the
package ``daraz_ai_shopping_assistant``.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from langgraph.checkpoint.memory import MemorySaver

from daraz_ai_shopping_assistant import __version__
from daraz_ai_shopping_assistant.agents.graph import warm_compiled_graph
from daraz_ai_shopping_assistant.api import api_router
from daraz_ai_shopping_assistant.api.exception_handlers import register_exception_handlers
from daraz_ai_shopping_assistant.core.config import settings
from daraz_ai_shopping_assistant.core.logging import configure_logging, get_logger
from daraz_ai_shopping_assistant.services.product_service import get_product_service
from daraz_ai_shopping_assistant.services.search_service import get_search_service
from daraz_ai_shopping_assistant.storage.json_store import ScrapeStore

logger = get_logger(__name__)

@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """FastAPI lifespan context.

    Runs once at startup and once at shutdown. Configures logging,
    initialises the scrape store, wires the process-wide service
    singletons, and prepares the conversation checkpointer.

    Args:
        app: The FastAPI application.

    Yields:
        Control back to FastAPI for the duration of the app's lifetime.
    """
    configure_logging()
    logger.info(
        "APP_STARTED",
        extra={
            "ctx": {
                "name": settings.app_name,
                "env": settings.app_env,
                "version": __version__,
            }
        },
    )

    # Local JSON persistence for scrape payloads.
    store = ScrapeStore.load(settings.scrape_store_path)
    app.state.scrape_store = store
    # Warm the service singletons so the first request does not pay the
    # cost of wiring them, and so the store is bound before any call.
    get_search_service(store=store)
    get_product_service(store=store)

    # In-memory conversation checkpointer. A new process starts with an
    # empty store: conversations do not survive a restart by design.
    checkpointer = MemorySaver()
    app.state.checkpointer = checkpointer
    warm_compiled_graph(checkpointer=checkpointer)

    try:
        yield
    finally:
        # Prune expired entries on shutdown so the file shrinks between
        # runs. Best-effort: a failure here must not prevent shutdown.
        try:
            removed = await store.prune()
            if removed:
                logger.info(
                    "SCRAPE_STORE_PRUNED",
                    extra={"ctx": {"removed": removed, "remaining": store.size}},
                )
        except Exception:
            logger.exception("SCRAPE_STORE_PRUNE_FAILED")

        logger.info("APP_STOPPED")

def create_app() -> FastAPI:
    """Build and return a configured FastAPI application.

    A factory is used (rather than a module-level side-effect) so that
    tests can build isolated instances with their own dependency overrides.
    The module-level ``app`` at the bottom of this file is what uvicorn
    imports in production.

    Returns:
        A fully configured :class:`FastAPI` instance.
    """
    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description=(
            "Backend for the Daraz AI Shopping Assistant. "
            "Search Daraz products, retrieve product details, and inspect "
            "Daraz's own recommendations."
        ),
        docs_url="/docs" if settings.app_env == "dev" else None,
        redoc_url="/redoc" if settings.app_env == "dev" else None,
        openapi_url="/openapi.json" if settings.app_env == "dev" else None,
        lifespan=_lifespan,
    )

    # Error -> HTTP mapping. Must run before any route registration so the
    # handlers are in place when the first request arrives.
    register_exception_handlers(app)

    # CORS. Permissive in dev (the future Next.js frontend runs on a
    # different port); locked down outside dev. Credentials are disabled so
    # that a wildcard origin is well-formed per the CORS spec.
    if settings.app_env == "dev":
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=False,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # Versioned API surface.
    app.include_router(api_router, prefix=settings.api_v1_prefix)

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        """Return a tiny landing payload so hitting the host is not a 404."""
        return {"service": settings.app_name, "docs": "/docs"}

    @app.get("/health", tags=["meta"], summary="Liveness probe")
    async def health() -> dict[str, str]:
        """Return service health metadata."""
        return {
            "status": "ok",
            "app": settings.app_name,
            "env": settings.app_env,
            "version": __version__,
        }

    return app

# Module-level instance for uvicorn. Do not import this in tests -- call
# create_app() instead so overrides are scoped per test.
app = create_app()
