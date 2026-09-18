"""Gemini Live WebSocket endpoint.

Exposes ``GET /api/v1/voice/live/ws``. The handler is thin: it validates
the Google API key, accepts the WebSocket, and hands control to
:class:`LiveVoiceSession`.

If ``GOOGLE_API_KEY`` is missing, the endpoint accepts then immediately
closes the WebSocket with a clear error frame. It never reaches out to
Gemini with a missing key.
"""

from __future__ import annotations

import contextlib
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from daraz_ai_shopping_assistant.core.config import settings
from daraz_ai_shopping_assistant.core.exceptions import VoiceError
from daraz_ai_shopping_assistant.core.logging import get_logger
from daraz_ai_shopping_assistant.voice.live_session import LiveVoiceSession

logger = get_logger(__name__)

router = APIRouter(prefix="/voice/live", tags=["voice"])

@router.websocket("/ws")
async def voice_live_websocket(websocket: WebSocket) -> None:
    """Serve one Live-mode voice conversation over a WebSocket.

    Args:
        websocket: The incoming WebSocket. FastAPI injects this.
    """
    await websocket.accept()

    if not settings.google_api_key:
        logger.warning("LIVE_MISSING_KEY")
        with contextlib.suppress(RuntimeError, WebSocketDisconnect):
            await websocket.send_text(
                json.dumps(
                    {
                        "type": "error",
                        "message": (
                            "Live voice is not configured. Missing: "
                            "GOOGLE_API_KEY"
                        ),
                    }
                )
            )
            await websocket.close(code=1011)
        return

    logger.info("LIVE_WS_CONNECTED")

    session = LiveVoiceSession(websocket=websocket)
    try:
        await session.run()
    except VoiceError as exc:
        logger.warning(
            "LIVE_SESSION_VENDOR_ERROR",
            extra={"ctx": {"message": exc.message}},
        )
        with contextlib.suppress(RuntimeError, WebSocketDisconnect):
            await websocket.send_text(
                json.dumps({"type": "error", "message": exc.message})
            )
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("LIVE_SESSION_FAILED")
    finally:
        with contextlib.suppress(RuntimeError, WebSocketDisconnect):
            await websocket.close()
        logger.info("LIVE_WS_DISCONNECTED")

__all__ = ["router"]
