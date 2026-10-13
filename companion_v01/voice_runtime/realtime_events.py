"""Shared outbound voice frame layouts; state remains in the owning session."""

from __future__ import annotations

from typing import Any


async def _send_response_presentation(websocket: Any, channel: Any, version: int, voice_turn_id: str) -> None:
    take = getattr(channel, "take_presentation", None)
    presentation = take() if callable(take) else None
    if presentation is not None:
        await websocket.send_json(
            {
                "type": "server.response.presentation",
                "protocol_version": version,
                "voice_turn_id": voice_turn_id,
                **presentation,
            }
        )


def playback_control_payload(control: Any, *, protocol_version: int) -> dict[str, Any]:
    return {
        "type": "server.playback.control",
        "protocol_version": protocol_version,
        "control_id": control.control_id,
        "command_id": control.command_id,
        "action": control.action,
        "delivery_id": control.delivery_id,
        "voice_turn_id": control.voice_turn_id,
        "response_id": control.response_id,
        "speech_unit_id": control.speech_unit_id,
        **({"resume_token": control.resume_token} if control.resume_token else {}),
        **({"interruption_id": control.interruption_id} if control.interruption_id else {}),
        **({"reason": control.reason} if control.reason else {}),
    }


def speech_payload(request: Any, *, protocol_version: int) -> dict[str, Any]:
    return {
        "type": "server.speech",
        "protocol_version": protocol_version,
        "delivery_id": request.delivery_id,
        "voice_turn_id": request.voice_turn_id,
        "response_id": request.response_id,
        "speech_unit_id": request.speech_unit_id,
        "ordinal": request.ordinal,
        "text": request.text,
        **({"emotion": request.emotion} if getattr(request, "emotion", "") else {}),
        "media_type": request.media_type,
        "byte_length": len(request.audio),
        "binary_follows": True,
    }
