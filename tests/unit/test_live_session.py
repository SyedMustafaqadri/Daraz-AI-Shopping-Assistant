"""Unit tests for :class:`LiveVoiceSession`.

The Gemini adapter and the browser WebSocket are both mocked, so these
tests never open a real Live session and never touch the network. They
verify:

    - tool dispatch routes to the correct service tool
    - search results are capped at 5 products
    - get_product payloads are trimmed to the context limit
    - ``DarazScraperError`` becomes ``{"error": "..."}``, not a crash
    - adapter events are translated into the documented browser frames
    - turn_complete resets per-turn state and emits a ``done`` frame
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.websockets import WebSocketState

from daraz_ai_shopping_assistant.core.exceptions import (
    ScraperError,
    ScraperTimeoutError,
)
from daraz_ai_shopping_assistant.voice.live_session import LiveVoiceSession


# ---------------------------------------------------------------------- #
# Fixtures
# ---------------------------------------------------------------------- #
@pytest.fixture()
def fake_ws() -> MagicMock:
    """Return a fake WebSocket with async send methods."""
    ws = MagicMock()
    ws.client_state = WebSocketState.CONNECTED
    ws.send_text = AsyncMock()
    ws.send_bytes = AsyncMock()
    return ws

@pytest.fixture()
def fake_adapter() -> MagicMock:
    """Return a fake GeminiLiveAdapter with async methods."""
    adapter = MagicMock()
    adapter.start = AsyncMock()
    adapter.finish = AsyncMock()
    adapter.send_audio = AsyncMock()
    adapter.send_tool_responses = AsyncMock()
    return adapter

@pytest.fixture()
def session(fake_ws: MagicMock, fake_adapter: MagicMock) -> LiveVoiceSession:
    """Return a LiveVoiceSession wired to fakes."""
    return LiveVoiceSession(websocket=fake_ws, adapter=fake_adapter)

def _sent_texts(ws: MagicMock) -> list[dict[str, Any]]:
    """Return the parsed JSON payloads the fake WebSocket received."""
    return [json.loads(call.args[0]) for call in ws.send_text.call_args_list]

# ---------------------------------------------------------------------- #
# _execute_tool -- search
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio()
async def test_execute_search_tool_returns_products(
    session: LiveVoiceSession,
) -> None:
    """A search call returns the products list from the service tool."""
    products = [{"id": f"i{i}", "title": f"P{i}"} for i in range(3)]
    with patch(
        "daraz_ai_shopping_assistant.voice.live_session.search_products_tool",
        new=AsyncMock(return_value={"products": products}),
    ) as mock_tool:
        result = await session._execute_tool(
            "search_products", {"query": "mouse"}
        )

    mock_tool.assert_awaited_once_with(
        "mouse", min_price=None, max_price=None, page=1
    )
    assert result == {"products": products}

@pytest.mark.asyncio()
async def test_execute_search_tool_caps_at_five_products(
    session: LiveVoiceSession,
) -> None:
    """Only the top five products are forwarded to the model."""
    products = [{"id": f"i{i}"} for i in range(20)]
    with patch(
        "daraz_ai_shopping_assistant.voice.live_session.search_products_tool",
        new=AsyncMock(return_value={"products": products}),
    ):
        result = await session._execute_tool(
            "search_products", {"query": "mouse"}
        )

    assert len(result["products"]) == 5
    assert result["products"][0]["id"] == "i0"

@pytest.mark.asyncio()
async def test_execute_search_tool_rejects_empty_query(
    session: LiveVoiceSession,
) -> None:
    """An empty query is rejected without calling the service."""
    with patch(
        "daraz_ai_shopping_assistant.voice.live_session.search_products_tool",
        new=AsyncMock(),
    ) as mock_tool:
        result = await session._execute_tool("search_products", {"query": "   "})

    mock_tool.assert_not_awaited()
    assert "error" in result

# ---------------------------------------------------------------------- #
# _execute_tool -- get_product
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio()
async def test_execute_get_product_tool_returns_product(
    session: LiveVoiceSession,
) -> None:
    """get_product forwards the id and wraps the result under 'product'."""
    payload = {"id": "i927677133", "title": "Speaker", "price": 2145.0}
    with patch(
        "daraz_ai_shopping_assistant.voice.live_session.get_product_tool",
        new=AsyncMock(return_value=payload),
    ) as mock_tool:
        result = await session._execute_tool(
            "get_product", {"product_id": "i927677133"}
        )

    mock_tool.assert_awaited_once_with("i927677133")
    assert result == {"product": payload}

@pytest.mark.asyncio()
async def test_execute_get_product_tool_trims_large_payload(
    session: LiveVoiceSession,
) -> None:
    """A payload larger than the char limit is truncated, not sent whole."""
    huge_description = "x" * 20_000
    payload = {"id": "i1", "title": "Big", "description": huge_description}
    with patch(
        "daraz_ai_shopping_assistant.voice.live_session.get_product_tool",
        new=AsyncMock(return_value=payload),
    ):
        result = await session._execute_tool(
            "get_product", {"product_id": "i1"}
        )

    product = result["product"]
    assert product.get("truncated") is True
    assert len(product["preview"]) <= 6000

@pytest.mark.asyncio()
async def test_execute_unknown_tool_returns_error(
    session: LiveVoiceSession,
) -> None:
    """An unknown tool name returns an error dict."""
    result = await session._execute_tool("not_a_tool", {})
    assert "error" in result

# ---------------------------------------------------------------------- #
# _execute_tool -- errors
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio()
async def test_execute_tool_scraper_error_is_captured(
    session: LiveVoiceSession,
) -> None:
    """A typed scraper error becomes an error dict, not a raise."""
    with patch(
        "daraz_ai_shopping_assistant.voice.live_session.search_products_tool",
        new=AsyncMock(side_effect=ScraperTimeoutError("timed out")),
    ):
        result = await session._execute_tool(
            "search_products", {"query": "mouse"}
        )

    assert "error" in result
    assert "timed out" in result["error"]

@pytest.mark.asyncio()
async def test_execute_tool_generic_scraper_error_is_captured(
    session: LiveVoiceSession,
) -> None:
    """A generic ScraperError is also captured."""
    with patch(
        "daraz_ai_shopping_assistant.voice.live_session.get_product_tool",
        new=AsyncMock(side_effect=ScraperError("bad gateway")),
    ):
        result = await session._execute_tool(
            "get_product", {"product_id": "i1"}
        )

    assert "error" in result

@pytest.mark.asyncio()
async def test_execute_tool_unexpected_error_is_captured(
    session: LiveVoiceSession,
) -> None:
    """An unexpected exception is captured as a generic error string."""
    with patch(
        "daraz_ai_shopping_assistant.voice.live_session.search_products_tool",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        result = await session._execute_tool(
            "search_products", {"query": "mouse"}
        )

    assert "error" in result
    assert "Internal error" in result["error"]

# ---------------------------------------------------------------------- #
# _handle_tool_calls
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio()
async def test_handle_tool_calls_dispatches_and_responds(
    session: LiveVoiceSession,
    fake_ws: MagicMock,
    fake_adapter: MagicMock,
) -> None:
    """Each call is dispatched, framed, and sent back to the model."""
    call = SimpleNamespace(
        id="c1", name="search_products", args={"query": "mouse"}
    )

    with patch(
        "daraz_ai_shopping_assistant.voice.live_session.search_products_tool",
        new=AsyncMock(return_value={"products": [{"id": "i1"}]}),
    ):
        await session._handle_tool_calls([call])

    # Browser saw tool_started and tool_done.
    sent = _sent_texts(fake_ws)
    kinds = [p["type"] for p in sent]
    assert "tool_started" in kinds
    assert "tool_done" in kinds

    # The adapter received a FunctionResponse carrying the tool result.
    fake_adapter.send_tool_responses.assert_awaited_once()
    responses = fake_adapter.send_tool_responses.call_args.args[0]
    assert len(responses) == 1
    assert responses[0].name == "search_products"
    assert responses[0].id == "c1"
    assert responses[0].response == {"products": [{"id": "i1"}]}

# ---------------------------------------------------------------------- #
# _on_adapter_event
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio()
async def test_audio_event_sends_bytes(
    session: LiveVoiceSession, fake_ws: MagicMock
) -> None:
    """An ``audio`` event becomes a binary WebSocket frame."""
    await session._on_adapter_event({"type": "audio", "data": b"\x01\x02"})
    fake_ws.send_bytes.assert_awaited_once_with(b"\x01\x02")

@pytest.mark.asyncio()
async def test_transcript_input_becomes_interim_frame(
    session: LiveVoiceSession, fake_ws: MagicMock
) -> None:
    """``transcript_input`` is forwarded as a ``transcript_interim`` frame."""
    await session._on_adapter_event({"type": "transcript_input", "text": "hi"})
    sent = _sent_texts(fake_ws)
    assert sent == [{"type": "transcript_interim", "text": "hi"}]
    assert session._user_transcript_buffer == "hi"

@pytest.mark.asyncio()
async def test_transcript_output_becomes_reply_chunk(
    session: LiveVoiceSession, fake_ws: MagicMock
) -> None:
    """``transcript_output`` is forwarded as a ``reply_chunk`` frame."""
    await session._on_adapter_event(
        {"type": "transcript_output", "text": "here you go"}
    )
    sent = _sent_texts(fake_ws)
    assert sent == [{"type": "reply_chunk", "text": "here you go"}]

@pytest.mark.asyncio()
async def test_interrupted_event_sends_stop_playback(
    session: LiveVoiceSession, fake_ws: MagicMock
) -> None:
    """A server-side interruption tells the browser to flush its queue."""
    await session._on_adapter_event({"type": "interrupted"})
    sent = _sent_texts(fake_ws)
    assert sent == [{"type": "stop_playback"}]

@pytest.mark.asyncio()
async def test_turn_complete_emits_transcript_final_and_done(
    session: LiveVoiceSession, fake_ws: MagicMock
) -> None:
    """``turn_complete`` emits transcript_final (if buffered) then done."""
    session._conversation_id = "conv-1"
    session._user_transcript_buffer = "find me a mouse"

    await session._on_adapter_event({"type": "turn_complete"})

    sent = _sent_texts(fake_ws)
    kinds = [p["type"] for p in sent]
    assert "transcript_final" in kinds
    assert "done" in kinds

    transcript_final = next(p for p in sent if p["type"] == "transcript_final")
    assert transcript_final["text"] == "find me a mouse"

    done = next(p for p in sent if p["type"] == "done")
    assert done["conversation_id"] == "conv-1"
    assert set(done) == {"type", "conversation_id", "intent", "error"}
    assert done["error"] is None

    # Per-turn state is reset for the next turn.
    assert session._user_transcript_buffer == ""
    assert session._model_transcript_buffer == ""

@pytest.mark.asyncio()
async def test_turn_complete_without_user_transcript_skips_final(
    session: LiveVoiceSession, fake_ws: MagicMock
) -> None:
    """If no user transcript was buffered, only ``done`` is emitted."""
    await session._on_adapter_event({"type": "turn_complete"})
    sent = _sent_texts(fake_ws)
    kinds = [p["type"] for p in sent]
    assert "transcript_final" not in kinds
    assert kinds == ["done"]

# ---------------------------------------------------------------------- #
# Client control messages
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio()
async def test_hello_with_conversation_id_is_echoed(
    session: LiveVoiceSession,
) -> None:
    """``hello`` with an id stores the client's conversation identifier."""
    await session._on_client_text(
        json.dumps({"type": "hello", "conversation_id": "client-conv"})
    )
    assert session._conversation_id == "client-conv"

@pytest.mark.asyncio()
async def test_hello_without_conversation_id_generates_uuid(
    session: LiveVoiceSession,
) -> None:
    """``hello`` without an id generates a fresh UUID."""
    await session._on_client_text(json.dumps({"type": "hello"}))
    assert session._conversation_id is not None
    assert len(session._conversation_id) > 0

@pytest.mark.asyncio()
async def test_malformed_client_text_is_ignored(
    session: LiveVoiceSession,
) -> None:
    """Non-JSON text frames are silently dropped."""
    # Must not raise.
    await session._on_client_text("not json at all")
