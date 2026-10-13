"""QQ HTTP admission and per-Bot runtime assembly, with legacy helper aliases."""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any, Callable

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..deployment_security import AdminWriteAuth, QQChannelRuntimeConfig

# Direct aliases preserve the existing helpers' names and signatures for callers.
from ..qq_artifact_delivery import (
    _qq_file_delivery_event_identity,
    _qq_has_visible_action_receipt,
    _hydrate_plugin_managed_artifact_events,
)
from ..qq_attention_runtime import (
    QQGroupAttentionRuntime,
    GROUP_ATTENTION_REVIEW_CONTEXT,
    _group_attention_review_event,
    _apply_group_attention_actor_scope,
)
from ..qq_command_handlers import QQCommandHandler, _is_qq_stop_command, _group_command_target_status
from ..qq_followups import QQFollowupRuntime
from ..qq_group_attention import QQGroupAttentionState
from ..qq_inbound_content import (
    _qq_quoted_message_reference,
    _with_qq_sender_context,
    _merge_qq_attachments,
    _build_qq_unavailable_quote_context,
    _qq_structured_forward_references,
    _build_qq_unavailable_forward_context,
    _append_qq_attachment_handles_to_message,
    _normalize_passive_image_placeholder,
    _build_qq_image_vision_followup_note,
    _qq_image_followup_failure_message,
    _qq_sender_role,
)
from ..qq_inbound_events import QQInboundEventHandler
from ..qq_inbound_media import QQInboundMedia
from ..qq_passive_messages import QQPassiveMessageHandler, _resolve_group_passive_memory_policy
from ..qq_plugin_input import QQPluginInputBridge
from ..qq_plugin_turns import QQPluginTurnAdapter
from ..qq_reply_delivery import (
    _normalize_reply_medium,
    _streaming_allows_text,
    _streaming_allows_tool_preface,
    _resolve_delivery_medium,
    _send_qq_delivery,
    _load_qq_delivery_config,
    _send_qq_emotion_image_fallback,
)
from ..qq_session_queue import QQSessionQueue
from ..qq_session_work import _session_work_key, _durable_qq_work_payload, _session_item_wait_ms
from ..qq_stream_delivery import (
    _normalize_reply_text,
    _canonical_reply_text,
    _strip_canonical_prefix,
    _reply_prefix_at_boundary,
    _filter_unsent_reply_messages,
    _StageSpeechReplay,
    _send_pending_stage_messages,
    _coalesce_deferred_stream_messages,
    _split_reply_for_transport,
)
from ..qq_turn_runtime import QQTurnRunner
from ..qq_turn_streaming import _process_qq_turn_streaming
from ..qq_voice_delivery import (
    _media_type_extension,
    _run_async_safely,
    _resolve_qq_tts_profile_user_id,
    _voice_cache_key,
    _prune_voice_cache,
    _synthesize_qq_voice_file,
)
from ..qq_workspace_messages import (
    QQ_WORKSPACE_KIND_LABELS,
    _format_qq_timestamp,
    _qq_item_time_label,
    _parse_qq_workspace_command,
    _qq_workspace_kind_label,
    _looks_like_machine_name,
    _friendly_qq_workspace_title,
    _format_qq_workspace_item,
    _format_qq_generated_item,
    _build_qq_workspace_list_reply,
    _build_qq_workspace_clear_reply,
)
from ..turn_coordination import TurnCoordinator


LogEvent = Callable[..., None]
_QQ_ROUTE_BASE_RE = re.compile(r"^/api(?:/[A-Za-z0-9._-]+)+$")


def _normalize_qq_route_base(value: Any) -> str:
    normalized = str(value or "").strip().rstrip("/")
    if _QQ_ROUTE_BASE_RE.fullmatch(normalized) is None:
        raise ValueError("invalid_qq_route_base")
    return normalized


def build_qq_router(
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
    channel_config: QQChannelRuntimeConfig | None = None,
    admin_auth: AdminWriteAuth | None = None,
    route_base: str = "/api/qq",
    plugin_command_broker_provider: Callable[[], Any] | None = None,
    plugin_event_broker_provider: Callable[[], Any] | None = None,
    plugin_agent_event_handler_registrar: Callable[[str, Any], None] | None = None,
    plugin_conversation_ref_issuer: Callable[..., str] | None = None,
    plugin_turn_router: Any = None,
    thinking_mode_setter: Callable[[str], str] | None = None,
    turn_coordinator: Any = None,
    session_work_queue: Any = None,
) -> APIRouter:
    from ..dialogue_targets import registry_for
    dialogue_registry = registry_for(engine)
    qq_gateway.dialogue_registry = dialogue_registry
    router = APIRouter()
    qq_route_base = _normalize_qq_route_base(route_base)
    diagnostic_auth = admin_auth or AdminWriteAuth.local_compatibility()
    turn_coordinator = turn_coordinator or TurnCoordinator()

    def schedule_followup(coroutine: Any) -> Any:
        if async_task_supervisor is not None:
            return async_task_supervisor.create_task(coroutine)
        return asyncio.create_task(coroutine)

    async def _send_route_reply(context: Any, text: str) -> dict[str, Any]:
        """Keep blocking OneBot delivery and synchronous Hook bridging off this loop."""

        return await asyncio.to_thread(qq_gateway.send_reply, context, text)

    async def _send_route_replies(context: Any, messages: list[str]) -> dict[str, Any]:
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
        send_reply=_send_route_reply, send_replies=_send_route_replies,
        resume_after_decision=followups.resume_after_decision,
    )

    passive_handler = QQPassiveMessageHandler(
        engine=engine, config_module=config_module, runtime_metrics=runtime_metrics,
        log_event=log_event, turn_coordinator=turn_coordinator,
        session_queue=session_queue, attention=attention_runtime,
    )
    media_handler = QQInboundMedia(
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
        passive=passive_handler, media=media_handler, send_reply=_send_route_reply,
    )

    @router.get(f"{qq_route_base}/napcat/status")
    async def qq_napcat_status(request: Request) -> JSONResponse:
        authorization = diagnostic_auth.authorize(request)
        if not authorization.ok:
            return JSONResponse(
                {"ok": False, "status": "forbidden", "reason": authorization.reason},
                status_code=authorization.status_code,
            )
        data = dict(qq_gateway.status())
        return JSONResponse({"status": "ok", "data": data})

    @router.post(f"{qq_route_base}/self-check")
    async def qq_self_check(request: Request) -> JSONResponse:
        """QQ / NapCat 连通性自检。主动测试 OneBot HTTP API 可达性和鉴权，返回结构化诊断。"""
        authorization = diagnostic_auth.authorize(request)
        if not authorization.ok:
            return JSONResponse(
                {"ok": False, "status": "forbidden", "reason": authorization.reason},
                status_code=authorization.status_code,
            )
        result = qq_gateway.self_check()
        return JSONResponse({"status": "ok", "data": result})

    @router.post(f"{qq_route_base}/napcat/event")
    async def qq_napcat_event(request: Request) -> JSONResponse:
        started_at = time.perf_counter()

        if channel_config is not None:
            auth = channel_config.authorize_webhook(request, body=await request.body())
            if not auth.ok:
                runtime_metrics.observe_request(
                    "qq_napcat_event",
                    duration_ms=(time.perf_counter() - started_at) * 1000,
                    ok=False,
                )
                return JSONResponse(
                    {"ok": False, "status": "forbidden", "reason": auth.reason},
                    status_code=auth.status_code,
                )

        bridge_enabled = (
            channel_config.enabled
            if channel_config is not None
            else bool(getattr(config_module, "QQ_BRIDGE_ENABLED", False))
        )
        if not bridge_enabled:
            runtime_metrics.observe_request(
                "qq_napcat_event",
                duration_ms=(time.perf_counter() - started_at) * 1000,
                ok=True,
            )
            return JSONResponse({"status": "disabled", "message": "QQ bridge is disabled"})

        try:
            event = await request.json()
            if not isinstance(event, dict):
                runtime_metrics.observe_request(
                    "qq_napcat_event",
                    duration_ms=(time.perf_counter() - started_at) * 1000,
                    ok=False,
                )
                return JSONResponse(
                    {"ok": False, "status": "invalid_event", "reason": "qq_event_must_be_object"},
                    status_code=400,
                )

            if channel_config is not None:
                identity = channel_config.authorize_event_identity(event)
                if not identity.ok:
                    runtime_metrics.observe_request(
                        "qq_napcat_event",
                        duration_ms=(time.perf_counter() - started_at) * 1000,
                        ok=False,
                    )
                    return JSONResponse(
                        {"ok": False, "status": "forbidden", "reason": identity.reason},
                        status_code=identity.status_code,
                    )

        except Exception as exc:
            return event_handler.failure_response(exc, started_at=started_at)
        return await event_handler.handle(request=request, event=event, started_at=started_at)

    return router
