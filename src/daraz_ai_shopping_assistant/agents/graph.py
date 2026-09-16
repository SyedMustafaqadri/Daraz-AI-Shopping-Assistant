"""LangGraph state machine for the chat pipeline.

The graph has four nodes:

    - ``parse_intent``   -- LLM classifies the user's message and extracts
                            parameters. Uses structured output so the
                            result is a typed ParsedIntent, not free text.
    - ``search``         -- calls search_products_tool.
    - ``get_product``    -- calls get_product_tool.
    - ``get_recommendations`` -- calls get_recommendations_tool.
    - ``respond``        -- LLM writes a plain-language reply using the
                            original question and whatever the tool
                            returned.

Conditional edges route from ``parse_intent`` to the appropriate tool (or
directly to ``respond`` for small-talk). All tool nodes converge on
``respond``, which produces the final assistant message.

The LLM never sees raw Daraz HTML. It sees the user's message, the parsed
intent, and a JSON dump of the tool result -- all of which are already
validated structures. It cannot invent product data because the data comes
from the tools, and the response node is instructed to work only from that.
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
)
from langgraph.graph import END, START, StateGraph

from daraz_ai_shopping_assistant.agents.state import (
    AgentState,
    IntentType,
    ParsedIntent,
)
from daraz_ai_shopping_assistant.agents.tools import (
    get_product_tool,
    get_recommendations_tool,
    search_products_tool,
)
from daraz_ai_shopping_assistant.core.config import settings
from daraz_ai_shopping_assistant.core.exceptions import DarazScraperError
from daraz_ai_shopping_assistant.core.logging import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------- #
# Prompt templates
# ---------------------------------------------------------------------- #
_INTENT_SYSTEM_PROMPT: str = (
    "You are the routing brain of a shopping assistant for Daraz.pk. "
    "Given the user's message, decide which action to take and extract "
    "the parameters needed for that action.\n"
    "\n"
    "Intents:\n"
    "- search: the user wants to find products matching a description. "
    "Extract: query (string), min_price (float, optional), "
    "max_price (float, optional), page (int, default 1).\n"
    "- get_product: the user asked for details of a specific product and "
    "provided its id. Extract: product_id (must be 'i' followed by "
    "digits, e.g. 'i927677133').\n"
    "- get_recommendations: the user asked for products similar to a "
    "specific product and provided its id. Extract: product_id.\n"
    "- small_talk: greetings, thanks, chitchat, or anything that does not "
    "fit the above. No parameters.\n"
    "\n"
    "Rules:\n"
    "- Prices are in PKR. 'under 5000' means max_price=5000.\n"
    "- Never invent a product_id. If the user says 'show me the first "
    "one' without an explicit id, classify as small_talk.\n"
    "- If the user's message is empty or unintelligible, classify as "
    "small_talk.\n"
    "- Return only the structured object matching the schema.\n"
)

_RESPONSE_SYSTEM_PROMPT: str = (
    "You are a helpful shopping assistant for Daraz.pk. Write a concise "
    "reply to the user's question using only the data provided below.\n"
    "\n"
    "Rules:\n"
    "- Never invent product details, prices, ratings, or URLs. If the "
    "data does not contain an answer, say so plainly.\n"
    "- If the user asked for products, present the most relevant ones as "
    "a short bulleted list showing title, price in PKR, and rating when "
    "available. Do not list more than 5 products.\n"
    "- If an error occurred, apologise briefly and describe what went "
    "wrong in plain language.\n"
    "- Prices are in PKR. Format them as 'Rs. 1,234'.\n"
    "- Keep the reply under 200 words.\n"
)

#: Maximum number of characters of a tool result to include in the
#: response-generation prompt. Tool results can contain 40+ products;
#: dumping all of them wastes tokens and slows the LLM without improving
#: the reply. The LLM only needs enough context to summarise.
_MAX_TOOL_RESULT_CHARS: int = 8000

# ---------------------------------------------------------------------- #
# LLM construction
# ---------------------------------------------------------------------- #
def _default_llm() -> BaseChatModel:
    """Build the default chat model from settings.

    Returns:
        A configured ``ChatGoogleGenerativeAI`` instance.

    Raises:
        RuntimeError: When ``GOOGLE_API_KEY`` is not configured. Raised
            with a clear message rather than the SDK's generic auth error.
    """
    if not settings.google_api_key:
        raise RuntimeError(
            "GOOGLE_API_KEY is not configured. The chat endpoint requires "
            "a Google API key to reach the LLM. Add it to your .env file."
        )
    # Import deferred so that the module can be imported even when the
    # optional LLM dependencies are not installed (e.g. minimal test runs).
    from langchain_google_genai import ChatGoogleGenerativeAI

    return ChatGoogleGenerativeAI(
        model=settings.llm_model,
        google_api_key=settings.google_api_key,
        temperature=settings.llm_temperature,
    )

# ---------------------------------------------------------------------- #
# Message helpers
# ---------------------------------------------------------------------- #
def _content_to_str(message: BaseMessage) -> str:
    """Return a message's text content as a plain string.

    LangChain's ``BaseMessage.content`` may be a string, a list of
    content blocks, or a provider-specific object. This normalises the
    common shapes used by the SDK.

    Args:
        message: The message whose content to extract.

    Returns:
        The text content, or an empty string when no text is present.
    """
    content: Any = message.content
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

def _last_human_message(state: AgentState) -> str:
    """Return the text of the most recent human message in the state.

    Args:
        state: The graph state.

    Returns:
        The user's message text, or an empty string if none is present.
    """
    for message in reversed(state["messages"]):
        if isinstance(message, HumanMessage):
            return _content_to_str(message)
    return ""

# ---------------------------------------------------------------------- #
# Graph construction
# ---------------------------------------------------------------------- #
def build_graph(llm: BaseChatModel | None = None) -> Any:
    """Build and compile the agent graph.

    Args:
        llm: Optional chat model to use. When ``None``, ``_default_llm()``
            is called. Tests inject a mock here so no LLM call is made.

    Returns:
        A compiled LangGraph ``StateGraph`` ready for ``ainvoke``.
    """
    resolved_llm: BaseChatModel = llm if llm is not None else _default_llm()

    # ------------------------------------------------------------------ #
    # Nodes
    # ------------------------------------------------------------------ #
    async def _parse_intent_node(state: AgentState) -> dict[str, Any]:
        """Classify the user's message into a ParsedIntent."""
        user_text = _last_human_message(state)
        structured_llm = resolved_llm.with_structured_output(ParsedIntent)
        try:
            intent = await structured_llm.ainvoke(
                [
                    SystemMessage(content=_INTENT_SYSTEM_PROMPT),
                    HumanMessage(content=user_text),
                ]
            )
            if not isinstance(intent, ParsedIntent):
                intent = ParsedIntent.model_validate(intent)
        except Exception as exc:
            logger.warning(
                "AGENT_INTENT_PARSE_FAILED",
                extra={"ctx": {"error": type(exc).__name__}},
            )
            intent = ParsedIntent(intent=IntentType.SMALL_TALK)
        logger.info(
            "AGENT_INTENT_PARSED",
            extra={"ctx": {"intent": intent.intent.value}},
        )
        return {"intent": intent}

    async def _search_node(state: AgentState) -> dict[str, Any]:
        """Execute the search tool and record the result."""
        intent = state["intent"]
        if intent is None or not intent.query:
            return {"error": "Search query was not provided."}
        try:
            result = await search_products_tool(
                intent.query,
                min_price=intent.min_price,
                max_price=intent.max_price,
                page=intent.page,
            )
            return {"tool_result": result}
        except DarazScraperError as exc:
            logger.warning(
                "AGENT_TOOL_FAILED",
                extra={"ctx": {"tool": "search", "error": exc.message}},
            )
            return {"error": exc.message}

    async def _get_product_node(state: AgentState) -> dict[str, Any]:
        """Execute the product-detail tool and record the result."""
        intent = state["intent"]
        if intent is None or not intent.product_id:
            return {"error": "Product ID was not provided."}
        try:
            result = await get_product_tool(intent.product_id)
            return {"tool_result": result}
        except DarazScraperError as exc:
            logger.warning(
                "AGENT_TOOL_FAILED",
                extra={"ctx": {"tool": "get_product", "error": exc.message}},
            )
            return {"error": exc.message}

    async def _get_recommendations_node(state: AgentState) -> dict[str, Any]:
        """Execute the recommendations tool and record the result."""
        intent = state["intent"]
        if intent is None or not intent.product_id:
            return {"error": "Product ID was not provided."}
        try:
            result = await get_recommendations_tool(intent.product_id)
            return {"tool_result": result}
        except DarazScraperError as exc:
            logger.warning(
                "AGENT_TOOL_FAILED",
                extra={"ctx": {"tool": "get_recommendations", "error": exc.message}},
            )
            return {"error": exc.message}

    async def _respond_node(state: AgentState) -> dict[str, Any]:
        """Generate the final assistant reply using the tool result."""
        user_text = _last_human_message(state)
        intent = state.get("intent")
        tool_result = state.get("tool_result")
        error = state.get("error")

        context_parts: list[str] = []
        if intent is not None:
            context_parts.append(f"Intent: {intent.intent.value}")
        if error:
            context_parts.append(f"Error: {error}")
        if tool_result is not None:
            serialised = json.dumps(tool_result, default=str, ensure_ascii=False)
            if len(serialised) > _MAX_TOOL_RESULT_CHARS:
                serialised = serialised[:_MAX_TOOL_RESULT_CHARS] + "... [truncated]"
            context_parts.append(f"Data:\n{serialised}")
        if not context_parts:
            context_parts.append("No data available.")

        prompt = [
            SystemMessage(content=_RESPONSE_SYSTEM_PROMPT),
            HumanMessage(
                content=(
                    f"User asked: {user_text}\n\n"
                    f"Context:\n" + "\n\n".join(context_parts) + "\n\n"
                    "Write the reply."
                )
            ),
        ]

        try:
            response = await resolved_llm.ainvoke(prompt)
        except Exception:
            logger.exception("AGENT_RESPONSE_FAILED")
            return {
                "messages": [
                    AIMessage(
                        content=(
                            "Sorry, I could not generate a response right "
                            "now. Please try again in a moment."
                        )
                    )
                ]
            }
        return {"messages": [response]}

    # ------------------------------------------------------------------ #
    # Routing
    # ------------------------------------------------------------------ #
    def _route_by_intent(state: AgentState) -> str:
        """Return the name of the next node based on the parsed intent.

        Args:
            state: The current graph state.

        Returns:
            One of the tool node names, or ``"respond"`` for small-talk.
        """
        intent = state.get("intent")
        if intent is None:
            return "respond"
        mapping = {
            IntentType.SEARCH: "search",
            IntentType.GET_PRODUCT: "get_product",
            IntentType.GET_RECOMMENDATIONS: "get_recommendations",
            IntentType.SMALL_TALK: "respond",
        }
        return mapping.get(intent.intent, "respond")

    # ------------------------------------------------------------------ #
    # Assembly
    # ------------------------------------------------------------------ #
    builder = StateGraph(AgentState)
    builder.add_node("parse_intent", _parse_intent_node)
    builder.add_node("search", _search_node)
    builder.add_node("get_product", _get_product_node)
    builder.add_node("get_recommendations", _get_recommendations_node)
    builder.add_node("respond", _respond_node)

    builder.add_edge(START, "parse_intent")
    builder.add_conditional_edges(
        "parse_intent",
        _route_by_intent,
        {
            "search": "search",
            "get_product": "get_product",
            "get_recommendations": "get_recommendations",
            "respond": "respond",
        },
    )
    builder.add_edge("search", "respond")
    builder.add_edge("get_product", "respond")
    builder.add_edge("get_recommendations", "respond")
    builder.add_edge("respond", END)

    return builder.compile()

# ---------------------------------------------------------------------- #
# Process-wide compiled graph
# ---------------------------------------------------------------------- #
_compiled_graph: Any | None = None

def get_compiled_graph() -> Any:
    """Return a lazily-constructed, process-wide compiled graph.

    Building the graph is cheap; constructing the LLM client is not. The
    cache means the FastAPI process only pays that cost once.

    Returns:
        The shared compiled graph.
    """
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_graph()
    return _compiled_graph

__all__ = ["build_graph", "get_compiled_graph"]
