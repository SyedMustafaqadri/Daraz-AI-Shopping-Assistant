#!/usr/bin/env python3
"""Run a live ElevenLabs TTS smoke test and save the generated audio.

Usage:
    uv run python scripts/test_elevenlabs_tts.py
    uv run python scripts/test_elevenlabs_tts.py --output tts-check.wav

The script reads ``ELEVENLABS_API_KEY`` and the other voice settings from the
project environment, usually ``.env``.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import wave
from pathlib import Path

from daraz_ai_shopping_assistant.core.config import settings
from daraz_ai_shopping_assistant.core.exceptions import VoiceError
from daraz_ai_shopping_assistant.voice.elevenlabs_tts import ElevenLabsTTSAdapter

TEXT = (
    "Hello from the Daraz AI Shopping Assistant. "
    "This sentence confirms that ElevenLabs text to speech is working."
)


def parse_args() -> argparse.Namespace:
    """Parse command-line options for the smoke test."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("elevenlabs_tts_smoke_test.wav"),
        help="WAV file to create (default: elevenlabs_tts_smoke_test.wav)",
    )
    return parser.parse_args()


async def synthesize(output_path: Path) -> int:
    """Synthesize the smoke-test sentence and write it as a WAV file."""
    audio_chunks: list[bytes] = []

    async def collect_audio(chunk: bytes) -> None:
        audio_chunks.append(chunk)

    adapter = ElevenLabsTTSAdapter()
    try:
        await adapter.start(collect_audio)
        await adapter.speak(TEXT)
        await adapter.finish()
    except VoiceError as exc:
        print(f"ElevenLabs TTS failed: {exc}", file=sys.stderr)
        return 1

    audio = b"".join(audio_chunks)
    if not audio:
        print("ElevenLabs TTS failed: no audio was returned.", file=sys.stderr)
        return 1

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(output_path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(settings.tts_sample_rate)
        wav_file.writeframes(audio)

    print(
        f"ElevenLabs TTS succeeded: {len(audio):,} bytes of PCM audio "
        f"written to {output_path}"
    )
    return 0


def main() -> None:
    """Run the asynchronous TTS smoke test."""
    args = parse_args()
    raise SystemExit(asyncio.run(synthesize(args.output)))


if __name__ == "__main__":
    main()
