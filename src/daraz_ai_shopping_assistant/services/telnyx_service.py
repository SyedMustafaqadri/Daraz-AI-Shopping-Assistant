"""Telnyx Conversation Relay orchestration service."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx

from daraz_ai_shopping_assistant.core.config import settings
from daraz_ai_shopping_assistant.core.logging import get_logger
from daraz_ai_shopping_assistant.services.chat_service import get_chat_service

logger = get_logger(__name__)

_TELNYX_API_BASE = "https://api.telnyx.com/v2"


class TelnyxService:
    """Translate Telnyx call events into Conversation Relay sessions."""

    async def handle_webhook(self, event: dict[str, Any]) -> str:
        """Answer a new inbound call and start text-mode Conversation Relay."""
        data = event.get("data") or {}
        event_type = str(data.get("event_type") or "")
        payload = data.get("payload") or {}
        call_control_id = payload.get("call_control_id")

        logger.info(
            "TELNYX_WEBHOOK",
            extra={
                "ctx": {
                    "event_type": event_type,
                    "has_call_control_id": bool(call_control_id),
                }
            },
        )

        if event_type != "call.initiated" or not isinstance(call_control_id, str):
            return "accepted"
        if not settings.telnyx_api_key or not settings.telnyx_conversation_ws_url:
            logger.warning("TELNYX_NOT_CONFIGURED")
            return "not_configured"

        event_id = str(data.get("id") or call_control_id)
        await self._call_control(
            call_control_id,
            "answer",
            {"command_id": f"answer-{event_id}"},
        )
        await self._call_control(
            call_control_id,
            "conversation_relay_start",
            {
                "url": settings.telnyx_conversation_ws_url,
                "voice": settings.telnyx_voice,
                "language": settings.telnyx_language,
                "transcription_engine": settings.telnyx_transcription_engine,
                "greeting": settings.telnyx_greeting,
            },
        )
        return "accepted"

    async def stream_reply(
        self,
        prompt: str,
        conversation_id: str | None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield voice-friendly chat events for one caller prompt."""
        async for event in get_chat_service().chat_voice_stream(
            prompt,
            conversation_id=conversation_id,
        ):
            yield event

    async def _call_control(
        self,
        call_control_id: str,
        action: str,
        payload: dict[str, Any],
    ) -> None:
        """Send one Call Control command to Telnyx."""
        url = f"{_TELNYX_API_BASE}/calls/{call_control_id}/actions/{action}"
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {settings.telnyx_api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()


_default_service: TelnyxService | None = None


def get_telnyx_service() -> TelnyxService:
    """Return the process-wide Telnyx service singleton."""
    global _default_service
    if _default_service is None:
        _default_service = TelnyxService()
    return _default_service


__all__ = ["TelnyxService", "get_telnyx_service"]
