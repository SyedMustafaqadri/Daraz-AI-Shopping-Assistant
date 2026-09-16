"""Chat orchestration service.

Wraps the compiled LangGraph agent behind a service-layer interface so
that the API route stays thin and the graph stays testable.

The service is the ONLY place that:

    - constructs the initial AgentState from a user message,
    - extracts the final reply from the graph output,
    - shapes the response envelope for the API layer.

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

from langchain_core.messages import AIMessage, HumanMessage

from daraz_ai_shopping_assistant.agents.graph import get_compiled_graph
from daraz_ai_shopping_assistant.agents.state import AgentState
from daraz_ai_shopping_assistant.core.logging import get_logger
from daraz_ai_shopping_assistant.schemas.chat import ChatResponse

logger = get_logger(__name__)

class ChatService:
    """Route a user message through the LangGraph agent.

    Attributes:
        _graph: The compiled graph. Injected in tests to avoid LLM calls.
    """

    def __init__(self, graph: object | None = None) -> None:
        """Initialise the service.

        Args:
            graph: Optional compiled graph to use. When ``None``, the
                process-wide default from ``get_compiled_graph()`` is used.
        """
        self._graph = graph

    async def chat(self, message: str) -> ChatResponse:
        """Process a user message and return the assistant's response.

        Args:
            message: The user's message.

        Returns:
            A fully-populated ChatResponse. When a tool inside the graph
            failed, the failure is reported on ``ChatResponse.error``; the
            call itself does not raise for tool failures.
        """
        graph = self._graph if self._graph is not None else get_compiled_graph()

        started_at = time.monotonic()
        logger.info(
            "CHAT_STARTED",
            extra={"ctx": {"message_length": len(message)}},
        )

        initial_state: AgentState = {
            "messages": [HumanMessage(content=message)],
            "intent": None,
            "tool_result": None,
            "error": None,
        }

        final_state = await graph.ainvoke(initial_state)

        reply = _extract_reply(final_state)
        intent = final_state.get("intent")
        tool_result = final_state.get("tool_result")
        error = final_state.get("error")

        duration_ms = int((time.monotonic() - started_at) * 1000)
        logger.info(
            "CHAT_COMPLETED",
            extra={
                "ctx": {
                    "intent": intent.intent.value if intent else None,
                    "has_data": tool_result is not None,
                    "has_error": error is not None,
                    "duration_ms": duration_ms,
                }
            },
        )

        return ChatResponse(
            reply=reply,
            intent=intent.intent.value if intent else None,
            data=tool_result,
            error=error,
        )

def _extract_reply(state: AgentState) -> str:
    """Return the last AIMessage's text from the final state.

    Args:
        state: The graph's final state.

    Returns:
        The assistant reply, or a generic fallback when no AIMessage was
        produced (should not happen in practice, but the endpoint must
        never return an empty body).
    """
    messages = state.get("messages") or []
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
