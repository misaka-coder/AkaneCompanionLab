"""Choose the realtime session from the first authenticated websocket message."""

from __future__ import annotations

from typing import Any, Callable

from fastapi import WebSocket

from .realtime_protocol import VoiceRealtimeCoordinatorFactory, VoiceRealtimeCallFactory, _websocket_json_message_type
from .realtime_turn import VoiceRealtimeWebSocketSession
from .realtime_call import VoiceRealtimeCallWebSocketSession


async def handle_voice_realtime_websocket(
    websocket: WebSocket,
    *,
    coordinator_factory: VoiceRealtimeCoordinatorFactory | None,
    call_factory: VoiceRealtimeCallFactory | None = None,
    runtime_metrics: Any,
    log_event: Callable[..., None],
    subprotocol: str | None = None,
) -> None:
    if subprotocol:
        await websocket.accept(subprotocol=subprotocol)
    else:
        await websocket.accept()
    try:
        initial_message = await websocket.receive()
    except Exception:
        return
    message_type = _websocket_json_message_type(initial_message)
    if message_type == "client.call.open":
        session: Any = VoiceRealtimeCallWebSocketSession(
            websocket=websocket,
            call_factory=call_factory,
            runtime_metrics=runtime_metrics,
            log_event=log_event,
            websocket_accepted=True,
            initial_message=initial_message,
        )
    else:
        session = VoiceRealtimeWebSocketSession(
            websocket=websocket,
            coordinator_factory=coordinator_factory,
            runtime_metrics=runtime_metrics,
            log_event=log_event,
            websocket_accepted=True,
            initial_message=initial_message,
        )
    await session.run()
