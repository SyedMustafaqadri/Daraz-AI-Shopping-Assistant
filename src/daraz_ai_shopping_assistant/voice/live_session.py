"""Live voice session turn manager.

One :class:`LiveVoiceSession` handles one browser WebSocket connection
talking to Gemini's Live API. Unlike the sandwich pipeline, Gemini owns
speech recognition, turn detection, barge-in, and speech synthesis -- so
this session has far less to do.

Responsibilities:

    - Relay browser PCM16 16 kHz audio into the adapter
    - Relay model PCM16 24 kHz audio out to the browser
    - Forward transcripts and lifecycle events to the browser
    - Execute tool calls by invoking the existing service-layer tools
    - Mirror the browser message contract used by the sandwich session so
      the frontend needs no per-mode rendering code

Browser message contract (identical to the sandwich session):

    Server -> Browser:
        {"type": "ready", "sample_rate": 24000}
        {"type": "transcript_interim", "text": "..."}
        {"type": "transcript_final", "text": "..."}
        {"type": "reply_chunk", "text": "..."}
        {"type": "tool_started", "tool": "..."}
        {"type": "tool_done", "tool": "..."}
        {"type": "done", "conversation_id": "...", "intent": null,
         "error": null}
        {"type": "error", "message": "..."}
        {"type": "stop_playback"}
        {"type": "ping", "ts": ...}
        Binary frames: PCM16 24 kHz mono audio

    Browser -> Server:
        Binary frames: PCM16 16 kHz mono audio
        {"type": "hello", "conversation_id": "..."}
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
import uuid
from typing import Any

from fastapi import WebSocket
from starlette.websockets import WebSocketState

from daraz_ai_shopping_assistant.agents.tools import (
    get_product_tool,
    search_products_tool,
)
from daraz_ai_shopping_assistant.core.config import settings
from daraz_ai_shopping_assistant.core.exceptions import DarazScraperError
from daraz_ai_shopping_assistant.core.logging import get_logger
from daraz_ai_shopping_assistant.voice.gemini_live import GeminiLiveAdapter

logger = get_logger(__name__)

#: Seconds between heartbeat pings to the browser.
_HEARTBEAT_INTERVAL_S: float = 5.0

#: Maximum number of products passed to the model after a search.
_SEARCH_RESULT_LIMIT: int = 5

#: Maximum number of characters of a tool result sent to the model.
#: Gemini Live accepts the full JSON, but a smaller window keeps the
#: model focused on the top results rather than enumerating 40 items.
_TOOL_RESULT_CHAR_LIMIT: int = 6000

#: Truncation length for logging.
_LOG_TEXT_PREVIEW: int = 60

class LiveVoiceSession:
    """Turn manager for one Live-mode WebSocket connection.

    Attributes:
        _ws: The browser-side Starlette WebSocket.
        _adapter: The :class:`GeminiLiveAdapter`.
        _conversation_id: Conversation identifier (informational only --
            Gemini owns the session state).
        _send_lock: Serialises WebSocket sends.
        _heartbeat_task: Background task sending pings.
        _user_transcript_buffer: Accumulated user transcript text for the
            current turn, flushed on turn complete.
        _model_transcript_buffer: Accumulated model transcript text.
    """

    def __init__(
        self,
        *,
        websocket: WebSocket,
        adapter: GeminiLiveAdapter | None = None,
    ) -> None:
        """Initialise the session.

        Args:
            websocket: The accepted Starlette WebSocket.
            adapter: Optional adapter. Defaults to a new
                :class:`GeminiLiveAdapter` reading from settings.
        """
        self._ws: WebSocket = websocket
        self._adapter: GeminiLiveAdapter = (
            adapter if adapter is not None else GeminiLiveAdapter()
        )
        self._conversation_id: str | None = None
        self._send_lock: asyncio.Lock = asyncio.Lock()
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._user_transcript_buffer: str = ""
        self._model_transcript_buffer: str = ""

    # ------------------------------------------------------------------ #
    # Main loop
    # ------------------------------------------------------------------ #
    async def run(self) -> None:
        """Serve the WebSocket until the client disconnects.

        Raises:
            VoiceError: When the Live session cannot be opened.
        """
        await self._adapter.start(on_event=self._on_adapter_event)
        await self._send_json(
            {"type": "ready", "sample_rate": settings.tts_sample_rate}
        )
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

        try:
            while True:
                message = await self._ws.receive()
                kind = message.get("type")
                if kind == "websocket.disconnect":
                    logger.info(
                        "LIVE_CLIENT_DISCONNECT",
                        extra={
                            "ctx": {
                                "code": message.get("code", "unknown"),
                                "reason": message.get("reason", ""),
                            }
                        },
                    )
                    break
                data = message.get("bytes")
                text = message.get("text")
                if data:
                    await self._adapter.send_audio(data)
                elif text:
                    await self._on_client_text(text)
        except Exception:
            logger.exception("LIVE_SESSION_READ_LOOP_FAILED")
        finally:
            if self._heartbeat_task is not None:
                self._heartbeat_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._heartbeat_task
                self._heartbeat_task = None
            await self._adapter.finish()

    # ------------------------------------------------------------------ #
    # Client messages
    # ------------------------------------------------------------------ #
    async def _on_client_text(self, text: str) -> None:
        """Handle a control message from the browser.

        Args:
            text: The raw text frame.
        """
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return
        kind = payload.get("type")
        if kind == "hello":
            conv_id = payload.get("conversation_id")
            if isinstance(conv_id, str) and conv_id.strip():
                self._conversation_id = conv_id.strip()
            else:
                self._conversation_id = str(uuid.uuid4())
            logger.info(
                "LIVE_SESSION_HELLO",
                extra={"ctx": {"conversation_id": self._conversation_id}},
            )
        # end_turn is not forwarded -- Gemini's VAD owns turn detection.

    # ------------------------------------------------------------------ #
    # Adapter events
    # ------------------------------------------------------------------ #
    async def _on_adapter_event(self, event: dict[str, Any]) -> None:
        """Translate an adapter event into browser actions.

        Args:
            event: One of the dicts the adapter emits.
        """
        kind = event.get("type")

        if kind == "connected":
            return

        if kind == "audio":
            await self._send_bytes(event["data"])
            return

        if kind == "transcript_input":
            self._user_transcript_buffer = event["text"]
            await self._send_json(
                {"type": "transcript_interim", "text": event["text"]}
            )
            return

        if kind == "transcript_output":
            self._model_transcript_buffer = event["text"]
            await self._send_json(
                {"type": "reply_chunk", "text": event["text"]}
            )
            return

        if kind == "interrupted":
            await self._send_json({"type": "stop_playback"})
            return

        if kind == "tool_call":
            await self._handle_tool_calls(event["calls"])
            return

        if kind == "turn_complete":
            if self._user_transcript_buffer:
                await self._send_json(
                    {
                        "type": "transcript_final",
                        "text": self._user_transcript_buffer,
                    }
                )
            await self._send_json(
                {
                    "type": "done",
                    "conversation_id": self._conversation_id,
                    "intent": None,
                    "error": None,
                }
            )
            self._user_transcript_buffer = ""
            self._model_transcript_buffer = ""
            return

        if kind == "resumption_update":
            logger.debug(
                "LIVE_SESSION_RESUMPTION_UPDATE",
                extra={"ctx": {"handle_len": len(event.get("handle", ""))}},
            )
            return

        logger.debug(
            "LIVE_SESSION_UNKNOWN_EVENT",
            extra={"ctx": {"kind": kind}},
        )

    # ------------------------------------------------------------------ #
    # Tool dispatch
    # ------------------------------------------------------------------ #
    async def _handle_tool_calls(self, calls: list[Any]) -> None:
        """Execute every tool call and return the responses to the model.

        Args:
            calls: The list of ``FunctionCall`` objects from the adapter.
        """
        from google.genai import types

        responses: list[types.FunctionResponse] = []
        for call in calls:
            name = getattr(call, "name", "unknown")
            call_id = getattr(call, "id", None)
            args = dict(getattr(call, "args", None) or {})

            logger.info(
                "LIVE_TOOL_STARTED",
                extra={"ctx": {"tool": name}},
            )
            await self._send_json({"type": "tool_started", "tool": name})

            result = await self._execute_tool(name, args)

            logger.info(
                "LIVE_TOOL_DONE",
                extra={
                    "ctx": {
                        "tool": name,
                        "keys": sorted(result.keys()),
                    }
                },
            )
            await self._send_json({"type": "tool_done", "tool": name})

            responses.append(
                types.FunctionResponse(
                    id=call_id,
                    name=name,
                    response=result,
                )
            )

        await self._adapter.send_tool_responses(responses)

    async def _execute_tool(
        self, name: str, args: dict[str, Any]
    ) -> dict[str, Any]:
        """Execute one tool and return a JSON-serialisable result.

        Errors are captured and returned as ``{"error": "..."}`` so the
        model can apologise rather than the session crashing.

        Args:
            name: The tool name.
            args: The arguments passed by the model.

        Returns:
            A dict suitable for a ``FunctionResponse``.
        """
        try:
            if name == "search_products":
                query = str(args.get("query", "")).strip()
                if not query:
                    return {"error": "Search query was empty."}
                result = await search_products_tool(
                    query,
                    min_price=args.get("min_price"),
                    max_price=args.get("max_price"),
                    page=1,
                )
                products = result.get("products", [])[:_SEARCH_RESULT_LIMIT]
                return {"products": products}

            if name == "get_product":
                product_id = str(args.get("product_id", "")).strip()
                if not product_id:
                    return {"error": "Product ID was empty."}
                result = await get_product_tool(product_id)
                trimmed = _trim_payload(result, _TOOL_RESULT_CHAR_LIMIT)
                return {"product": trimmed}

            return {"error": f"Unknown tool: {name}"}

        except DarazScraperError as exc:
            logger.warning(
                "LIVE_TOOL_SCRAPER_ERROR",
                extra={"ctx": {"tool": name, "message": exc.message}},
            )
            return {"error": exc.message}
        except Exception:
            logger.exception(
                "LIVE_TOOL_UNEXPECTED_ERROR",
                extra={"ctx": {"tool": name}},
            )
            return {"error": "Internal error while executing the tool."}

    # ------------------------------------------------------------------ #
    # WebSocket helpers
    # ------------------------------------------------------------------ #
    async def _send_json(self, payload: dict[str, Any]) -> None:
        """Send a JSON text frame to the browser.

        Args:
            payload: The message body.
        """
        if self._ws.client_state != WebSocketState.CONNECTED:
            return
        async with self._send_lock:
            with contextlib.suppress(RuntimeError):
                await self._ws.send_text(
                    json.dumps(payload, ensure_ascii=False, default=str)
                )

    async def _send_bytes(self, data: bytes) -> None:
        """Send a binary PCM frame to the browser.

        Args:
            data: Raw PCM16 mono audio at 24 kHz.
        """
        if not data:
            return
        if self._ws.client_state != WebSocketState.CONNECTED:
            return
        async with self._send_lock:
            with contextlib.suppress(RuntimeError):
                await self._ws.send_bytes(data)

    async def _heartbeat_loop(self) -> None:
        """Send a small JSON ping every 5 seconds.

        Keeps the server-to-client channel busy so an idle WebSocket is
        never closed by the browser while the model is thinking.
        """
        try:
            while True:
                await asyncio.sleep(_HEARTBEAT_INTERVAL_S)
                if self._ws.client_state != WebSocketState.CONNECTED:
                    return
                await self._send_json({"type": "ping", "ts": time.time()})
        except asyncio.CancelledError:
            raise

# ---------------------------------------------------------------------- #
# Helpers
# ---------------------------------------------------------------------- #
def _trim_payload(payload: Any, limit: int) -> Any:
    """Serialise a payload and truncate it if it exceeds ``limit``.

    Args:
        payload: Any JSON-serialisable value.
        limit: Maximum number of characters to keep.

    Returns:
        The original payload when short enough, otherwise a truncated
        representation.
    """
    try:
        serialised = json.dumps(payload, default=str, ensure_ascii=False)
    except (TypeError, ValueError):
        return payload
    if len(serialised) <= limit:
        return payload
    return {
        "truncated": True,
        "preview": serialised[:limit],
        "note": "Payload was truncated to fit the model's context window.",
    }

__all__ = ["LiveVoiceSession"]
