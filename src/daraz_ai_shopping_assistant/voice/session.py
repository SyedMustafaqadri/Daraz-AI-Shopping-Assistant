"""Voice session turn manager.

One :class:`VoiceSession` handles one browser WebSocket connection. It
wires three things together:

    - the browser WebSocket (Starlette ``WebSocket``),
    - a :class:`DeepgramSTTAdapter` for the inbound audio,
    - one :class:`ElevenLabsTTSAdapter` per assistant turn, for outbound
      audio.

Turn triggering uses three independent signals, whichever fires first:

    1. ``speech_final=true`` on a Deepgram ``Results`` frame.
    2. ``UtteranceEnd`` from Deepgram's endpointing.
    3. A local debounce timer: 800 ms after the last ``final`` transcript
       with no new speech, the pending transcript is flushed as a turn.

    The first signal to fire wins; the others become no-ops because
    :meth:`_flush_pending_turn` clears the pending transcript. This makes
    the pipeline resilient to any single Deepgram signaling quirk.

Barge-in: if STT emits ``speech_started`` while a turn is speaking, the
turn is cancelled and the browser is told to flush its audio queue.
Interim transcripts are display updates and are not treated as barge-in
signals by themselves.

Followup filler: after the primary intent filler is played, a background
task waits for the primary filler's playback duration plus a small gap.
If no reply token has arrived by then, a second, generic filler plays.
This masks slow tool calls (Firecrawl often takes 8-10 seconds on a cold
cache). The followup is only played if it is already cached; the session
never synthesizes it mid-turn because that would delay the actual reply.

Only one turn is active at a time. Concurrency between the STT callback
and the client read loop is guarded by an ``asyncio.Lock`` around the
turn pointer, and every outgoing frame goes through a send lock so the
WebSocket is never written from two coroutines simultaneously.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket
from starlette.websockets import WebSocketState

from daraz_ai_shopping_assistant.core.config import settings
from daraz_ai_shopping_assistant.core.exceptions import VoiceError
from daraz_ai_shopping_assistant.core.logging import get_logger
from daraz_ai_shopping_assistant.services.chat_service import ChatService
from daraz_ai_shopping_assistant.voice.deepgram_stt import DeepgramSTTAdapter
from daraz_ai_shopping_assistant.voice.elevenlabs_tts import ElevenLabsTTSAdapter
from daraz_ai_shopping_assistant.voice.fillers import get_filler_cache
from daraz_ai_shopping_assistant.voice.sentence_chunker import SentenceChunker

logger = get_logger(__name__)

#: Seconds to wait after the last ``final`` transcript before flushing a
#: turn anyway. Acts as a safety net when Deepgram's own endpoint signal
#: does not fire. Chosen to feel responsive while not cutting the user
#: off mid-sentence.
_TURN_DEBOUNCE_S: float = 0.8

#: Minimum length of an interim transcript that accompanies a
#: ``speech_started`` event before barge-in is allowed to fire. Guards
#: against ambient noise triggering a false interruption.
_MIN_BARGE_IN_CHARS: int = 3

#: Silence gap, in seconds, between the end of the primary filler's
#: playback and the first followup filler. Subsequent followups fire
#: every ``_FOLLOWUP_INTERVAL_S`` until the reply starts.
_FOLLOWUP_GAP_S: float = 2.0

#: Seconds between consecutive followup fillers while the tool call is
#: still running. Longer than the gap that follows the primary filler
#: because the followup now repeats -- a shorter interval would nag.
_FOLLOWUP_INTERVAL_S: float = 3.0

#: Truncation length for logging transcript text.
_LOG_TEXT_PREVIEW: int = 60

#: Intents that trigger a tool call. Only these get the followup filler,
#: because they are the only ones that can take more than a couple of
#: seconds to answer.
_TOOL_INTENTS: frozenset[str] = frozenset(
    {"search", "get_product"}
)

@dataclass
class _Turn:
    """Mutable bookkeeping for one in-flight assistant turn.

    Attributes:
        id: Monotonic turn identifier. Bumped on barge-in so stale
            writes are ignored.
        cancelled: Set to True when a barge-in cancels this turn.
        completed: Set to True when the turn finishes normally.
        task: The asyncio task running the turn, when one is active.
    """

    id: int
    cancelled: bool = False
    completed: bool = False
    task: asyncio.Task[None] | None = field(default=None)

class VoiceSession:
    """Turn manager for one voice WebSocket connection.

    Attributes:
        _ws: The browser-side Starlette WebSocket.
        _stt: The Deepgram STT adapter.
        _chat: The chat service.
        _conversation_id: The current conversation id, or ``None`` before
            the first turn completes.
        _active_turn: The currently running turn, or ``None``.
        _turn_counter: Monotonic counter for turn ids.
        _turn_lock: Guards ``_active_turn`` and ``_turn_counter``.
        _send_lock: Serialises WebSocket sends.
        _pending_transcript: Accumulated text for the current user
            utterance. Flushed to a turn when a trigger fires.
        _defer_token: Monotonic token that invalidates stale debounce
            timers when new speech arrives.
        _defer_task: The most recent debounce task, kept so it can be
            cancelled when a new one is scheduled or on teardown.
        _barge_in_candidate: Whether a VAD speech-start event is awaiting
            a sufficiently substantial interim transcript.
        _warm_task: Background task warming the process-wide filler
            cache. Kept so teardown can cancel it.
        _warming_fillers: True while a background warm is in progress,
            so concurrent turns do not spawn duplicate warm tasks.
    """

    def __init__(
        self,
        *,
        websocket: WebSocket,
        stt: DeepgramSTTAdapter | None = None,
        chat: ChatService | None = None,
    ) -> None:
        """Initialise the session.

        Args:
            websocket: The accepted Starlette WebSocket.
            stt: Optional STT adapter. Defaults to a new
                :class:`DeepgramSTTAdapter` reading from settings.
            chat: Optional chat service. Defaults to a new
                :class:`ChatService` (which lazily resolves the compiled
                graph).
        """
        self._ws: WebSocket = websocket
        self._stt: DeepgramSTTAdapter = stt or DeepgramSTTAdapter()
        self._chat: ChatService = chat or ChatService()
        self._conversation_id: str | None = None
        self._active_turn: _Turn | None = None
        self._turn_counter: int = 0
        self._turn_lock: asyncio.Lock = asyncio.Lock()
        self._send_lock: asyncio.Lock = asyncio.Lock()
        self._pending_transcript: str = ""
        self._defer_token: int = 0
        self._defer_task: asyncio.Task[None] | None = None
        self._barge_in_candidate: bool = False
        self._warm_task: asyncio.Task[None] | None = None
        self._warming_fillers: bool = False

    # ------------------------------------------------------------------ #
    # Main loop
    # ------------------------------------------------------------------ #
    async def run(self) -> None:
        """Serve the WebSocket until the client disconnects.

        Starts the STT adapter, then reads frames from the browser until
        the connection closes. Audio frames go to STT; text frames are
        handled as control messages.

        Raises:
            VoiceError: When STT cannot start (missing key, bad handshake).
        """
        await self._stt.start(on_event=self._on_stt_event)
        await self._send_json({"type": "ready", "sample_rate": settings.tts_sample_rate})

        try:
            while True:
                message = await self._ws.receive()
                kind = message.get("type")
                if kind == "websocket.disconnect":
                    logger.info("VOICE_CLIENT_DISCONNECT")
                    break
                data = message.get("bytes")
                text = message.get("text")
                if data:
                    await self._stt.send_audio(data)
                elif text:
                    await self._on_client_text(text)
        except Exception:
            logger.exception("VOICE_SESSION_READ_LOOP_FAILED")
        finally:
            await self._teardown()

    # ------------------------------------------------------------------ #
    # Client messages
    # ------------------------------------------------------------------ #
    async def _on_client_text(self, text: str) -> None:
        """Handle a control message from the browser.

        Recognised messages:

            {"type": "hello", "conversation_id": "..."}  -- resume a
                conversation (optional; a new one is created otherwise).
            {"type": "end_turn"} -- force the current turn to finalize.

        Unknown types are ignored silently.

        Args:
            text: The raw text frame.
        """
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            logger.debug(
                "VOICE_CLIENT_TEXT_BAD_JSON",
                extra={"ctx": {"chars": len(text)}},
            )
            return
        kind = payload.get("type")
        if kind == "hello":
            conv_id = payload.get("conversation_id")
            if isinstance(conv_id, str) and conv_id.strip():
                self._conversation_id = conv_id.strip()
                logger.info(
                    "VOICE_SESSION_HELLO",
                    extra={"ctx": {"conversation_id": self._conversation_id}},
                )
        elif kind == "end_turn":
            logger.info("VOICE_END_TURN_REQUESTED")
            await self._flush_pending_turn(trigger="client_end_turn")
        else:
            logger.debug(
                "VOICE_CLIENT_TEXT_UNKNOWN",
                extra={"ctx": {"kind": kind}},
            )

    # ------------------------------------------------------------------ #
    # STT events
    # ------------------------------------------------------------------ #
    async def _on_stt_event(self, event: dict[str, Any]) -> None:
        """Translate an STT event into session actions.

        Three independent signals trigger a turn:
        ``speech_final=true`` on a final, ``UtteranceEnd``, and the
        debounce timer. Whichever fires first wins.

        Args:
            event: One of the dicts the STT adapter yields.
        """
        kind = event.get("type")
        text = str(event.get("text", "") or "")
        active_turn_id = self._active_turn.id if self._active_turn else None

        logger.debug(
            "VOICE_STT_EVENT",
            extra={
                "ctx": {
                    "kind": kind,
                    "text_len": len(text),
                    "text_preview": text[:_LOG_TEXT_PREVIEW],
                    "active_turn_id": active_turn_id,
                    "barge_in_candidate": self._barge_in_candidate,
                }
            },
        )

        if kind == "interim":
            if text:
                await self._send_json({"type": "transcript_interim", "text": text})
                logger.debug(
                    "VOICE_INTERIM",
                    extra={"ctx": {"text_preview": text[:_LOG_TEXT_PREVIEW]}},
                )
            # User is still speaking: cancel any pending debounce.
            self._defer_token += 1
            if self._barge_in_candidate:
                if len(text.strip()) >= _MIN_BARGE_IN_CHARS:
                    self._barge_in_candidate = False
                    await self._maybe_barge_in(
                        trigger=f"interim_text:{text[:_LOG_TEXT_PREVIEW]}"
                    )
                else:
                    logger.debug(
                        "VOICE_BARGE_IN_HELD",
                        extra={
                            "ctx": {
                                "reason": "interim_too_short",
                                "text_len": len(text.strip()),
                                "required": _MIN_BARGE_IN_CHARS,
                            }
                        },
                    )
            return

        if kind == "final":
            if text:
                self._pending_transcript = text
                await self._send_json({"type": "transcript_final", "text": text})
                logger.info(
                    "VOICE_FINAL",
                    extra={
                        "ctx": {
                            "text_preview": text[:_LOG_TEXT_PREVIEW],
                            "speech_final": bool(event.get("speech_final", False)),
                        }
                    },
                )
            if event.get("speech_final"):
                await self._flush_pending_turn(trigger="speech_final")
            else:
                self._schedule_turn_defer()
            return

        if kind == "utterance_end":
            logger.info("VOICE_UTTERANCE_END")
            await self._flush_pending_turn(trigger="utterance_end")
            return

        if kind == "speech_started":
            logger.info(
                "VOICE_SPEECH_STARTED",
                extra={"ctx": {"active_turn_id": active_turn_id}},
            )
            self._defer_token += 1
            self._barge_in_candidate = self._active_turn is not None
            return

        # metadata and any future events: ignore.

    # ------------------------------------------------------------------ #
    # Turn triggering
    # ------------------------------------------------------------------ #
    async def _flush_pending_turn(self, *, trigger: str) -> None:
        """Start a turn with the pending transcript, if any.

        Idempotent: clears the pending transcript and invalidates any
        outstanding debounce before starting the turn, so a concurrent
        signal becomes a no-op.

        Args:
            trigger: Short tag identifying which signal fired. Logged for
                debugging.
        """
        text = self._pending_transcript.strip()
        if not text:
            logger.debug(
                "VOICE_FLUSH_SKIPPED",
                extra={"ctx": {"trigger": trigger, "reason": "empty_pending"}},
            )
            return
        logger.info(
            "VOICE_FLUSH_TRIGGER",
            extra={
                "ctx": {
                    "trigger": trigger,
                    "text_preview": text[:_LOG_TEXT_PREVIEW],
                }
            },
        )
        self._pending_transcript = ""
        self._defer_token += 1
        self._barge_in_candidate = False
        await self._start_turn(text)

    def _schedule_turn_defer(self) -> None:
        """Arm a debounce timer that flushes the pending transcript.

        If new speech arrives before the timer fires, the token changes
        and the timer task exits without doing anything. The task
        reference is stored so a subsequent call or teardown can cancel
        it -- an uncancelled dangling task would survive the WebSocket.
        """
        self._defer_token += 1
        my_token = self._defer_token

        # Cancel any previous debounce before scheduling a new one.
        if self._defer_task is not None and not self._defer_task.done():
            self._defer_task.cancel()

        logger.debug(
            "VOICE_TURN_DEFER_ARMED",
            extra={"ctx": {"token": my_token, "seconds": _TURN_DEBOUNCE_S}},
        )

        async def _defer() -> None:
            try:
                await asyncio.sleep(_TURN_DEBOUNCE_S)
            except asyncio.CancelledError:
                return
            if my_token != self._defer_token:
                logger.debug(
                    "VOICE_TURN_DEFER_STALE",
                    extra={"ctx": {"token": my_token, "current": self._defer_token}},
                )
                return
            await self._flush_pending_turn(trigger="debounce")

        self._defer_task = asyncio.create_task(_defer())

    # ------------------------------------------------------------------ #
    # Turn lifecycle
    # ------------------------------------------------------------------ #
    async def _maybe_barge_in(self, *, trigger: str) -> None:
        """Cancel the active turn if the user has started speaking.

        Uses the turn lock so two concurrent STT events do not both
        cancel the same turn. The ``trigger`` argument is logged so a
        barge-in can be traced back to the STT event that caused it.

        Args:
            trigger: Short tag describing what fired the barge-in.
        """
        async with self._turn_lock:
            turn = self._active_turn
            if turn is None or turn.completed or turn.cancelled:
                logger.debug(
                    "VOICE_BARGE_IN_NOOP",
                    extra={
                        "ctx": {
                            "trigger": trigger,
                            "turn_present": turn is not None,
                            "turn_completed": turn.completed if turn else None,
                            "turn_cancelled": turn.cancelled if turn else None,
                        }
                    },
                )
                return
            turn.cancelled = True
            self._active_turn = None
            self._turn_counter += 1

        logger.info(
            "VOICE_BARGE_IN",
            extra={"ctx": {"turn_id": turn.id, "trigger": trigger}},
        )
        task = turn.task
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        await self._send_json({"type": "stop_playback"})

    async def _start_turn(self, text: str) -> None:
        """Begin a new assistant turn for a completed user utterance.

        Args:
            text: The user's final transcript.
        """
        if not text.strip():
            return

        # If a turn is already active, treat this as a barge-in so the
        # previous reply stops and the new one starts clean.
        await self._maybe_barge_in(trigger="new_turn_starting")

        async with self._turn_lock:
            self._turn_counter += 1
            turn = _Turn(id=self._turn_counter)
            self._active_turn = turn

        logger.info(
            "VOICE_TURN_STARTED",
            extra={
                "ctx": {
                    "turn_id": turn.id,
                    "text_len": len(text),
                    "text_preview": text[:_LOG_TEXT_PREVIEW],
                }
            },
        )
        turn.task = asyncio.create_task(self._run_turn(text, turn))

    async def _run_turn(self, text: str, turn: _Turn) -> None:
        """Run one full turn: chat stream -> TTS -> browser.

        Args:
            text: The user's final transcript.
            turn: The turn bookkeeping record.
        """
        tts = ElevenLabsTTSAdapter()
        chunker = SentenceChunker()
        sentences_queued = 0
        first_token_logged = False
        followup_task: asyncio.Task[None] | None = None

        async def _on_audio(pcm: bytes) -> None:
            if turn.cancelled:
                return
            logger.debug(
                "VOICE_AUDIO_FORWARDED",
                extra={"ctx": {"turn_id": turn.id, "bytes": len(pcm)}},
            )
            await self._send_bytes(pcm)

        try:
            try:
                logger.info(
                    "VOICE_TTS_STARTED",
                    extra={"ctx": {"turn_id": turn.id}},
                )
                await tts.start(on_audio=_on_audio)

                async for event in self._chat.chat_voice_stream(
                    text, self._conversation_id
                ):
                    if turn.cancelled:
                        logger.info(
                            "VOICE_TURN_ABANDONED",
                            extra={"ctx": {"turn_id": turn.id}},
                        )
                        return
                    kind = event.get("type")

                    if kind == "intent_parsed":
                        conv_id = event.get("conversation_id")
                        if isinstance(conv_id, str):
                            self._conversation_id = conv_id
                        intent_str = str(event.get("intent", ""))
                        logger.info(
                            "VOICE_INTENT_PARSED",
                            extra={
                                "ctx": {
                                    "turn_id": turn.id,
                                    "intent": intent_str,
                                    "query": event.get("query"),
                                }
                            },
                        )
                        await self._send_json(
                            {
                                "type": "intent",
                                "conversation_id": self._conversation_id,
                                "intent": intent_str,
                                "query": event.get("query"),
                            }
                        )
                        filler_bytes = await self._play_filler(intent_str)

                        # Schedule a followup filler for tool intents when
                        # the primary filler actually played. Fires after
                        # the primary's playback duration plus a gap, and
                        # is cancelled the moment a reply token arrives.
                        if filler_bytes > 0 and intent_str in _TOOL_INTENTS:
                            playback_s = filler_bytes / (
                                settings.tts_sample_rate * 2
                            )
                            delay = playback_s + _FOLLOWUP_GAP_S
                            followup_task = asyncio.create_task(
                                self._nag_until_first_token(turn, delay=delay)
                            )

                    elif kind == "tool_started":
                        logger.debug(
                            "VOICE_TOOL_STARTED",
                            extra={
                                "ctx": {"turn_id": turn.id, "tool": event.get("tool")}
                            },
                        )
                        await self._send_json(
                            {"type": "tool_started", "tool": event.get("tool")}
                        )

                    elif kind == "tool_done":
                        logger.debug(
                            "VOICE_TOOL_DONE",
                            extra={
                                "ctx": {"turn_id": turn.id, "tool": event.get("tool")}
                            },
                        )
                        await self._send_json(
                            {"type": "tool_done", "tool": event.get("tool")}
                        )

                    elif kind == "token":
                        token_text = str(event.get("text", ""))
                        if not first_token_logged:
                            first_token_logged = True
                            # Reply is coming; the followup is no longer
                            # needed.
                            if (
                                followup_task is not None
                                and not followup_task.done()
                            ):
                                followup_task.cancel()
                            logger.info(
                                "VOICE_FIRST_TOKEN",
                                extra={
                                    "ctx": {
                                        "turn_id": turn.id,
                                        "preview": token_text[:40],
                                    }
                                },
                            )
                        await self._send_json(
                            {"type": "reply_chunk", "text": token_text}
                        )
                        for sentence in chunker.feed(token_text):
                            sentences_queued += 1
                            logger.debug(
                                "VOICE_SENTENCE_QUEUED",
                                extra={
                                    "ctx": {
                                        "turn_id": turn.id,
                                        "index": sentences_queued,
                                        "chars": len(sentence),
                                        "preview": sentence[:40],
                                    }
                                },
                            )
                            if not turn.cancelled:
                                await tts.speak(sentence)

                    elif kind == "done":
                        if not turn.cancelled:
                            remainder = chunker.flush()
                            if remainder:
                                sentences_queued += 1
                                logger.debug(
                                    "VOICE_SENTENCE_QUEUED",
                                    extra={
                                        "ctx": {
                                            "turn_id": turn.id,
                                            "index": sentences_queued,
                                            "chars": len(remainder),
                                            "preview": remainder[:40],
                                            "source": "final_flush",
                                        }
                                    },
                                )
                                await tts.speak(remainder)
                            await tts.finish()
                            logger.info(
                                "VOICE_TURN_COMPLETED",
                                extra={
                                    "ctx": {
                                        "turn_id": turn.id,
                                        "sentences": sentences_queued,
                                        "intent": event.get("intent"),
                                    }
                                },
                            )
                            await self._send_json(
                                {
                                    "type": "done",
                                    "conversation_id": event.get("conversation_id"),
                                    "intent": event.get("intent"),
                                    "error": event.get("error"),
                                }
                            )

            except asyncio.CancelledError:
                # Barge-in cancels the task; the canceller has already
                # stopped the TTS adapter. Do not log this as an error.
                logger.info(
                    "VOICE_TURN_CANCELLED",
                    extra={"ctx": {"turn_id": turn.id}},
                )
                return
            except VoiceError as exc:
                logger.warning(
                    "VOICE_TURN_VENDOR_ERROR",
                    extra={"ctx": {"turn_id": turn.id, "message": exc.message}},
                )
                await self._send_json(
                    {"type": "error", "message": "Voice service unavailable."}
                )
            except Exception:
                logger.exception(
                    "VOICE_TURN_FAILED",
                    extra={"ctx": {"turn_id": turn.id}},
                )
                await self._send_json(
                    {"type": "error", "message": "Internal error."}
                )
            finally:
                if followup_task is not None and not followup_task.done():
                    followup_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await followup_task
                with contextlib.suppress(Exception):
                    await tts.cancel()
        finally:
            turn.completed = True
            async with self._turn_lock:
                if self._active_turn is turn:
                    self._active_turn = None

    async def _nag_until_first_token(
        self, turn: _Turn, *, delay: float
    ) -> None:
        """Play the followup filler repeatedly until a reply token arrives.

        The first repeat fires ``delay`` seconds after the primary filler
        was queued; subsequent repeats fire every ``_FOLLOWUP_INTERVAL_S``.
        The loop exits as soon as the turn completes, is cancelled, or
        the session is torn down.

        Looping matters for two reasons:

        - A single followup only buys a couple of seconds of cover.
          Firecrawl on a cold cache takes 8-10 seconds, so one followup
          leaves a large silent gap the user hears as a hang.
        - Some browsers (and OS network stacks behind them) treat a
          WebSocket as idle after roughly five seconds with no bytes
          arriving from the server, and close it. A looped filler keeps
          the server-to-client channel busy, which prevents that
          disconnect.

        Skipped entirely when the followup phrase is not yet cached,
        because synthesizing it mid-turn would delay the actual reply.

        Args:
            turn: The turn this followup belongs to.
            delay: Seconds to wait before the first repeat.
        """
        cache = get_filler_cache()
        audio = cache.audio_for("followup")
        if audio is None:
            logger.debug(
                "VOICE_FOLLOWUP_SKIPPED",
                extra={"ctx": {"turn_id": turn.id, "reason": "not_cached"}},
            )
            return

        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return

        repeats = 0
        while not turn.cancelled and not turn.completed:
            repeats += 1
            logger.info(
                "VOICE_FOLLOWUP_PLAYED",
                extra={
                    "ctx": {
                        "turn_id": turn.id,
                        "bytes": len(audio),
                        "repeat": repeats,
                    }
                },
            )
            await self._send_bytes(audio)
            try:
                await asyncio.sleep(_FOLLOWUP_INTERVAL_S)
            except asyncio.CancelledError:
                return

    async def _play_filler(self, intent: str) -> int:
        """Send a cached (or freshly synthesized) filler for the intent.

        Silently does nothing when no filler is available. Filler audio
        plays in the same stream as the reply, so the browser queue just
        starts a little earlier.

        Also kicks off a background warm of the remaining fillers
        (including the followup phrase) so they are ready the next time
        a slow tool call needs one. The warm is idempotent and bounded;
        it never blocks this turn.

        Args:
            intent: The parsed intent name.

        Returns:
            Number of PCM bytes actually sent to the browser, or 0 when
            no filler was available. The caller uses this to compute the
            followup delay from the primary filler's playback duration.
        """
        cache = get_filler_cache()
        audio = cache.audio_for(intent)
        if audio is None:
            logger.info(
                "VOICE_FILLER_LOOKUP",
                extra={"ctx": {"intent": intent, "hit": False}},
            )
            # Lazy warm; not fatal if ElevenLabs is slow or unavailable.
            with contextlib.suppress(Exception):
                audio = await cache.ensure(intent)
        else:
            logger.info(
                "VOICE_FILLER_LOOKUP",
                extra={"ctx": {"intent": intent, "hit": True}},
            )

        sent = 0
        if audio:
            logger.debug(
                "VOICE_FILLER_PLAYED",
                extra={"ctx": {"intent": intent, "bytes": len(audio)}},
            )
            await self._send_bytes(audio)
            sent = len(audio)

        # Warm the rest of the fillers in the background. The followup
        # phrase is synthesised by this path the first time any filler
        # plays, so it is available for later slow turns. The task is
        # bounded (a few seconds) and never blocks this turn.
        if not cache.is_warm() and not self._warming_fillers:
            self._warming_fillers = True
            self._warm_task = asyncio.create_task(self._warm_fillers_background())

        return sent

    async def _warm_fillers_background(self) -> None:
        """Synthesize any missing filler phrases.

        Best-effort: failure is logged but never re-raised. The flag is
        reset so a subsequent turn can retry.
        """
        try:
            await get_filler_cache().warm()
        except Exception:
            logger.exception("VOICE_FILLER_BACKGROUND_WARM_FAILED")
        finally:
            self._warming_fillers = False

    # ------------------------------------------------------------------ #
    # WebSocket helpers
    # ------------------------------------------------------------------ #
    async def _send_json(self, payload: dict[str, Any]) -> None:
        """Send a JSON text frame to the browser.

        Silently drops the frame when the connection is already closed.

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
            data: Raw PCM16 mono audio at ``settings.tts_sample_rate``.
        """
        if not data:
            return
        if self._ws.client_state != WebSocketState.CONNECTED:
            return
        async with self._send_lock:
            with contextlib.suppress(RuntimeError):
                await self._ws.send_bytes(data)

    async def _teardown(self) -> None:
        """Cancel any active turn, background tasks, and STT stream."""
        logger.info("VOICE_SESSION_TEARDOWN")

        # Cancel the debounce timer first so it cannot fire during
        # teardown and try to start a turn on a closed session.
        if self._defer_task is not None and not self._defer_task.done():
            self._defer_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._defer_task
        self._defer_task = None

        # Cancel the background filler warm. The cache is process-wide,
        # so partial state is fine: a later session resumes from where
        # this one left off.
        if self._warm_task is not None and not self._warm_task.done():
            self._warm_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._warm_task
        self._warm_task = None
        self._warming_fillers = False

        async with self._turn_lock:
            turn = self._active_turn
            if turn is not None and not turn.completed:
                turn.cancelled = True
                self._active_turn = None
                task = turn.task
                if task is not None:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await task

        with contextlib.suppress(Exception):
            await self._stt.finish()

__all__ = ["VoiceSession"]
