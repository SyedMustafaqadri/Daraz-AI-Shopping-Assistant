"""Telnyx Conversation Relay endpoints.

The webhook answers inbound calls and starts Telnyx Conversation Relay in
text mode. The WebSocket then forwards transcribed caller turns through the
existing voice-aware chat service, so Daraz tools remain in the service layer.
"""

from __future__ import annotations

import contextlib
import json
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from daraz_ai_shopping_assistant.core.logging import get_logger
from daraz_ai_shopping_assistant.services.telnyx_service import (
    get_telnyx_service,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/telnyx", tags=["telnyx"])

@router.post("/webhook")
async def telnyx_webhook(event: dict[str, Any]) -> dict[str, str]:
    """Handle Telnyx call events and start Conversation Relay.

    Configure this URL as the webhook for the Telnyx Voice API application.
    For production, place the route behind a signed-webhook verifier or an
    authenticated gateway before accepting untrusted traffic.
    """
    try:
        status = await get_telnyx_service().handle_webhook(event)
    except Exception:
        logger.exception(
            "TELNYX_CALL_CONTROL_FAILED",
        )
        status = "accepted"
    return {"status": status}


@router.websocket("/conversation")
async def telnyx_conversation(websocket: WebSocket) -> None:
    """Bridge Telnyx text prompts to the existing voice chat service."""
    await websocket.accept()
    conversation_id: str | None = None
    service = get_telnyx_service()

    try:
        while True:
            message = json.loads(await websocket.receive_text())
            message_type = message.get("type")

            if message_type == "setup":
                conversation_id = (
                    message.get("call_control_id")
                    or message.get("callSid")
                    or message.get("sessionId")
                )
                continue

            if message_type != "prompt" or not message.get("last", True):
                continue

            prompt = str(message.get("voicePrompt") or "").strip()
            if not prompt:
                continue

            emitted_token = False
            async for event in service.stream_reply(
                prompt,
                conversation_id=conversation_id,
            ):
                if event.get("type") == "token" and event.get("text"):
                    emitted_token = True
                    await websocket.send_json(
                        {
                            "type": "text",
                            "token": str(event["text"]),
                            "last": False,
                            "interruptible": True,
                        }
                    )
                elif event.get("type") == "done":
                    if not emitted_token:
                        await websocket.send_json(
                            {
                                "type": "text",
                                "token": "I could not find an answer. Please try again.",
                                "last": False,
                                "interruptible": True,
                            }
                        )
                    await websocket.send_json(
                        {"type": "text", "token": "", "last": True}
                    )
    except WebSocketDisconnect:
        return
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        logger.exception("TELNYX_CONVERSATION_BAD_MESSAGE")
        with contextlib.suppress(RuntimeError, WebSocketDisconnect):
            await websocket.close(code=1003)
    except Exception:
        logger.exception("TELNYX_CONVERSATION_FAILED")
        with contextlib.suppress(RuntimeError, WebSocketDisconnect):
            await websocket.close(code=1011)


__all__ = ["router"]
