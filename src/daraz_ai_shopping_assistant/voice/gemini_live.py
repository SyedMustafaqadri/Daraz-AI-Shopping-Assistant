"""Gemini Live API adapter.

Wraps the ``google-genai`` Live API so no other module imports it
directly. Mirrors the isolation rule used by ``FirecrawlAdapter`` and
``DeepgramSTTAdapter``: only this module knows the SDK exists.

Responsibilities:

    - Open a bidirectional Live session with ``client.aio.live.connect``
    - Register function declarations that map to the project's existing
      service-layer tools
    - Relay browser PCM16 16 kHz audio into the session
    - Relay model PCM16 24 kHz audio out of the session
    - Dispatch incoming tool calls to the service layer and return the
      result via ``session.send_tool_response``
    - Surface barge-in (``interrupted``) as an event for the session layer
    - Persist the resumption handle so a brief disconnect can resume the
      same conversation

Event vocabulary emitted to the caller:

    {"type": "connected"}
    {"type": "audio", "data": bytes}
    {"type": "transcript_input", "text": "..."}
    {"type": "transcript_output", "text": "..."}
    {"type": "tool_call", "calls": [...]}
    {"type": "interrupted"}
    {"type": "turn_complete"}
    {"type": "resumption_update", "handle": "..."}

SDK response shape (READ THIS BEFORE EDITING ``_dispatch``):

    The SDK delivers every model message as a ``LiveServerMessage``.
    Audio, transcripts, and turn signals are nested under
    ``server_content`` -- they are NOT top-level attributes on the
    message. Reading ``response.data`` or ``response.text`` returns
    ``None`` on every message, which silently drops all audio.

    Nested under ``response.server_content``:

        server_content.model_turn.parts[i].inline_data.data   -- audio
        server_content.model_turn.parts[i].text               -- model text
        server_content.input_transcription.text               -- user transcript
        server_content.output_transcription.text              -- model transcript
        server_content.interrupted                            -- barge-in
        server_content.turn_complete                          -- end of turn

    Top-level on ``response``:

        response.tool_call.function_calls
        response.session_resumption_update.new_handle
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from typing import Any, Final

from google import genai
from google.genai import types

from daraz_ai_shopping_assistant.core.config import settings
from daraz_ai_shopping_assistant.core.exceptions import VoiceError
from daraz_ai_shopping_assistant.core.logging import get_logger

logger = get_logger(__name__)

_ON_EVENT = Callable[[dict[str, Any]], Awaitable[None]]

#: System instruction for the Live session. Mirrors the voice prompt used
#: by the sandwich pipeline (``_RESPONSE_SYSTEM_PROMPT_VOICE``) but is
#: delivered once at session setup rather than per turn.
_SYSTEM_INSTRUCTION: Final[str] = (
    "You are a voice shopping assistant for Daraz.pk. "
    "You help users find products by searching and fetching details. "
    "Keep replies short and conversational -- they will be spoken aloud. "
    "When you present products, mention at most three, with the short title "
    "and price in rupees. "
    "Never invent product data. If a tool returns an error, apologise briefly "
    "and suggest trying again. "
    "Use the search_products tool when the user describes what they want. "
    "And when you are searching the product add the filler words accordingly like 'Searching for products...' or 'Looking up that item...' "
    "Use get_product when the user asks about a specific product ID."
)

#: Function declarations matching the service-layer tools.
_TOOL_DECLARATIONS: Final[list[dict[str, Any]]] = [
    {
        "name": "search_products",
        "description": (
            "Search Daraz.pk for products matching a query. "
            "Returns up to 5 products with title, price, URL, and rating."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Free-text search query, e.g. 'gaming mouse'.",
                },
                "min_price": {
                    "type": "number",
                    "description": "Minimum price in PKR. Optional.",
                },
                "max_price": {
                    "type": "number",
                    "description": "Maximum price in PKR. Optional.",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_product",
        "description": (
            "Fetch full details for a specific Daraz product by its ID. "
            "Use this when the user asks about a specific product they "
            "have seen or mentioned."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "product_id": {
                    "type": "string",
                    "description": (
                        "Daraz product identifier, 'i' followed by digits "
                        "(e.g. 'i927677133')."
                    ),
                },
            },
            "required": ["product_id"],
        },
    },
]

class GeminiLiveAdapter:
    """Async wrapper around a single Gemini Live session.

    Attributes:
        _api_key: Google API key.
        _model: Live model identifier (loaded from ``LLM_LIVE_MODEL``).
        _voice_name: Prebuilt voice name.
        _client: The ``google-genai`` client.
        _session_cm: The async context manager returned by ``connect``.
        _session: The live session object.
        _reader_task: Background task draining model messages.
        _on_event: Coroutine receiving translated events.
        _closing: True once :meth:`finish` has been called.
        _resumption_handle: Last resumption token, or ``None``.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        voice_name: str | None = None,
        system_instruction: str | None = None,
    ) -> None:
        """Initialise the adapter.

        Args:
            api_key: Google API key. Defaults to ``settings.google_api_key``.
            model: Live model identifier. Defaults to
                ``settings.llm_live_model`` (env: ``LLM_LIVE_MODEL``).
            voice_name: Prebuilt voice name. Defaults to
                ``settings.gemini_live_voice``.
            system_instruction: System instruction text. Defaults to the
                module-level prompt.
        """
        self._api_key: str = api_key if api_key is not None else settings.google_api_key
        self._model: str = (
            model if model is not None else settings.llm_live_model
        )
        self._voice_name: str = (
            voice_name if voice_name is not None else settings.gemini_live_voice
        )
        self._system_instruction: str = (
            system_instruction or _SYSTEM_INSTRUCTION
        )
        self._client: genai.Client | None = None
        self._session_cm: Any = None
        self._session: Any = None
        self._reader_task: asyncio.Task[None] | None = None
        self._on_event: _ON_EVENT | None = None
        self._closing: bool = False
        self._resumption_handle: str | None = None

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    async def start(self, on_event: _ON_EVENT) -> None:
        """Open the Live session and begin delivering events.

        Args:
            on_event: Coroutine called with each translated event dict.

        Raises:
            VoiceError: When the API key is missing or the session cannot
                be opened.
        """
        if not self._api_key:
            raise VoiceError(
                "GOOGLE_API_KEY is not configured. The Live voice endpoint "
                "requires a Google API key. Add it to your .env file."
            )

        self._on_event = on_event
        self._closing = False
        self._client = genai.Client(api_key=self._api_key)

        tools = [types.Tool(function_declarations=_TOOL_DECLARATIONS)]
        speech_config = types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(
                    voice_name=self._voice_name,
                )
            )
        )
        session_resumption = types.SessionResumptionConfig(
            handle=self._resumption_handle,
        )

        config = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            system_instruction=types.Content(
                parts=[types.Part(text=self._system_instruction)]
            ),
            speech_config=speech_config,
            tools=tools,
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
            session_resumption=session_resumption,
        )

        logger.info(
            "GEMINI_LIVE_CONNECTING",
            extra={
                "ctx": {
                    "model": self._model,
                    "voice": self._voice_name,
                    "resuming": self._resumption_handle is not None,
                }
            },
        )

        try:
            self._session_cm = self._client.aio.live.connect(
                model=self._model,
                config=config,
            )
            self._session = await self._session_cm.__aenter__()
        except Exception as exc:
            logger.warning(
                "GEMINI_LIVE_CONNECT_FAILED",
                extra={
                    "ctx": {
                        "error": type(exc).__name__,
                        "detail": str(exc)[:200],
                    }
                },
            )
            raise VoiceError(
                "Failed to open the Gemini Live session.",
                context={"error": type(exc).__name__},
            ) from exc

        self._reader_task = asyncio.create_task(self._read_loop())
        logger.info(
            "GEMINI_LIVE_CONNECTED",
            extra={"ctx": {"model": self._model}},
        )
        await self._emit({"type": "connected"})

    async def send_audio(self, chunk: bytes) -> None:
        """Forward a PCM16 16 kHz audio frame to the Live session.

        Args:
            chunk: Raw PCM16 mono audio at 16 kHz.
        """
        if self._session is None or self._closing:
            return
        with contextlib.suppress(Exception):
            await self._session.send_realtime_input(
                audio=types.Blob(
                    data=chunk,
                    mime_type="audio/pcm;rate=16000",
                )
            )

    async def send_tool_responses(
        self, responses: list[types.FunctionResponse]
    ) -> None:
        """Send function responses back to the model.

        Args:
            responses: The ``FunctionResponse`` objects to send.
        """
        if self._session is None or self._closing:
            return
        if not responses:
            return
        with contextlib.suppress(Exception):
            await self._session.send_tool_response(
                function_responses=responses
            )

    async def finish(self) -> None:
        """Close the Live session and cancel background tasks."""
        if self._closing:
            return
        self._closing = True

        if self._reader_task is not None:
            self._reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader_task
            self._reader_task = None

        if self._session_cm is not None:
            with contextlib.suppress(Exception):
                await self._session_cm.__aexit__(None, None, None)
            self._session_cm = None
            self._session = None

        self._client = None
        logger.info("GEMINI_LIVE_CLOSED")

    # ------------------------------------------------------------------ #
    # Read loop
    # ------------------------------------------------------------------ #
    async def _read_loop(self) -> None:
        """Drain each per-turn Live iterator until the session closes."""
        assert self._session is not None
        try:
            while not self._closing:
                async for response in self._session.receive():
                    await self._dispatch(response)
        except asyncio.CancelledError:
            raise
        except Exception:
            if not self._closing:
                logger.exception("GEMINI_LIVE_READ_LOOP_FAILED")

    async def _dispatch(self, response: Any) -> None:
        """Translate one ``LiveServerMessage`` into our event vocabulary.

        Audio, transcripts, and turn signals are nested under
        ``response.server_content`` -- not top-level. Tool calls and
        resumption updates sit at the top level of the message. See the
        module docstring for the full path table.

        Args:
            response: The raw ``LiveServerMessage`` from the SDK.
        """
        # ----- Top-level: session resumption -----
        resumption_update = getattr(response, "session_resumption_update", None)
        if resumption_update is not None:
            new_handle = getattr(resumption_update, "new_handle", None)
            if new_handle:
                self._resumption_handle = new_handle
                await self._emit(
                    {"type": "resumption_update", "handle": new_handle}
                )

        # ----- Top-level: tool calls -----
        tool_call = getattr(response, "tool_call", None)
        if tool_call is not None:
            calls = getattr(tool_call, "function_calls", None) or []
            if calls:
                logger.info(
                    "GEMINI_LIVE_TOOL_CALL",
                    extra={"ctx": {"count": len(calls)}},
                )
                await self._emit({"type": "tool_call", "calls": list(calls)})

        # ----- Nested: server_content -----
        server_content = getattr(response, "server_content", None)
        if server_content is None:
            return

        # Model turn: audio and/or text parts.
        model_turn = getattr(server_content, "model_turn", None)
        if model_turn is not None:
            parts = getattr(model_turn, "parts", None) or []
            for part in parts:
                inline_data = getattr(part, "inline_data", None)
                if inline_data is not None:
                    audio_bytes = getattr(inline_data, "data", None)
                    if audio_bytes:
                        await self._emit(
                            {"type": "audio", "data": audio_bytes}
                        )
                part_text = getattr(part, "text", None)
                if part_text:
                    await self._emit(
                        {"type": "transcript_output", "text": part_text}
                    )

        # User speech transcript.
        input_transcription = getattr(
            server_content, "input_transcription", None
        )
        if input_transcription is not None:
            transcript_text = getattr(input_transcription, "text", None)
            if transcript_text:
                await self._emit(
                    {"type": "transcript_input", "text": transcript_text}
                )

        # Model speech transcript.
        output_transcription = getattr(
            server_content, "output_transcription", None
        )
        if output_transcription is not None:
            transcript_text = getattr(output_transcription, "text", None)
            if transcript_text:
                await self._emit(
                    {"type": "transcript_output", "text": transcript_text}
                )

        # Barge-in.
        interrupted = getattr(server_content, "interrupted", None)
        if interrupted:
            logger.info("GEMINI_LIVE_INTERRUPTED")
            await self._emit({"type": "interrupted"})

        # End of turn.
        turn_complete = getattr(server_content, "turn_complete", None)
        if turn_complete:
            await self._emit({"type": "turn_complete"})

    async def _emit(self, event: dict[str, Any]) -> None:
        """Forward an event to the registered callback.

        Args:
            event: The translated event dict.
        """
        if self._on_event is not None:
            await self._on_event(event)

__all__ = ["GeminiLiveAdapter"]
