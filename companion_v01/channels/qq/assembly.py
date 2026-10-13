"""Wire one Bot's QQ channel dependencies; HTTP handling remains in routes.qq."""
from __future__ import annotations

import asyncio
from typing import Any, Callable

from companion_v01.channels.qq.commands.handler import (
    QQCommandHandler,
)
from companion_v01.channels.qq.inbound.observations import QQPassiveMessageHandler
from companion_v01.channels.qq.inbound.plugin_input import QQPluginInputBridge
from companion_v01.channels.qq.inbound.prepare import QQContentPreparation
from companion_v01.channels.qq.inbound.router import QQInboundEventHandler
from companion_v01.channels.qq.turns.attention import (
    QQGroupAttentionRuntime,
)
from companion_v01.channels.qq.turns.attention_state import QQGroupAttentionState
from companion_v01.channels.qq.turns.followups import QQFollowupRuntime
from companion_v01.channels.qq.turns.plugin_turns import QQPluginTurnAdapter
from companion_v01.channels.qq.turns.queue import QQSessionQueue
from companion_v01.channels.qq.turns.runner import QQTurnRunner
from companion_v01.channels.qq.turns.streaming import _process_qq_turn_streaming
from companion_v01.dialogue_targets import registry_for
from companion_v01.turn_coordination import TurnCoordinator

LogEvent = Callable[..., None]


def build_qq_event_handler(
    *,
    engine: Any,
    config_module: Any,
    qq_gateway: Any,
    runtime_metrics: Any,
    logger: Any,
    log_event: LogEvent,
    tts_client: Any = None,
    settings: Any = None,
    async_task_supervisor: Any = None,
    plugin_command_broker_provider: Callable[[], Any] | None = None,
    plugin_event_broker_provider: Callable[[], Any] | None = None,
    plugin_agent_event_handler_registrar: Callable[[str, Any], None] | None = None,
    plugin_conversation_ref_issuer: Callable[..., str] | None = None,
    plugin_turn_router: Any = None,
    thinking_mode_setter: Callable[[str], str] | None = None,
    turn_coordinator: Any = None,
    session_work_queue: Any = None,
) -> QQInboundEventHandler:
    dialogue_registry = registry_for(engine)
    qq_gateway.dialogue_registry = dialogue_registry
    turn_coordinator = turn_coordinator or TurnCoordinator()

    def schedule_followup(coroutine: Any) -> Any:
        if async_task_supervisor is not None:
            return async_task_supervisor.create_task(coroutine)
        return asyncio.create_task(coroutine)

    async def _send_reply(context: Any, text: str) -> dict[str, Any]:
        """Keep blocking OneBot delivery and synchronous Hook bridging off this loop."""

        return await asyncio.to_thread(qq_gateway.send_reply, context, text)

    async def _send_replies(context: Any, messages: list[str]) -> dict[str, Any]:
        return await asyncio.to_thread(qq_gateway.send_replies, context, messages)


    plugin_inputs_bridge = QQPluginInputBridge(
        plugin_conversation_ref_issuer=plugin_conversation_ref_issuer,
        plugin_event_broker_provider=plugin_event_broker_provider, plugin_turn_router=plugin_turn_router,
        log_event=log_event, schedule_task=schedule_followup,
    )
    # These two callbacks connect queue/attention admission to the assembled runner.
    # Construction does not invoke them; each router owns the instances they close over.
    attention_runtime = QQGroupAttentionRuntime(
        engine=engine, config_module=config_module, qq_gateway=qq_gateway, logger=logger,
        log_event=log_event, turn_coordinator=turn_coordinator, state=QQGroupAttentionState(),
        queue_has_work=lambda key: session_queue.queue.has_work(key),
        run_turn=lambda **payload: turn_runner.run(**payload), schedule_task=schedule_followup,
    )
    session_queue = QQSessionQueue(
        engine=engine, config_module=config_module, qq_gateway=qq_gateway, runtime_metrics=runtime_metrics,
        logger=logger, log_event=log_event, turn_coordinator=turn_coordinator,
        plugin_inputs=plugin_inputs_bridge, plugin_turn_router=plugin_turn_router, attention=attention_runtime,
        run_unlocked=lambda **payload: turn_runner.run_unlocked(**payload),
        queue=session_work_queue, schedule_task=schedule_followup,
    )
    turn_runner = QQTurnRunner(
        engine=engine, config_module=config_module, qq_gateway=qq_gateway,
        log_event=log_event, turn_coordinator=turn_coordinator,
        tts_client=tts_client, settings=settings, plugin_conversation_ref_issuer=plugin_conversation_ref_issuer,
        plugin_inputs=plugin_inputs_bridge, plugin_turn_router=plugin_turn_router,
        attention=attention_runtime, session_queue=session_queue,
        stream_processor=lambda **payload: _process_qq_turn_streaming(**payload),
    )
    followups = QQFollowupRuntime(
        engine=engine, config_module=config_module, qq_gateway=qq_gateway, logger=logger,
        log_event=log_event, prepare_payload=turn_runner.prepare_payload, run_turn=turn_runner.run,
    )
    plugin_turn_adapter = QQPluginTurnAdapter(
        qq_gateway=qq_gateway, logger=logger, session_queue=session_queue, run_turn=turn_runner.run,
    )
    if plugin_agent_event_handler_registrar is not None:
        plugin_agent_event_handler_registrar("qq", plugin_turn_adapter.submit)

    command_handler = QQCommandHandler(
        engine=engine, config_module=config_module, qq_gateway=qq_gateway,
        runtime_metrics=runtime_metrics, log_event=log_event, dialogue_registry=dialogue_registry,
        plugin_command_broker_provider=plugin_command_broker_provider,
        plugin_conversation_ref_issuer=plugin_conversation_ref_issuer,
        thinking_mode_setter=thinking_mode_setter, schedule_followup=schedule_followup,
        send_reply=_send_reply, send_replies=_send_replies,
        resume_after_decision=followups.resume_after_decision,
    )

    passive_handler = QQPassiveMessageHandler(
        engine=engine, config_module=config_module, runtime_metrics=runtime_metrics,
        log_event=log_event, turn_coordinator=turn_coordinator,
        session_queue=session_queue, attention=attention_runtime,
    )
    content_preparation = QQContentPreparation(
        engine=engine, config_module=config_module, qq_gateway=qq_gateway,
        runtime_metrics=runtime_metrics, log_event=log_event,
        plugin_inputs=plugin_inputs_bridge, followups=followups,
    )
    event_handler = QQInboundEventHandler(
        engine=engine, qq_gateway=qq_gateway, runtime_metrics=runtime_metrics,
        logger=logger, log_event=log_event, dialogue_registry=dialogue_registry,
        plugin_command_broker_provider=plugin_command_broker_provider, plugin_turn_router=plugin_turn_router,
        plugin_inputs_bridge=plugin_inputs_bridge, attention_runtime=attention_runtime,
        turn_runner=turn_runner, command_handler=command_handler,
        passive=passive_handler, content_preparation=content_preparation, send_reply=_send_reply,
    )
    return event_handler
