"""LangGraph agent layer (Phase 8).

This package owns the conversational pipeline: intent parsing, tool
selection, tool execution, and response formatting. It sits on top of the
service layer -- tools call services, never scrapers.

Public API:
    - AgentState, ParsedIntent, IntentType -- the graph's typed state.
    - build_graph, get_compiled_graph -- graph construction.
    - The tool functions in agents.tools.
"""

from __future__ import annotations

from daraz_ai_shopping_assistant.agents.graph import (
    build_graph,
    get_compiled_graph,
)
from daraz_ai_shopping_assistant.agents.state import (
    AgentState,
    IntentType,
    ParsedIntent,
)
from daraz_ai_shopping_assistant.agents.tools import (
    get_product_tool,
    search_products_tool,
)

__all__ = [
    "AgentState",
    "IntentType",
    "ParsedIntent",
    "build_graph",
    "get_compiled_graph",
    "get_product_tool",
    "search_products_tool",
]
