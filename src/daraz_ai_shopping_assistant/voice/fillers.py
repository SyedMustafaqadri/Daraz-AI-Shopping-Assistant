"""Pre-synthesized filler phrases.

The Firecrawl scrape can take three to fifteen seconds. Playing a short
acknowledgement ("Let me look that up.") the instant the intent is known
masks almost all of that wait, and the user perceives the assistant as
responsive.

Fillers are ElevenLabs audio, synthesized once and cached in memory.
They are keyed by intent so different intents get different phrasing.

Pre-warming at startup costs a handful of ElevenLabs characters. It is
opt-in via ``VOICE_PREWARM_FILLERS=true`` because development restarts
should not burn credits. When disabled, fillers are synthesized lazily
on first request and cached thereafter.

The ``"followup"`` phrase is a special filler used when the primary
filler has played but the tool call is still running. It is not keyed
by intent -- the same phrase plays for any slow tool call. It is
considered "not ready" if the cache has not warmed it yet, and the
session skips it rather than synthesizing mid-turn.
"""

from __future__ import annotations

import asyncio
from typing import Final

from daraz_ai_shopping_assistant.core.logging import get_logger
from daraz_ai_shopping_assistant.voice.elevenlabs_tts import ElevenLabsTTSAdapter

logger = get_logger(__name__)

#: Phrases spoken while the assistant is fetching. Kept short -- the
#: purpose is to signal "I heard you", not to add words.
_FILLER_PHRASES: Final[dict[str, str]] = {
    "search": "Let me look that up.",
    "get_product": "One moment please.",
    "get_recommendations": "Let me find some similar items.",
    "error": "Sorry, something went wrong.",
    # Played once, a few seconds after the primary filler, when the
    # tool call has not returned yet. Kept generic because it fires
    # for any slow intent.
    "followup": "Still looking, one moment.",
}

class FillerCache:
    """In-memory cache of synthesized filler audio.

    Attributes:
        _audio: Raw PCM bytes keyed by filler name.
        _lock: Guards lazy synthesis so concurrent first requests do not
            both hit ElevenLabs for the same phrase.
    """

    def __init__(self) -> None:
        """Initialise an empty cache."""
        self._audio: dict[str, bytes] = {}
        self._lock: asyncio.Lock = asyncio.Lock()

    # ------------------------------------------------------------------ #
    # Reads
    # ------------------------------------------------------------------ #
    def audio_for(self, intent: str) -> bytes | None:
        """Return cached audio for an intent, or ``None`` if not ready.

        Args:
            intent: The intent name (``search``, ``get_product``,
                ``get_recommendations``, ``error``, ``followup``).

        Returns:
            Raw PCM bytes, or ``None`` when no filler is cached for that
            intent. Callers should treat ``None`` as "skip the filler".
        """
        return self._audio.get(intent)

    def is_warm(self) -> bool:
        """Return True when every known filler has cached audio."""
        return all(name in self._audio for name in _FILLER_PHRASES)

    # ------------------------------------------------------------------ #
    # Writes
    # ------------------------------------------------------------------ #
    async def warm(self) -> None:
        """Synthesize every known filler phrase.

        Idempotent: phrases already cached are skipped. Never raises --
        failure to warm is logged and treated as "no fillers", which
        degrades to a slightly slower-feeling first turn rather than
        crashing startup.
        """
        async with self._lock:
            missing = [name for name in _FILLER_PHRASES if name not in self._audio]
            if not missing:
                return

            logger.info(
                "VOICE_FILLERS_WARMING",
                extra={"ctx": {"count": len(missing), "names": missing}},
            )

            for name in missing:
                phrase = _FILLER_PHRASES[name]
                try:
                    audio = await _synthesize(phrase)
                except Exception as exc:
                    logger.warning(
                        "VOICE_FILLER_SYNTH_FAILED",
                        extra={
                            "ctx": {
                                "name": name,
                                "error": type(exc).__name__,
                                "detail": str(exc)[:200],
                            }
                        },
                    )
                    continue
                if audio:
                    self._audio[name] = audio
                    logger.info(
                        "VOICE_FILLER_CACHED",
                        extra={"ctx": {"name": name, "bytes": len(audio)}},
                    )

    async def ensure(self, intent: str) -> bytes | None:
        """Return the filler for an intent, synthesizing it if missing.

        Args:
            intent: The intent name.

        Returns:
            Raw PCM bytes, or ``None`` when the phrase is unknown or
            synthesis failed. Never raises.
        """
        cached = self._audio.get(intent)
        if cached is not None:
            return cached

        phrase = _FILLER_PHRASES.get(intent)
        if phrase is None:
            logger.debug(
                "VOICE_FILLER_UNKNOWN_INTENT",
                extra={"ctx": {"intent": intent}},
            )
            return None

        async with self._lock:
            # Re-check inside the lock: a concurrent caller may have won.
            cached = self._audio.get(intent)
            if cached is not None:
                return cached

            logger.info(
                "VOICE_FILLER_SYNTH_STARTED",
                extra={"ctx": {"intent": intent}},
            )
            try:
                audio = await _synthesize(phrase)
            except Exception as exc:
                logger.warning(
                    "VOICE_FILLER_SYNTH_FAILED",
                    extra={
                        "ctx": {
                            "name": intent,
                            "error": type(exc).__name__,
                            "detail": str(exc)[:200],
                        }
                    },
                )
                return None

            if not audio:
                return None

            self._audio[intent] = audio
            logger.info(
                "VOICE_FILLER_CACHED",
                extra={"ctx": {"name": intent, "bytes": len(audio)}},
            )
            return audio

    def clear(self) -> None:
        """Drop every cached filler. Used by tests."""
        self._audio.clear()

# ---------------------------------------------------------------------- #
# Synthesis helper
# ---------------------------------------------------------------------- #
async def _synthesize(phrase: str) -> bytes:
    """Synthesize one phrase and return its raw PCM bytes.

    Opens a short-lived ElevenLabs stream, sends the phrase as a single
    "sentence", and collects the audio.

    Args:
        phrase: The text to speak.

    Returns:
        Raw PCM16 bytes at the configured TTS sample rate.

    Raises:
        VoiceError: When the ElevenLabs connection fails.
    """
    chunks: list[bytes] = []

    async def _collect(pcm: bytes) -> None:
        chunks.append(pcm)

    adapter = ElevenLabsTTSAdapter()
    await adapter.start(on_audio=_collect)
    try:
        await adapter.speak(phrase)
    finally:
        await adapter.finish()

    logger.info(
        "VOICE_FILLER_SYNTH_DONE",
        extra={"ctx": {"chars": len(phrase), "chunks": len(chunks)}},
    )
    return b"".join(chunks)

# ---------------------------------------------------------------------- #
# Process-wide singleton
# ---------------------------------------------------------------------- #
_default_cache: FillerCache | None = None

def get_filler_cache() -> FillerCache:
    """Return the process-wide FillerCache singleton.

    Returns:
        The shared cache. Created on first call.
    """
    global _default_cache
    if _default_cache is None:
        _default_cache = FillerCache()
    return _default_cache

def reset_filler_cache() -> None:
    """Clear and drop the singleton. Used by tests."""
    global _default_cache
    _default_cache = None

__all__ = ["FillerCache", "get_filler_cache", "reset_filler_cache"]
