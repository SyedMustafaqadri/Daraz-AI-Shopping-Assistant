"""LangGraph state machine for the chat pipeline.

The graph has four nodes:

    - ``parse_intent``   -- LLM classifies the user's message and extracts
                            parameters. Uses structured output so the
                            result is a typed ParsedIntent, not free text.
    - ``search``         -- calls search_products_tool.
    - ``get_product``    -- calls get_product_tool.
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

Response modes:

    ``respond`` selects between two system prompts based on
    ``state["mode"]``:

    - ``"text"`` (default): markdown bullets, clickable product URLs,
      prices as ``Rs. 1,234``.
    - ``"voice"``: plain conversational speech for text-to-speech. No
      markdown, no URLs, prices as words.

Conversation memory:

    When a checkpointer is passed to :func:`build_graph`, the compiled
    graph persists conversation state per ``thread_id``. Both LLM nodes
    trim the message history to the last ``_MAX_CONTEXT_MESSAGES`` before
    calling the model, so a long conversation does not grow unbounded.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    trim_messages,
)
from langgraph.graph import END, START, StateGraph

from daraz_ai_shopping_assistant.agents.state import (
    AgentState,
    IntentType,
    ParsedIntent,
)
from daraz_ai_shopping_assistant.agents.tools import (
    get_product_tool,
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
    "- When the user asked for products (or you are recommending products), "
    "present the most relevant ones as a short bulleted list. Each bullet "
    "MUST include the product's full URL as a markdown link so the user "
    "can click through. Use this exact format:\n"
    "    - [Product Title](https://www.daraz.pk/products/...html) - Rs. 1,234\n"
    "  The URL comes from the product's `url` field in the data. Never "
    "shorten it, never invent it, never write it as bare text. If a "
    "product has no `url` field, skip that product rather than emit a "
    "bullet without a link.\n"
    "- For rating: only include 'Rating: X.X' when the data has a non-null "
    "`rating` field. Do not print 'Rating: null'.\n"
    "- Do not list more than 5 products.\n"
    "- If an error occurred, apologise briefly and describe what went "
    "wrong in plain language.\n"
    "- Prices are in PKR. Format them as 'Rs. 1,234'.\n"
    "- Keep the reply under 200 words.\n"
)

_RESPONSE_SYSTEM_PROMPT_VOICE: str = (
    "You are a voice shopping assistant for Daraz.pk. Your reply will be "
    "SPOKEN ALOUD to the user. Write plain conversational speech.\n"
    "\n"
    "Rules:\n"
    "- No markdown, no bullets, no numbered lists, no links, no URLs -- "
    "never.\n"
    "- Say prices as words: 'twelve hundred rupees' or 'one thousand, "
    "two hundred rupees'. Never 'Rs.' and never 'PKR'.\n"
    "- Mention at most three products. For each, say the short title "
    "followed by the price. Do not read model numbers or SKUs.\n"
    "- Keep the whole reply under sixty words.\n"
    "- End with a brief natural follow-up question when it fits, such as "
    "'Want me to show you more?'\n"
    "- If the tool returned an error, apologise in one short sentence and "
    "suggest trying again.\n"
    "- Never invent data. If a field is missing, simply omit it.\n"
)

#: Maximum number of characters of a tool result to include in the
#: response-generation prompt. Tool results can contain 40+ products;
#: dumping all of them wastes tokens and slows the LLM without improving
#: the reply. The LLM only needs enough context to summarise.
_MAX_TOOL_RESULT_CHARS: int = 8000

#: Maximum number of messages fed to either LLM call. Each turn
#: contributes one HumanMessage and one AIMessage, so 16 messages equals
#: the last eight turns. History is still stored in full by the
#: checkpointer; only the window sent to the model is capped.
_MAX_CONTEXT_MESSAGES: int = 16

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

def _trim_for_llm(messages: Sequence[BaseMessage]) -> list[BaseMessage]:
    """Return the trailing window of ``messages`` for the LLM.

    Keeps the last ``_MAX_CONTEXT_MESSAGES`` messages, starting on a
    HumanMessage so the model sees a clean turn boundary. System messages
    are not included -- the caller adds its own.

    The parameter is typed as ``Sequence`` rather than ``list`` so that
    callers can pass LangGraph's ``list[AnyMessage]`` without a cast --
    ``list`` is invariant, ``Sequence`` is covariant.

    Args:
        messages: The full conversation from ``AgentState.messages``.

    Returns:
        A trimmed list of Human/AI messages.
    """
    if not messages:
        return []
    trimmed = trim_messages(
        list(messages),
        max_tokens=_MAX_CONTEXT_MESSAGES,
        strategy="last",
        token_counter=len,
        start_on="human",
        include_system=False,
        allow_partial=False,
    )
    return list(trimmed)

def _select_response_prompt(mode: str) -> str:
    """Return the system prompt for the given response mode.

    Args:
        mode: ``"text"`` or ``"voice"``. Anything else is treated as
            ``"text"``.

    Returns:
        The system prompt string.
    """
    if mode == "voice":
        return _RESPONSE_SYSTEM_PROMPT_VOICE
    return _RESPONSE_SYSTEM_PROMPT

# ---------------------------------------------------------------------- #
# Graph construction
# ---------------------------------------------------------------------- #
def build_graph(
    llm: BaseChatModel | None = None,
    *,
    checkpointer: Any | None = None,
) -> Any:
    """Build and compile the agent graph.

    Args:
        llm: Optional chat model to use. When ``None``, ``_default_llm()``
            is called. Tests inject a mock here so no LLM call is made.
        checkpointer: Optional LangGraph checkpointer. When provided, the
            compiled graph persists state per ``thread_id`` so successive
            turns in the same conversation see the prior history.

    Returns:
        A compiled LangGraph ``StateGraph`` ready for ``ainvoke`` or
        ``astream_events``.
    """
    resolved_llm: BaseChatModel = llm if llm is not None else _default_llm()

    # ------------------------------------------------------------------ #
    # Nodes
    # ------------------------------------------------------------------ #
    async def _parse_intent_node(state: AgentState) -> dict[str, Any]:
        """Classify the user's message into a ParsedIntent."""
        trimmed = _trim_for_llm(state["messages"])
        structured_llm = resolved_llm.with_structured_output(ParsedIntent)
        try:
            intent = await structured_llm.ainvoke(
                [SystemMessage(content=_INTENT_SYSTEM_PROMPT), *trimmed]
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

    async def _respond_node(state: AgentState) -> dict[str, Any]:
        """Generate the final assistant reply using the tool result."""
        intent = state.get("intent")
        tool_result = state.get("tool_result")
        error = state.get("error")
        mode = state.get("mode", "text") or "text"

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

        # Prior conversation is passed to the LLM as context so follow-up
        # turns ("tell me more about the second one") are intelligible.
        # The most recent HumanMessage is the user's current question --
        # the LLM sees it in the trimmed history, so it is not repeated.
        trimmed = _trim_for_llm(state["messages"])

        prompt = [
            SystemMessage(content=_select_response_prompt(mode)),
            *trimmed,
            HumanMessage(
                content=(
                    "Context for the reply (not visible to the user):\n\n"
                    + "\n\n".join(context_parts) + "\n\n"
                    "Write the reply now."
                )
            ),
        ]

        try:
            response = await resolved_llm.ainvoke(prompt)
        except Exception:
            logger.exception("AGENT_RESPONSE_FAILED")
            fallback = (
                "Sorry, something went wrong. Please try again."
                if mode == "voice"
                else (
                    "Sorry, I could not generate a response right now. "
                    "Please try again in a moment."
                )
            )
            return {"messages": [AIMessage(content=fallback)]}
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
    builder.add_node("respond", _respond_node)

    builder.add_edge(START, "parse_intent")
    builder.add_conditional_edges(
        "parse_intent",
        _route_by_intent,
        {
            "search": "search",
            "get_product": "get_product",
            "respond": "respond",
        },
    )
    builder.add_edge("search", "respond")
    builder.add_edge("get_product", "respond")
    builder.add_edge("respond", END)

    if checkpointer is not None:
        return builder.compile(checkpointer=checkpointer)
    return builder.compile()

# ---------------------------------------------------------------------- #
# Process-wide compiled graph
# ---------------------------------------------------------------------- #
_compiled_graph: Any | None = None

def warm_compiled_graph(*, checkpointer: Any) -> Any:
    """Build and cache the compiled graph with a checkpointer bound.

    Called once from the FastAPI lifespan so the process-wide graph
    carries the checkpointer. Idempotent: if the graph is already
    compiled, this is a no-op and the existing instance is returned.

    Args:
        checkpointer: A LangGraph checkpointer (MemorySaver, SqliteSaver,
            or any object implementing the checkpointer protocol).

    Returns:
        The shared compiled graph.
    """
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_graph(checkpointer=checkpointer)
    return _compiled_graph

def get_compiled_graph() -> Any:
    """Return a lazily-constructed, process-wide compiled graph.

    Building the graph is cheap; constructing the LLM client is not. The
    cache means the FastAPI process only pays that cost once.

    If :func:`warm_compiled_graph` was not called (e.g. from a script or
    a test that does not use the FastAPI lifespan), a graph without a
    checkpointer is built. In that case conversation state is not
    persisted across turns.

    Returns:
        The shared compiled graph.
    """
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_graph()
    return _compiled_graph

def reset_compiled_graph() -> None:
    """Clear the cached compiled graph.

    Intended for tests that need to rebuild the graph between cases.
    Not used by production code.
    """
    global _compiled_graph
    _compiled_graph = None

__all__ = [
    "build_graph",
    "get_compiled_graph",
    "reset_compiled_graph",
    "warm_compiled_graph",
]
