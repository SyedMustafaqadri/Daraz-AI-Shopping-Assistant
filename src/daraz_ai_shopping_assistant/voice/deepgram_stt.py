"""Deepgram Nova-3 streaming STT adapter.

Wraps the Deepgram WebSocket streaming API. The adapter is the ONLY
module in this package that knows Deepgram's request/response protocol.

Protocol summary (streaming listen, raw PCM16 in):

    Connect:
        wss://api.deepgram.com/v1/listen
        Header: Authorization: Token <DEEPGRAM_API_KEY>
        Query:  model, language, encoding, sample_rate, channels,
                punctuate, interim_results, smart_format, endpointing,
                utterance_end_ms, vad_events

    Send:
        - Binary frames: raw PCM16 audio chunks.
        - Text frames: {"type": "CloseStream"} to finish.
        - Text frames: {"type": "KeepAlive"} every ~5s to prevent the
          server's inactivity timeout.

    Receive:
        - {"type": "Results", "channel": {"alternatives": [{"transcript": "..."}]},
           "is_final": bool, "speech_final": bool}
        - {"type": "UtteranceEnd", "last_word_end": float}
        - {"type": "SpeechStarted", "timestamp": float}
        - {"type": "Metadata", ...}

The adapter translates those into our own event dicts:

    {"type": "interim", "text": "..."}
    {"type": "final", "text": "...", "speech_final": bool}
    {"type": "utterance_end"}
    {"type": "speech_started"}
    {"type": "metadata"}

Endpointing quirk:

    Deepgram frequently splits the endpoint signal across two frames.
    The first ``Results`` frame carries the transcript with
    ``is_final=true, speech_final=false``; the follow-up frame carries
    an empty transcript with ``is_final=true, speech_final=true``. The
    adapter forwards both so the session can react to either signal.

Errors from Deepgram are mapped to :class:`VoiceError`.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Awaitable, Callable
from typing import Any, Final
from urllib.parse import urlencode

import websockets
from websockets.exceptions import ConnectionClosed, WebSocketException

from daraz_ai_shopping_assistant.core.config import settings
from daraz_ai_shopping_assistant.core.exceptions import VoiceError
from daraz_ai_shopping_assistant.core.logging import get_logger

logger = get_logger(__name__)

_DEEPGRAM_URL: Final[str] = "wss://api.deepgram.com/v1/listen"

#: Seconds between KeepAlive pings. Deepgram closes idle connections
#: after ~10s, so this gives comfortable headroom.
_KEEPALIVE_INTERVAL_S: Final[float] = 5.0

#: Callback type for translated events.
_ON_EVENT = Callable[[dict[str, Any]], Awaitable[None]]

class DeepgramSTTAdapter:
    """Streaming STT adapter over Deepgram's WebSocket API.

    Attributes:
        _api_key: Deepgram API key.
        _model: STT model identifier (default ``nova-3``).
        _language: Language hint (default ``multi``).
        _sample_rate: Input sample rate in Hz.
        _on_event: Coroutine called with each translated event dict.
        _ws: The live WebSocket connection, or ``None`` when closed.
        _reader_task: Background task draining Deepgram frames.
        _keepalive_task: Background task sending KeepAlive pings.
        _closing: True once :meth:`finish` has been called.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        language: str | None = None,
        sample_rate: int | None = None,
    ) -> None:
        """Initialise the adapter.

        Args:
            api_key: Deepgram API key. Defaults to
                ``settings.deepgram_api_key``.
            model: STT model identifier. Defaults to
                ``settings.deepgram_model``.
            language: Language hint. Defaults to
                ``settings.deepgram_language``.
            sample_rate: Input sample rate. Defaults to
                ``settings.stt_sample_rate``.
        """
        self._api_key: str = api_key if api_key is not None else settings.deepgram_api_key
        self._model: str = model if model is not None else settings.deepgram_model
        self._language: str = (
            language if language is not None else settings.deepgram_language
        )
        self._sample_rate: int = (
            sample_rate if sample_rate is not None else settings.stt_sample_rate
        )
        self._on_event: _ON_EVENT | None = None
        self._ws: Any = None
        self._reader_task: asyncio.Task[None] | None = None
        self._keepalive_task: asyncio.Task[None] | None = None
        self._closing: bool = False

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    async def start(self, on_event: _ON_EVENT) -> None:
        """Open the Deepgram stream and begin delivering events.

        Args:
            on_event: Coroutine called with each translated event dict.

        Raises:
            VoiceError: When the API key is missing or the WebSocket
                handshake fails.
        """
        if not self._api_key:
            raise VoiceError(
                "DEEPGRAM_API_KEY is not configured. The voice endpoint "
                "requires a Deepgram API key. Add it to your .env file."
            )

        url = self._build_url()
        headers = {"Authorization": f"Token {self._api_key}"}

        logger.info(
            "DEEPGRAM_CONNECTING",
            extra={
                "ctx": {
                    "model": self._model,
                    "language": self._language,
                    "sample_rate": self._sample_rate,
                }
            },
        )

        try:
            self._ws = await websockets.connect(
                url,
                additional_headers=headers,
                ping_interval=None,  # Deepgram does not respond to WS pings
                max_size=None,
            )
        except (WebSocketException, OSError) as exc:
            logger.warning(
                "DEEPGRAM_CONNECT_FAILED",
                extra={"ctx": {"error": type(exc).__name__, "detail": str(exc)[:200]}},
            )
            raise VoiceError(
                "Failed to open the Deepgram streaming connection.",
                context={"error": type(exc).__name__},
            ) from exc

        self._on_event = on_event
        self._closing = False
        self._reader_task = asyncio.create_task(self._read_loop())
        self._keepalive_task = asyncio.create_task(self._keepalive_loop())

        logger.info(
            "DEEPGRAM_CONNECTED",
            extra={
                "ctx": {
                    "model": self._model,
                    "language": self._language,
                    "sample_rate": self._sample_rate,
                }
            },
        )

    async def send_audio(self, chunk: bytes) -> None:
        """Forward a raw PCM16 audio frame to Deepgram.

        Silent no-op when the connection has already been closed, so a
        late frame from the browser does not crash the session.

        Args:
            chunk: Raw PCM16 mono audio at :attr:`_sample_rate` Hz.
        """
        if self._ws is None or self._closing:
            return
        with contextlib.suppress(ConnectionClosed, WebSocketException):
            await self._ws.send(chunk)

    async def finish(self) -> None:
        """Close the Deepgram stream and cancel background tasks."""
        if self._closing:
            return
        self._closing = True

        if self._ws is not None:
            with contextlib.suppress(ConnectionClosed, WebSocketException):
                await self._ws.send(json.dumps({"type": "CloseStream"}))

        if self._keepalive_task is not None:
            self._keepalive_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._keepalive_task

        if self._reader_task is not None:
            self._reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader_task

        if self._ws is not None:
            with contextlib.suppress(ConnectionClosed, WebSocketException):
                await self._ws.close()

        self._ws = None
        logger.info("DEEPGRAM_CLOSED")

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    def _build_url(self) -> str:
        """Return the fully-qualified Deepgram streaming URL.

        Returns:
            The URL with all query parameters set.
        """
        params = {
            "model": self._model,
            "language": self._language,
            "encoding": "linear16",
            "sample_rate": str(self._sample_rate),
            "channels": "1",
            "punctuate": "true",
            "interim_results": "true",
            "smart_format": "true",
            "endpointing": "300",
            "utterance_end_ms": "1000",
            "vad_events": "true",
        }
        return f"{_DEEPGRAM_URL}?{urlencode(params)}"

    async def _keepalive_loop(self) -> None:
        """Send KeepAlive frames until cancelled.

        Raises:
            asyncio.CancelledError: When :meth:`finish` cancels the task.
        """
        try:
            while True:
                await asyncio.sleep(_KEEPALIVE_INTERVAL_S)
                if self._ws is None or self._closing:
                    return
                with contextlib.suppress(ConnectionClosed, WebSocketException):
                    await self._ws.send(json.dumps({"type": "KeepAlive"}))
                    logger.debug("DEEPGRAM_KEEPALIVE_SENT")
        except asyncio.CancelledError:
            raise

    async def _read_loop(self) -> None:
        """Drain frames from Deepgram and forward translated events."""
        assert self._ws is not None
        try:
            async for raw in self._ws:
                if isinstance(raw, bytes):
                    # Deepgram's streaming API only sends text JSON frames.
                    continue
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    logger.warning(
                        "DEEPGRAM_BAD_FRAME",
                        extra={"ctx": {"bytes": len(raw)}},
                    )
                    continue
                await self._dispatch(payload)
        except asyncio.CancelledError:
            raise
        except (ConnectionClosed, WebSocketException) as exc:
            if not self._closing:
                logger.warning(
                    "DEEPGRAM_CONNECTION_CLOSED",
                    extra={
                        "ctx": {
                            "code": getattr(exc, "code", None),
                            "reason": str(getattr(exc, "reason", ""))[:120],
                        }
                    },
                )
        except Exception:
            logger.exception("DEEPGRAM_READ_LOOP_FAILED")

    async def _dispatch(self, payload: dict[str, Any]) -> None:
        """Translate one Deepgram frame into our event vocabulary.

        Handles the endpointing quirk described in the module docstring:
        a `Results` frame with `is_final=true` and an empty transcript
        can still carry `speech_final=true`, and that signal must reach
        the session.

        Args:
            payload: The parsed JSON frame from Deepgram.
        """
        if self._on_event is None:
            return

        kind = payload.get("type")

        logger.debug(
            "DEEPGRAM_FRAME",
            extra={
                "ctx": {
                    "kind": kind,
                    "is_final": payload.get("is_final"),
                    "speech_final": payload.get("speech_final"),
                }
            },
        )

        if kind == "Results":
            channel = payload.get("channel") or {}
            alternatives = channel.get("alternatives") or []
            transcript = ""
            if alternatives and isinstance(alternatives[0], dict):
                transcript = str(alternatives[0].get("transcript", "")).strip()

            is_final = bool(payload.get("is_final", False))
            speech_final = bool(payload.get("speech_final", False))

            if is_final:
                if transcript:
                    await self._on_event(
                        {
                            "type": "final",
                            "text": transcript,
                            "speech_final": speech_final,
                        }
                    )
                elif speech_final:
                    # Endpoint signal with no new text. Forward it so the
                    # session can finalize the pending transcript.
                    logger.info("DEEPGRAM_ENDPOINT_EMPTY_FINAL")
                    await self._on_event(
                        {
                            "type": "final",
                            "text": "",
                            "speech_final": True,
                        }
                    )
                else:
                    logger.debug(
                        "DEEPGRAM_EMPTY_FINAL_NO_ENDPOINT",
                        extra={"ctx": {"transcript_len": 0}},
                    )
                return

            if transcript:
                await self._on_event({"type": "interim", "text": transcript})
            return

        if kind == "UtteranceEnd":
            await self._on_event({"type": "utterance_end"})
            return

        if kind == "SpeechStarted":
            logger.info(
                "DEEPGRAM_SPEECH_STARTED_FRAME",
                extra={"ctx": {"timestamp": payload.get("timestamp")}},
            )
            await self._on_event({"type": "speech_started"})
            return

        if kind == "Metadata":
            await self._on_event({"type": "metadata"})
            return

        logger.debug(
            "DEEPGRAM_UNKNOWN_FRAME",
            extra={"ctx": {"kind": kind}},
        )

__all__ = ["DeepgramSTTAdapter"]
