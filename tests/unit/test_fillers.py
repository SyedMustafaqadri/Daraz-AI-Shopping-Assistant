"""Unit tests for :class:`FillerCache`.

Synthesis is monkeypatched so tests never touch ElevenLabs. The public
API (``audio_for``, ``ensure``, ``warm``, ``clear``) is exercised.

Tests reference ``_FILLER_PHRASES`` so that adding a new filler phrase
does not require updating a hardcoded count.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from daraz_ai_shopping_assistant.voice.fillers import (
    _FILLER_PHRASES,
    FillerCache,
    reset_filler_cache,
)


@pytest.fixture(autouse=True)
def _reset_singleton() -> None:
    """Drop the process-wide singleton between tests."""
    reset_filler_cache()

@pytest.mark.asyncio()
async def test_audio_for_returns_none_when_cold() -> None:
    """An unwarmed cache returns None for every intent."""
    cache = FillerCache()
    assert cache.audio_for("search") is None
    assert cache.audio_for("error") is None

@pytest.mark.asyncio()
async def test_warm_populates_every_known_filler() -> None:
    """warm() calls synthesis once per known filler and caches bytes."""
    expected_calls = len(_FILLER_PHRASES)
    with patch(
        "daraz_ai_shopping_assistant.voice.fillers._synthesize",
        new=AsyncMock(return_value=b"\x00\x01"),
    ) as mock_synth:
        cache = FillerCache()
        await cache.warm()

    assert cache.is_warm()
    for name in _FILLER_PHRASES:
        assert cache.audio_for(name) == b"\x00\x01", f"missing filler: {name}"
    assert mock_synth.await_count == expected_calls

@pytest.mark.asyncio()
async def test_warm_is_idempotent() -> None:
    """A second warm() call does not re-synthesize."""
    expected_calls = len(_FILLER_PHRASES)
    with patch(
        "daraz_ai_shopping_assistant.voice.fillers._synthesize",
        new=AsyncMock(return_value=b"x"),
    ) as mock_synth:
        cache = FillerCache()
        await cache.warm()
        await cache.warm()

    assert mock_synth.await_count == expected_calls

@pytest.mark.asyncio()
async def test_warm_tolerates_synthesis_failure() -> None:
    """A failing synthesis call is logged and skipped, not raised."""
    with patch(
        "daraz_ai_shopping_assistant.voice.fillers._synthesize",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        cache = FillerCache()
        await cache.warm()

    assert cache.audio_for("search") is None
    assert not cache.is_warm()

@pytest.mark.asyncio()
async def test_ensure_synthesizes_on_demand() -> None:
    """ensure() synthesizes a filler the first time it is requested."""
    with patch(
        "daraz_ai_shopping_assistant.voice.fillers._synthesize",
        new=AsyncMock(return_value=b"lazy"),
    ) as mock_synth:
        cache = FillerCache()
        audio = await cache.ensure("search")

    assert audio == b"lazy"
    assert cache.audio_for("search") == b"lazy"
    assert mock_synth.await_count == 1

@pytest.mark.asyncio()
async def test_ensure_returns_none_for_unknown_intent() -> None:
    """An unknown intent yields None without synthesis."""
    cache = FillerCache()
    assert await cache.ensure("unknown") is None

@pytest.mark.asyncio()
async def test_ensure_returns_none_on_failure() -> None:
    """A synthesis failure yields None, not an exception."""
    with patch(
        "daraz_ai_shopping_assistant.voice.fillers._synthesize",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        cache = FillerCache()
        assert await cache.ensure("search") is None

@pytest.mark.asyncio()
async def test_clear_drops_cached_audio() -> None:
    """clear() empties the cache."""
    with patch(
        "daraz_ai_shopping_assistant.voice.fillers._synthesize",
        new=AsyncMock(return_value=b"x"),
    ):
        cache = FillerCache()
        await cache.warm()
        assert cache.is_warm()
        cache.clear()
        assert not cache.is_warm()
