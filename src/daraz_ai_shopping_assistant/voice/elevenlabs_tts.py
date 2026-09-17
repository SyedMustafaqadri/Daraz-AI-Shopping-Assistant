"""ElevenLabs Flash streaming TTS adapter.

Wraps the ElevenLabs WebSocket streaming API. The adapter is the ONLY
module in this package that knows ElevenLabs's request/response protocol.

Protocol summary (stream-input, PCM out):

    Connect:
        wss://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream-input
              ?model_id=eleven_flash_v2_5&output_format=pcm_24000
        Header: xi-api-key: <ELEVENLABS_API_KEY>

    Send:
        - BOS: {"text": " ", "voice_settings": {...}, "generation_config": {...}}
        - Per sentence: {"text": "<sentence>", "try_trigger_generation": true}
        - EOS: {"text": ""}

    Receive:
        - {"audio": "<base64 PCM>", "isFinal": bool}
        - {"isFinal": true, ...}   (final marker, no audio)

The adapter decodes the base64 audio into raw PCM bytes and forwards them
via the ``on_audio`` callback.

The stream is per-turn: open a new adapter for each assistant reply and
close it when :meth:`finish` or :meth:`cancel` is called. This mirrors
ElevenLabs's recommended usage and keeps barge-in simple.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
from collections.abc import Awaitable, Callable
from typing import Any, Final

import websockets
from websockets.exceptions import ConnectionClosed, WebSocketException

from daraz_ai_shopping_assistant.core.config import settings
from daraz_ai_shopping_assistant.core.exceptions import VoiceError
from daraz_ai_shopping_assistant.core.logging import get_logger

logger = get_logger(__name__)

_TTS_BASE_URL: Final[str] = "wss://api.elevenlabs.io/v1/text-to-speech"

#: Voice settings tuned for shopping replies: moderate stability keeps the
#: voice from over-acting on price numbers; high similarity holds the voice
#: consistent across the turn.
_VOICE_SETTINGS: Final[dict[str, Any]] = {
    "stability": 0.5,
    "similarity_boost": 0.75,
    "style": 0.0,
    "use_speaker_boost": True,
}

#: chunk_length_schedule is the number of characters ElevenLabs buffers
#: before it starts synthesizing. Aggressive low values reduce first-byte
#: latency but degrade quality on short fragments.
_GENERATION_CONFIG: Final[dict[str, Any]] = {
    "chunk_length_schedule": [120, 160, 220, 260],
}

_ON_AUDIO = Callable[[bytes], Awaitable[None]]

class ElevenLabsTTSAdapter:
    """Streaming TTS adapter over ElevenLabs's WebSocket API.

    Attributes:
        _api_key: ElevenLabs API key.
        _voice_id: Target voice identifier.
        _model_id: ElevenLabs model identifier.
        _output_format: Output format string (``pcm_24000``).
        _ws: The live WebSocket connection, or ``None`` when closed.
        _reader_task: Background task draining ElevenLabs frames.
        _on_audio: Coroutine called with each decoded PCM chunk.
        _closing: True once :meth:`finish` or :meth:`cancel` has run.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        voice_id: str | None = None,
        model_id: str | None = None,
        sample_rate: int | None = None,
    ) -> None:
        """Initialise the adapter.

        Args:
            api_key: ElevenLabs API key. Defaults to
                ``settings.elevenlabs_api_key``.
            voice_id: Voice identifier. Defaults to
                ``settings.elevenlabs_voice_id``.
            model_id: Model identifier. Defaults to
                ``settings.elevenlabs_model_id``.
            sample_rate: Output sample rate in Hz. Defaults to
                ``settings.tts_sample_rate``.
        """
        self._api_key: str = (
            api_key if api_key is not None else settings.elevenlabs_api_key
        )
        self._voice_id: str = (
            voice_id if voice_id is not None else settings.elevenlabs_voice_id
        )
        self._model_id: str = (
            model_id if model_id is not None else settings.elevenlabs_model_id
        )
        self._sample_rate: int = (
            sample_rate if sample_rate is not None else settings.tts_sample_rate
        )
        self._ws: Any = None
        self._reader_task: asyncio.Task[None] | None = None
        self._on_audio: _ON_AUDIO | None = None
        self._closing: bool = False

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    async def start(self, on_audio: _ON_AUDIO) -> None:
        """Open the ElevenLabs stream and send the BOS message.

        Args:
            on_audio: Coroutine called with each decoded PCM chunk.

        Raises:
            VoiceError: When the API key is missing or the WebSocket
                handshake fails.
        """
        if not self._api_key:
            raise VoiceError(
                "ELEVENLABS_API_KEY is not configured. The voice endpoint "
                "requires an ElevenLabs API key. Add it to your .env file."
            )

        url = self._build_url()
        headers = {"xi-api-key": self._api_key}

        logger.info(
            "ELEVENLABS_CONNECTING",
            extra={
                "ctx": {
                    "voice_id": self._voice_id,
                    "model_id": self._model_id,
                    "sample_rate": self._sample_rate,
                }
            },
        )

        try:
            self._ws = await websockets.connect(
                url,
                additional_headers=headers,
                ping_interval=None,
                max_size=None,
            )
        except (WebSocketException, OSError) as exc:
            logger.warning(
                "ELEVENLABS_CONNECT_FAILED",
                extra={"ctx": {"error": type(exc).__name__, "detail": str(exc)[:200]}},
            )
            raise VoiceError(
                "Failed to open the ElevenLabs streaming connection.",
                context={"error": type(exc).__name__},
            ) from exc

        self._on_audio = on_audio
        self._closing = False
        self._reader_task = asyncio.create_task(self._read_loop())

        # BOS: the leading space is required by ElevenLabs's protocol.
        await self._send(
            {
                "text": " ",
                "voice_settings": _VOICE_SETTINGS,
                "generation_config": _GENERATION_CONFIG,
            }
        )

        logger.info(
            "ELEVENLABS_CONNECTED",
            extra={
                "ctx": {
                    "voice_id": self._voice_id,
                    "model_id": self._model_id,
                    "sample_rate": self._sample_rate,
                }
            },
        )

    async def speak(self, sentence: str) -> None:
        """Send one sentence to ElevenLabs for synthesis.

        Empty or whitespace-only sentences are ignored so a caller can
        pass the chunker's output unconditionally.

        Args:
            sentence: One complete sentence, no trailing whitespace
                required.
        """
        stripped = sentence.strip()
        if not stripped or self._ws is None or self._closing:
            logger.debug(
                "ELEVENLABS_SPEAK_SKIPPED",
                extra={
                    "ctx": {
                        "chars": len(stripped),
                        "ws_present": self._ws is not None,
                        "closing": self._closing,
                    }
                },
            )
            return
        logger.debug(
            "ELEVENLABS_SPEAK_SENT",
            extra={"ctx": {"chars": len(stripped), "preview": stripped[:40]}},
        )
        await self._send({"text": stripped + " ", "try_trigger_generation": True})

    async def finish(self) -> None:
        """Send EOS and wait for the server to drain, then close."""
        if self._closing:
            return
        self._closing = True

        if self._ws is not None:
            with contextlib.suppress(ConnectionClosed, WebSocketException):
                await self._ws.send(json.dumps({"text": ""}))

        if self._reader_task is not None:
            try:
                await asyncio.wait_for(self._reader_task, timeout=10.0)
            except (TimeoutError, asyncio.CancelledError):
                self._reader_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._reader_task

        if self._ws is not None:
            with contextlib.suppress(ConnectionClosed, WebSocketException):
                await self._ws.close()

        self._ws = None
        logger.info("ELEVENLABS_CLOSED")

    async def cancel(self) -> None:
        """Hard-close the stream mid-synthesis.

        Used for barge-in. Audio already delivered to the browser keeps
        playing until the client flushes its queue; the server just stops
        producing more.
        """
        if self._closing:
            return
        self._closing = True

        if self._reader_task is not None:
            self._reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader_task

        if self._ws is not None:
            with contextlib.suppress(ConnectionClosed, WebSocketException):
                await self._ws.close()

        self._ws = None
        logger.info("ELEVENLABS_CANCELLED")

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    def _build_url(self) -> str:
        """Return the fully-qualified ElevenLabs streaming URL.

        Returns:
            The URL with the model and output format query parameters.
        """
        output_format = f"pcm_{self._sample_rate}"
        return (
            f"{_TTS_BASE_URL}/{self._voice_id}/stream-input"
            f"?model_id={self._model_id}&output_format={output_format}"
        )

    async def _send(self, payload: dict[str, Any]) -> None:
        """JSON-encode and send a frame to ElevenLabs.

        Args:
            payload: The message body.
        """
        if self._ws is None:
            return
        with contextlib.suppress(ConnectionClosed, WebSocketException):
            await self._ws.send(json.dumps(payload))

    async def _read_loop(self) -> None:
        """Drain frames from ElevenLabs and forward decoded PCM."""
        assert self._ws is not None
        try:
            async for raw in self._ws:
                if isinstance(raw, bytes):
                    # ElevenLabs sends only text JSON frames.
                    continue
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    logger.warning(
                        "ELEVENLABS_BAD_FRAME",
                        extra={"ctx": {"bytes": len(raw)}},
                    )
                    continue
                await self._dispatch(payload)
        except asyncio.CancelledError:
            raise
        except (ConnectionClosed, WebSocketException) as exc:
            if not self._closing:
                logger.warning(
                    "ELEVENLABS_CONNECTION_CLOSED",
                    extra={
                        "ctx": {
                            "code": getattr(exc, "code", None),
                            "reason": str(getattr(exc, "reason", ""))[:120],
                        }
                    },
                )
        except Exception:
            logger.exception("ELEVENLABS_READ_LOOP_FAILED")

    async def _dispatch(self, payload: dict[str, Any]) -> None:
        """Decode and forward one ElevenLabs frame.

        Args:
            payload: The parsed JSON frame from ElevenLabs.
        """
        if self._on_audio is None:
            return

        # Log any error-shaped fields for debugging.
        if "error" in payload or ("message" in payload and "audio" not in payload):
            logger.warning(
                "ELEVENLABS_SERVER_MESSAGE",
                extra={"ctx": {"payload_keys": sorted(payload.keys())}},
            )

        audio_b64 = payload.get("audio")
        if not isinstance(audio_b64, str) or not audio_b64:
            logger.debug(
                "ELEVENLABS_NO_AUDIO_FRAME",
                extra={
                    "ctx": {
                        "keys": sorted(payload.keys()),
                        "is_final": payload.get("isFinal"),
                    }
                },
            )
            return
        try:
            pcm = base64.b64decode(audio_b64)
        except (ValueError, TypeError):
            logger.warning("ELEVENLABS_BAD_AUDIO_FRAME")
            return
        if pcm:
            logger.debug(
                "ELEVENLABS_AUDIO_CHUNK",
                extra={"ctx": {"bytes": len(pcm), "is_final": payload.get("isFinal")}},
            )
            await self._on_audio(pcm)

__all__ = ["ElevenLabsTTSAdapter"]
