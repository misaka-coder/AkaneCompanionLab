"""QQ HTTP endpoints: authentication, request parsing and response metrics."""
from __future__ import annotations

import re
import time
from typing import Any, Callable

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from companion_v01.channels.qq.assembly import build_qq_event_handler
from companion_v01.deployment_security import AdminWriteAuth, QQChannelRuntimeConfig

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
    router = APIRouter()
    qq_route_base = _normalize_qq_route_base(route_base)
    diagnostic_auth = admin_auth or AdminWriteAuth.local_compatibility()
    event_handler = build_qq_event_handler(
        engine=engine, config_module=config_module, qq_gateway=qq_gateway,
        runtime_metrics=runtime_metrics, logger=logger, log_event=log_event,
        tts_client=tts_client, settings=settings, async_task_supervisor=async_task_supervisor,
        plugin_command_broker_provider=plugin_command_broker_provider,
        plugin_event_broker_provider=plugin_event_broker_provider,
        plugin_agent_event_handler_registrar=plugin_agent_event_handler_registrar,
        plugin_conversation_ref_issuer=plugin_conversation_ref_issuer,
        plugin_turn_router=plugin_turn_router, thinking_mode_setter=thinking_mode_setter,
        turn_coordinator=turn_coordinator, session_work_queue=session_work_queue,
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
