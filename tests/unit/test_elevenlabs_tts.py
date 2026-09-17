"""Unit tests for the ElevenLabs streaming adapter."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from daraz_ai_shopping_assistant.voice.elevenlabs_tts import ElevenLabsTTSAdapter


def test_build_url_uses_supported_stream_parameters() -> None:
    """The stream URL contains only the current model and audio options."""
    adapter = ElevenLabsTTSAdapter(
        api_key="test-key",
        voice_id="test-voice",
        model_id="eleven_flash_v2_5",
        sample_rate=24000,
    )

    parsed = urlparse(adapter._build_url())

    assert parsed.path.endswith("/test-voice/stream-input")
    assert parse_qs(parsed.query) == {
        "model_id": ["eleven_flash_v2_5"],
        "output_format": ["pcm_24000"],
    }
