"""Chat orchestration service.

Wraps the compiled LangGraph agent behind a service-layer interface so
that the API routes stay thin and the graph stays testable.

The service is the ONLY place that:

    - constructs the initial AgentState from a user message,
    - derives or echoes the conversation id,
    - extracts the final reply from the graph output,
    - curates the structured ``recommended_products`` list,
    - shapes the response envelope for the API layer,
    - exposes streaming variants for SSE and voice clients.

Error handling policy:

    The graph catches ``DarazScraperError`` inside its tool nodes and
    records the failure in ``AgentState.error`` rather than propagating
    it. This service therefore never sees a typed scraper exception --
    if the fetch failed, the reason arrives on the ``error`` field of the
    final state and is forwarded to the API response unchanged.

    Unhandled exceptions from the LLM (bad API key, network failure)
    remain the caller's concern and propagate up to FastAPI's global
    exception handlers.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Sequence
from typing import Any
from uuid import uuid4

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from daraz_ai_shopping_assistant.agents.graph import get_compiled_graph
from daraz_ai_shopping_assistant.agents.state import AgentState, ParsedIntent
from daraz_ai_shopping_assistant.core.logging import get_logger
from daraz_ai_shopping_assistant.schemas.chat import ChatResponse

logger = get_logger(__name__)

#: Maximum number of products included in ``recommended_products``.
#: Matches the instruction in ``_RESPONSE_SYSTEM_PROMPT``: the LLM is told
#: to present "the most relevant ones ... do not list more than 5 products".
_RECOMMENDED_LIMIT: int = 5

class ChatService:
    """Route a user message through the LangGraph agent.

    Attributes:
        _graph: The compiled graph. Injected in tests to avoid LLM calls.
    """

    def __init__(self, graph: Any | None = None) -> None:
        """Initialise the service.

        Args:
            graph: Optional compiled graph to use. When ``None``, the
                process-wide default from ``get_compiled_graph()`` is used.
        """
        self._graph: Any | None = graph

    # ------------------------------------------------------------------ #
    # Non-streaming chat
    # ------------------------------------------------------------------ #
    async def chat(
        self,
        message: str,
        conversation_id: str | None = None,
    ) -> ChatResponse:
        """Process a user message and return the assistant's response.

        Args:
            message: The user's message.
            conversation_id: Optional conversation identifier. When
                ``None`` or blank, a new UUID is generated and returned.

        Returns:
            A fully-populated ChatResponse. When a tool inside the graph
            failed, the failure is reported on ``ChatResponse.error``; the
            call itself does not raise for tool failures.
        """
        graph = self._graph if self._graph is not None else get_compiled_graph()
        resolved_id = _resolve_conversation_id(conversation_id)

        started_at = time.monotonic()
        logger.info(
            "CHAT_STARTED",
            extra={
                "ctx": {
                    "message_length": len(message),
                    "conversation_id": resolved_id,
                    "streaming": False,
                    "mode": "text",
                }
            },
        )

        initial_state: AgentState = {
            "messages": [HumanMessage(content=message)],
            "intent": None,
            "tool_result": None,
            "error": None,
            "mode": "text",
        }
        config = _thread_config(resolved_id)

        final_state = await graph.ainvoke(initial_state, config=config)

        reply = _extract_reply(final_state)
        intent = final_state.get("intent")
        tool_result = final_state.get("tool_result")
        error = final_state.get("error")
        recommended = _curate_recommended_products(intent, tool_result)

        duration_ms = int((time.monotonic() - started_at) * 1000)
        logger.info(
            "CHAT_COMPLETED",
            extra={
                "ctx": {
                    "conversation_id": resolved_id,
                    "intent": intent.intent.value if intent else None,
                    "has_data": tool_result is not None,
                    "recommended_count": len(recommended),
                    "has_error": error is not None,
                    "duration_ms": duration_ms,
                    "streaming": False,
                }
            },
        )

        return ChatResponse(
            reply=reply,
            conversation_id=resolved_id,
            intent=intent.intent.value if intent else None,
            recommended_products=recommended,
            data=tool_result,
            error=error,
        )

    # ------------------------------------------------------------------ #
    # SSE streaming chat
    # ------------------------------------------------------------------ #
    async def chat_stream(
        self,
        message: str,
        conversation_id: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Stream a chat response token-by-token over SSE.

        Yields event dicts suitable for JSON-serialising straight into an
        SSE frame. The consumer (``api/chat.py``) is responsible for the
        ``data: ...`` framing and the terminal ``[DONE]`` sentinel.

        Event shapes:

            {"type": "token", "text": "..."} -- one LLM token.
            {"type": "done", "conversation_id": "...", "intent": "...",
             "recommended_products": [...], "error": null | "..."} -- end.

        Only tokens produced by the ``respond`` node are emitted. The
        ``parse_intent`` node also calls the LLM, but its output is a
        structured object that must not be streamed to the client.

        Args:
            message: The user's message.
            conversation_id: Optional conversation identifier. When
                ``None`` or blank, a new UUID is generated.

        Yields:
            Dicts, one per streamed token and one terminal ``done`` event.
        """
        graph = self._graph if self._graph is not None else get_compiled_graph()
        resolved_id = _resolve_conversation_id(conversation_id)

        started_at = time.monotonic()
        logger.info(
            "CHAT_STARTED",
            extra={
                "ctx": {
                    "message_length": len(message),
                    "conversation_id": resolved_id,
                    "streaming": True,
                    "mode": "text",
                }
            },
        )

        initial_state: AgentState = {
            "messages": [HumanMessage(content=message)],
            "intent": None,
            "tool_result": None,
            "error": None,
            "mode": "text",
        }
        config = _thread_config(resolved_id)

        token_count = 0
        async for event in graph.astream_events(
            initial_state, config=config, version="v2"
        ):
            if event.get("event") != "on_chat_model_stream":
                continue
            metadata = event.get("metadata") or {}
            if metadata.get("langgraph_node") != "respond":
                continue
            chunk = event.get("data", {}).get("chunk")
            text = _chunk_text(chunk)
            if not text:
                continue
            token_count += 1
            yield {"type": "token", "text": text}

        final_values: dict[str, Any] = {}
        try:
            snapshot = await graph.aget_state(config)
            final_values = dict(snapshot.values) if snapshot is not None else {}
        except Exception:
            logger.exception(
                "CHAT_STREAM_STATE_LOOKUP_FAILED",
                extra={"ctx": {"conversation_id": resolved_id}},
            )

        intent_obj = final_values.get("intent")
        tool_result = final_values.get("tool_result")
        error_value = final_values.get("error")
        recommended = _curate_recommended_products(intent_obj, tool_result)

        duration_ms = int((time.monotonic() - started_at) * 1000)
        logger.info(
            "CHAT_COMPLETED",
            extra={
                "ctx": {
                    "conversation_id": resolved_id,
                    "intent": intent_obj.intent.value if intent_obj else None,
                    "recommended_count": len(recommended),
                    "has_error": error_value is not None,
                    "token_count": token_count,
                    "duration_ms": duration_ms,
                    "streaming": True,
                }
            },
        )

        yield {
            "type": "done",
            "conversation_id": resolved_id,
            "intent": intent_obj.intent.value if intent_obj else None,
            "recommended_products": recommended,
            "error": error_value,
        }

    # ------------------------------------------------------------------ #
    # Voice streaming chat
    # ------------------------------------------------------------------ #
    async def chat_voice_stream(
        self,
        message: str,
        conversation_id: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Stream a chat turn with voice-friendly lifecycle events.

        Unlike :meth:`chat_stream`, this emits intermediate events so the
        voice session can play a filler phrase the moment intent is
        known, before the slow Firecrawl scrape completes.

        Event shapes:

            {"type": "intent_parsed", "conversation_id": "...",
             "intent": "search", "query": "..." | None}
                Emitted as soon as the intent node finishes. The voice
                session uses this to play a pre-cached filler.

            {"type": "tool_started", "tool": "search"}
            {"type": "tool_done", "tool": "search"}
                Fired when a tool node starts and finishes. Useful for
                UI status indicators.

            {"type": "token", "text": "..."}
                One LLM token from the ``respond`` node. Voice-only;
                never contains markdown because the voice prompt forbids
                it.

            {"type": "done", "conversation_id": "...", "intent": "...",
             "recommended_products": [...], "error": null | "..."}
                Terminal event.

        Args:
            message: The user's message (already transcribed).
            conversation_id: Optional conversation identifier.

        Yields:
            Dicts, one per lifecycle event, ending with ``done``.
        """
        graph = self._graph if self._graph is not None else get_compiled_graph()
        resolved_id = _resolve_conversation_id(conversation_id)

        started_at = time.monotonic()
        logger.info(
            "VOICE_CHAT_STARTED",
            extra={
                "ctx": {
                    "message_length": len(message),
                    "conversation_id": resolved_id,
                }
            },
        )

        initial_state: AgentState = {
            "messages": [HumanMessage(content=message)],
            "intent": None,
            "tool_result": None,
            "error": None,
            "mode": "voice",
        }
        config = _thread_config(resolved_id)

        token_count = 0
        intent_emitted = False

        async for event in graph.astream_events(
            initial_state, config=config, version="v2"
        ):
            kind = event.get("event")
            node = (event.get("metadata") or {}).get("langgraph_node")

            # Intent ready -> caller plays a filler immediately.
            if kind == "on_chain_end" and node == "parse_intent":
                if not intent_emitted:
                    output = event.get("data", {}).get("output") or {}
                    intent_obj = _coerce_parsed_intent(
                        output
                        if isinstance(output, ParsedIntent)
                        else output.get("intent")
                    )
                    if intent_obj is not None:
                        intent_emitted = True
                        yield {
                            "type": "intent_parsed",
                            "conversation_id": resolved_id,
                            "intent": intent_obj.intent.value,
                            "query": intent_obj.query,
                        }
                continue

            # Tool boundaries -> UI status indicators, second filler hooks.
            if kind == "on_chain_start" and node in (
                "search",
                "get_product",
            ):
                yield {"type": "tool_started", "tool": node}
                continue
            if kind == "on_chain_end" and node in (
                "search",
                "get_product",
            ):
                yield {"type": "tool_done", "tool": node}
                continue

            # Respond tokens -> sentence chunker -> TTS.
            if kind == "on_chat_model_stream" and node == "respond":
                chunk = event.get("data", {}).get("chunk")
                text = _chunk_text(chunk)
                if text:
                    token_count += 1
                    yield {"type": "token", "text": text}
                continue

        final_values: dict[str, Any] = {}
        try:
            snapshot = await graph.aget_state(config)
            final_values = dict(snapshot.values) if snapshot is not None else {}
        except Exception:
            logger.exception(
                "VOICE_CHAT_STATE_LOOKUP_FAILED",
                extra={"ctx": {"conversation_id": resolved_id}},
            )

        intent_obj = _coerce_parsed_intent(final_values.get("intent"))
        tool_result = final_values.get("tool_result")
        error_value = final_values.get("error")
        recommended = _curate_recommended_products(intent_obj, tool_result)

        duration_ms = int((time.monotonic() - started_at) * 1000)
        logger.info(
            "VOICE_CHAT_COMPLETED",
            extra={
                "ctx": {
                    "conversation_id": resolved_id,
                    "intent": intent_obj.intent.value if intent_obj else None,
                    "recommended_count": len(recommended),
                    "has_error": error_value is not None,
                    "token_count": token_count,
                    "duration_ms": duration_ms,
                }
            },
        )

        yield {
            "type": "done",
            "conversation_id": resolved_id,
            "intent": intent_obj.intent.value if intent_obj else None,
            "recommended_products": recommended,
            "error": error_value,
        }

# ---------------------------------------------------------------------- #
# Helpers
# ---------------------------------------------------------------------- #
def _coerce_parsed_intent(value: Any) -> ParsedIntent | None:
    """Convert a checkpoint or event value into a parsed intent model."""
    if value is None:
        return None
    if isinstance(value, ParsedIntent):
        return value
    return ParsedIntent.model_validate(value)

def _resolve_conversation_id(conversation_id: str | None) -> str:
    """Return a usable conversation identifier.

    Args:
        conversation_id: Client-supplied identifier, or ``None``.

    Returns:
        A non-empty string: the client's value when it is non-blank, a
        freshly generated UUID otherwise.
    """
    if isinstance(conversation_id, str):
        stripped = conversation_id.strip()
        if stripped:
            return stripped
    return str(uuid4())

def _thread_config(conversation_id: str) -> dict[str, Any]:
    """Build the LangGraph config carrying the thread id.

    Args:
        conversation_id: The resolved conversation identifier.

    Returns:
        A config dict of the shape LangGraph's checkpointer expects.
    """
    return {"configurable": {"thread_id": conversation_id}}

def _curate_recommended_products(
    intent: Any,
    tool_result: dict[str, Any] | None,
    *,
    limit: int = _RECOMMENDED_LIMIT,
) -> list[dict[str, Any]]:
    """Return the structured list of products the assistant recommends.

    The LLM's prompt instructs it to present the top N products from the
    tool result. Extracting WHICH products it mentioned would require a
    second LLM call or fragile parsing of its prose. Instead we mirror the
    window it was told to work from: the first ``limit`` products.

    Args:
        intent: The ParsedIntent stored on AgentState, or ``None``.
        tool_result: The tool result dict, or ``None``.
        limit: Maximum number of products to return.

    Returns:
        A list of product dicts (possibly empty). Each dict is a
        Pydantic-validated product as serialised by the service layer.
    """
    if intent is None or tool_result is None:
        return []

    intent_kind = getattr(intent, "intent", None)
    intent_value = getattr(intent_kind, "value", intent_kind)

    if intent_value == "search":
        products = tool_result.get("products") or []
        return [p for p in products[:limit] if isinstance(p, dict)]

    if intent_value == "get_product":
        if isinstance(tool_result, dict) and tool_result.get("id"):
            return [tool_result]
        return []

    return []

def _chunk_text(chunk: Any) -> str:
    """Extract the text from an ``on_chat_model_stream`` chunk.

    Chunk content varies by provider: a plain string, a list of content
    blocks, or a message object whose ``content`` holds either.

    Args:
        chunk: The ``data.chunk`` value from the stream event.

    Returns:
        The token text, or an empty string when no text is present.
    """
    if chunk is None:
        return ""
    content: Any = getattr(chunk, "content", chunk)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "".join(parts)
    if isinstance(content, dict):
        text = content.get("text")
        return str(text) if text is not None else ""
    return ""

def _extract_reply(state: AgentState) -> str:
    """Return the last AIMessage's text from the final state.

    The local variable is typed ``Sequence`` so that LangGraph's
    ``list[AnyMessage]`` can be assigned without a cast -- ``list`` is
    invariant, ``Sequence`` is covariant.

    Args:
        state: The graph's final state.

    Returns:
        The assistant reply, or a generic fallback when no AIMessage was
        produced (should not happen in practice, but the endpoint must
        never return an empty body).
    """
    messages: Sequence[BaseMessage] = state.get("messages") or []
    for message in reversed(messages):
        if isinstance(message, AIMessage):
            content = message.content
            if isinstance(content, str) and content.strip():
                return content.strip()
            if isinstance(content, list):
                parts: list[str] = []
                for block in content:
                    if isinstance(block, str):
                        parts.append(block)
                    elif isinstance(block, dict) and block.get("type") == "text":
                        parts.append(str(block.get("text", "")))
                joined = "".join(parts).strip()
                if joined:
                    return joined
    return "I could not produce a response. Please try again."

# ---------------------------------------------------------------------- #
# Module-level convenience wrapper
# ---------------------------------------------------------------------- #
_default_service: ChatService | None = None

def get_chat_service() -> ChatService:
    """Return a lazily-constructed, process-wide ChatService.

    Returns:
        The shared ChatService instance.
    """
    global _default_service
    if _default_service is None:
        _default_service = ChatService()
    return _default_service

__all__ = ["ChatService", "get_chat_service"]
