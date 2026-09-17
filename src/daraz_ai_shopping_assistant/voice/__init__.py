"""Voice pipeline (STT + TTS).

This package is the voice analogue of the scraping package: it isolates
vendor-specific protocols behind small adapters so the rest of the app
never imports a vendor SDK.

Layout:

    - :mod:`deepgram_stt`     -- Deepgram Nova-3 streaming STT adapter.
    - :mod:`elevenlabs_tts`   -- ElevenLabs Flash streaming TTS adapter.
    - :mod:`sentence_chunker` -- pure token -> sentence splitter.
    - :mod:`fillers`          -- pre-synthesized latency-masking phrases.
    - :mod:`session`          -- turn manager + barge-in for one WS client.

Isolation rule (mirrors ``scrapers/firecrawl.py``): only
:mod:`deepgram_stt` knows Deepgram's protocol, only :mod:`elevenlabs_tts`
knows ElevenLabs's protocol. Everything above them sees abstract event
dicts and PCM ``bytes``.
"""

from __future__ import annotations

from daraz_ai_shopping_assistant.voice.deepgram_stt import DeepgramSTTAdapter
from daraz_ai_shopping_assistant.voice.elevenlabs_tts import ElevenLabsTTSAdapter
from daraz_ai_shopping_assistant.voice.sentence_chunker import SentenceChunker

__all__ = [
    "DeepgramSTTAdapter",
    "ElevenLabsTTSAdapter",
    "SentenceChunker",
]
