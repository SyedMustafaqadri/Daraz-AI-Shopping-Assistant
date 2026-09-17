"""Voice WebSocket endpoint.

Exposes ``GET /api/v1/voice/ws``. The handler is thin: it validates the
vendor keys, accepts the WebSocket, and hands control to
:class:`VoiceSession` for the rest of the connection's life.

Protocol (see ``docs/API-REFERENCE.md`` for the full table):

    Browser -> Server:
        Binary frames: raw 16 kHz PCM16 mono audio.
        Text frames: JSON control messages (currently only ``hello``).

    Server -> Browser:
        Binary frames: raw 24 kHz PCM16 mono audio to play.
        Text frames: JSON event messages (``ready``, ``transcript_*``,
        ``intent``, ``tool_*``, ``reply_chunk``, ``done``, ``error``,
        ``stop_playback``).

If either ``DEEPGRAM_API_KEY`` or ``ELEVENLABS_API_KEY`` is missing, the
endpoint accepts then immediately closes the WebSocket with a clear
error frame. It never reaches out to a vendor with a missing key.
"""

from __future__ import annotations

import contextlib
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from daraz_ai_shopping_assistant.core.config import settings
from daraz_ai_shopping_assistant.core.exceptions import VoiceError
from daraz_ai_shopping_assistant.core.logging import get_logger
from daraz_ai_shopping_assistant.voice.session import VoiceSession

logger = get_logger(__name__)

router = APIRouter(prefix="/voice", tags=["voice"])

@router.websocket("/ws")
async def voice_websocket(websocket: WebSocket) -> None:
    """Serve one voice conversation over a WebSocket.

    Args:
        websocket: The incoming WebSocket. FastAPI injects this.
    """
    await websocket.accept()

    missing: list[str] = []
    if not settings.deepgram_api_key:
        missing.append("DEEPGRAM_API_KEY")
    if not settings.elevenlabs_api_key:
        missing.append("ELEVENLABS_API_KEY")

    if missing:
        logger.warning(
            "VOICE_MISSING_KEYS",
            extra={"ctx": {"missing": missing}},
        )
        with contextlib.suppress(RuntimeError):
            await websocket.send_text(
                json.dumps(
                    {
                        "type": "error",
                        "message": (
                            "Voice service is not configured. Missing: "
                            + ", ".join(missing)
                        ),
                    }
                )
            )
            await websocket.close(code=1011)
        return

    logger.info("VOICE_WS_CONNECTED")

    session = VoiceSession(websocket=websocket)
    try:
        await session.run()
    except VoiceError as exc:
        logger.warning(
            "VOICE_SESSION_VENDOR_ERROR",
            extra={"ctx": {"message": exc.message}},
        )
        with contextlib.suppress(RuntimeError):
            await websocket.send_text(
                json.dumps({"type": "error", "message": exc.message})
            )
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("VOICE_SESSION_FAILED")
    finally:
        with contextlib.suppress(RuntimeError):
            await websocket.close()
        logger.info("VOICE_WS_DISCONNECTED")

__all__ = ["router"]
