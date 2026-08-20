from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

import config
from ..desktop_pet_contract import DESKTOP_PET_CONTRACT_VERSION, build_desktop_pet_error_payload
from ..turn_coordination import TurnCoordinator

logger = logging.getLogger("akane.think")


LogEvent = Callable[..., None]


def build_think_router(
    *,
    engine: Any,
    public_guard: Any,
    runtime_metrics: Any,
    log_event: LogEvent,
    turn_coordinator: Any = None,
) -> APIRouter:
    router = APIRouter()
    turn_coordinator = turn_coordinator or TurnCoordinator()

    def _turn_identity(payload: dict[str, Any]) -> tuple[str, str, str]:
        session_id = str(payload.get("user_id") or payload.get("session_id") or "default_session")
        profile_user_id = str(payload.get("real_user_id") or session_id)
        actor_id = str(payload.get("actor_stable_id") or f"desktop:{profile_user_id}").strip()
        return profile_user_id, session_id, actor_id

    async def _control_payload(request: Request) -> dict[str, Any] | JSONResponse:
        try:
            payload = await request.json()
        except Exception as exc:
            return JSONResponse(
                build_desktop_pet_error_payload(
                    error="invalid_json",
                    message=f"无法读取控制请求：{str(exc)[:160]}",
                    retryable=False,
                ),
                status_code=400,
                headers={"Cache-Control": "no-store"},
            )
        if not isinstance(payload, dict):
            return JSONResponse(
                build_desktop_pet_error_payload(
                    error="invalid_payload",
                    message="turn control payload must be a JSON object",
                    retryable=False,
                ),
                status_code=400,
                headers={"Cache-Control": "no-store"},
            )
        return payload

    @router.post("/think/steer")
    async def steer_turn(request: Request):
        payload = await _control_payload(request)
        if isinstance(payload, JSONResponse):
            return payload
        profile_user_id, session_id, actor_id = _turn_identity(payload)
        result = turn_coordinator.offer_steer(
            profile_user_id=profile_user_id,
            session_id=session_id,
            actor_id=actor_id,
            content=payload.get("message"),
            timestamp=int(payload.get("timestamp") or time.time()),
            actor_display_name=payload.get("actor_display_name"),
            channel="desktop_pet",
        )
        status_code = 202 if result.get("ok") else (409 if result.get("status") == "finalizing" else 404)
        return JSONResponse(result, status_code=status_code, headers={"Cache-Control": "no-store"})

    @router.post("/think/stop")
    async def stop_turn(request: Request):
        payload = await _control_payload(request)
        if isinstance(payload, JSONResponse):
            return payload
        profile_user_id, session_id, actor_id = _turn_identity(payload)
        result = turn_coordinator.request_stop(
            profile_user_id=profile_user_id,
            session_id=session_id,
            actor_id=actor_id,
        )
        status_code = 202 if result.get("ok") else (409 if result.get("status") == "finalizing" else 404)
        return JSONResponse(result, status_code=status_code, headers={"Cache-Control": "no-store"})

    @router.post("/think")
    async def think(request: Request):
        try:
            payload = await request.json()
        except Exception as exc:
            return JSONResponse(
                build_desktop_pet_error_payload(
                    error="invalid_json",
                    message=f"无法读取 /think 请求：{str(exc)[:160]}",
                    retryable=False,
                ),
                status_code=400,
                headers={"Cache-Control": "no-store"},
            )
        if not isinstance(payload, dict):
            return JSONResponse(
                build_desktop_pet_error_payload(
                    error="invalid_payload",
                    message="/think payload must be a JSON object",
                    retryable=False,
                ),
                status_code=400,
                headers={"Cache-Control": "no-store"},
            )
        guard_decision = public_guard.try_acquire()
        if not guard_decision.allowed:
            runtime_metrics.incr("public_guard_blocked_total", 1)
            runtime_metrics.incr(f"public_guard_blocked_{guard_decision.reason}_total", 1)
            log_event(
                "public_guard_blocked",
                route="think",
                reason=guard_decision.reason,
                message=guard_decision.message,
                session_id=str(payload.get("user_id") or ""),
            )
            raise HTTPException(status_code=429, detail=guard_decision.message)

        def _advance_stream(stream: Any) -> tuple[bool, Any]:
            try:
                return True, next(stream)
            except StopIteration:
                return False, None

        async def _stream_active():
            started_at = time.perf_counter()
            partial = {
                "emotion": "",
                "speech": "",
                "event_count": 0,
            }
            turn_stream = None
            ok = False
            disconnected = False
            yield json.dumps(
                {
                    "type": "stream_start",
                    "contract_version": DESKTOP_PET_CONTRACT_VERSION,
                },
                ensure_ascii=False,
            ) + "\n"
            try:
                # Keep an explicit handle so a client-side abort closes the
                # engine stream in the same response cleanup path.  Relying on
                # generator garbage collection can inject GeneratorExit into
                # the inner stream from a different context, leaving an open
                # MemCore turn behind for the next desktop-pet message.
                turn_stream = engine.process_turn_stream(payload)
                while True:
                    has_event, event = await asyncio.to_thread(_advance_stream, turn_stream)
                    if not has_event:
                        break
                    partial["event_count"] = int(partial.get("event_count", 0)) + 1
                    event_type = str(event.get("type") or "")
                    if event_type == "ui":
                        partial["emotion"] = str(event.get("emotion") or partial.get("emotion") or "")
                    elif event_type == "speech_chunk":
                        partial["speech"] = str(partial.get("speech") or "") + str(event.get("text") or "")
                    if str(event.get("type") or "") == "final":
                        final_payload = event.get("payload")
                        if isinstance(final_payload, dict):
                            print_debug(payload, final_payload)
                            log_turn_result(
                                "think",
                                payload,
                                final_payload,
                                (time.perf_counter() - started_at) * 1000,
                                log_event=log_event,
                            )
                            partial["emotion"] = str(final_payload.get("emotion") or partial.get("emotion") or "")
                            partial["speech"] = str(final_payload.get("speech") or partial.get("speech") or "")
                    yield json.dumps(event, ensure_ascii=False) + "\n"
                ok = True
            except (GeneratorExit, asyncio.CancelledError):
                disconnected = True
                raise
            except Exception as exc:
                yield json.dumps(
                    {
                        "type": "stream_error",
                        "contract_version": DESKTOP_PET_CONTRACT_VERSION,
                        "error": "think_stream_failed",
                        "message": f"stream failed: {exc}",
                        "retryable": True,
                        "partial": partial,
                    },
                    ensure_ascii=False,
                ) + "\n"
                log_event("think_stream_error", session_id=str(payload.get("user_id") or ""), message=str(exc), partial=partial)
            finally:
                close_stream = getattr(turn_stream, "close", None)
                if callable(close_stream):
                    try:
                        close_stream()
                    except Exception as exc:
                        logger.warning("think stream cleanup failed: %s", type(exc).__name__)
                if guard_decision.acquired:
                    public_guard.release()
                duration_ms = (time.perf_counter() - started_at) * 1000
                runtime_metrics.observe_request("think_stream", duration_ms=duration_ms, ok=ok)
                if not disconnected:
                    yield json.dumps(
                        {
                            "type": "stream_end",
                            "contract_version": DESKTOP_PET_CONTRACT_VERSION,
                            "status": "ok" if ok else "error",
                            "partial": partial,
                        },
                        ensure_ascii=False,
                    ) + "\n"

        async def _stream():
            profile_user_id, session_id, actor_id = _turn_identity(payload)
            async with turn_coordinator.hold(
                profile_user_id,
                session_id,
                actor_id=actor_id,
                channel="desktop_pet",
            ) as turn_control_id:
                payload["_turn_control_id"] = turn_control_id
                async for chunk in _stream_active():
                    yield chunk

        return StreamingResponse(
            _stream(),
            media_type="application/x-ndjson",
            headers={
                "Cache-Control": "no-store",
                "X-Akane-Contract": DESKTOP_PET_CONTRACT_VERSION,
                # Tell nginx not to buffer streamed NDJSON chunks; otherwise UI
                # state like BGM / choices may appear only after a refresh.
                "X-Accel-Buffering": "no",
            },
        )

    @router.post("/think_once")
    async def think_once(request: Request):
        started_at = time.perf_counter()
        try:
            payload = await request.json()
        except Exception as exc:
            runtime_metrics.observe_request("think_once", duration_ms=(time.perf_counter() - started_at) * 1000, ok=False)
            return JSONResponse(
                build_desktop_pet_error_payload(
                    error="invalid_json",
                    message=f"无法读取 /think_once 请求：{str(exc)[:160]}",
                    retryable=False,
                ),
                status_code=400,
                headers={"Cache-Control": "no-store"},
            )
        if not isinstance(payload, dict):
            runtime_metrics.observe_request("think_once", duration_ms=(time.perf_counter() - started_at) * 1000, ok=False)
            return JSONResponse(
                build_desktop_pet_error_payload(
                    error="invalid_payload",
                    message="/think_once payload must be a JSON object",
                    retryable=False,
                ),
                status_code=400,
                headers={"Cache-Control": "no-store"},
            )
        guard_decision = public_guard.try_acquire()
        if not guard_decision.allowed:
            runtime_metrics.incr("public_guard_blocked_total", 1)
            runtime_metrics.incr(f"public_guard_blocked_{guard_decision.reason}_total", 1)
            log_event(
                "public_guard_blocked",
                route="think_once",
                reason=guard_decision.reason,
                message=guard_decision.message,
                session_id=str(payload.get("user_id") or ""),
            )
            raise HTTPException(status_code=429, detail=guard_decision.message)
        profile_user_id, session_id, actor_id = _turn_identity(payload)
        try:
            async with turn_coordinator.hold(
                profile_user_id,
                session_id,
                actor_id=actor_id,
                channel="desktop_pet",
            ) as turn_control_id:
                payload["_turn_control_id"] = turn_control_id
                frame = await asyncio.to_thread(engine.process_turn, payload)
        except Exception as exc:
            duration_ms = (time.perf_counter() - started_at) * 1000
            runtime_metrics.observe_request("think_once", duration_ms=duration_ms, ok=False)
            log_event("think_once_error", session_id=str(payload.get("user_id") or ""), message=str(exc))
            raise
        finally:
            if guard_decision.acquired:
                public_guard.release()
        print_debug(payload, frame)
        duration_ms = (time.perf_counter() - started_at) * 1000
        runtime_metrics.observe_request("think_once", duration_ms=duration_ms, ok=True)
        log_turn_result("think_once", payload, frame, duration_ms, log_event=log_event)
        return JSONResponse(frame)

    return router


def print_debug(payload: dict, frame: dict) -> None:
    # Only emit verbose debug output when at least one debug flag is enabled.
    # Without this gate, every /think and /think_once call dumps large JSON
    # blocks to stdout, making logs unreadable in normal operation.
    router_debug = bool(getattr(config, "ROUTER_DEBUG", False))
    verifier_debug = bool(getattr(config, "VERIFIER_DEBUG", False))
    final_debug = bool(getattr(config, "FINAL_DEBUG", False))
    if not (router_debug or verifier_debug or final_debug):
        return

    debug = frame.get("_debug") or {}
    retrieval_debug = debug.get("retrieval_result") or {}
    memory_snippets = list(retrieval_debug.get("memory_snippets") or [])
    selected_memory_snippets = list(retrieval_debug.get("selected_memory_snippets") or [])

    out: list[str] = []
    out.append("")
    out.append("=" * 18 + " Aihong Companion V0.1 " + "=" * 18)
    out.append(f"session_id: {payload.get('user_id', '')}")
    out.append(f"user_text: {payload.get('message', '')}")
    out.append("")
    out.append("=== 前置检索控制输出 ===")
    out.append(json.dumps(debug.get("router_output", {}), ensure_ascii=False, indent=2))
    out.append("")
    out.append("=== 前置检索控制耗时 ===")
    out.append(json.dumps(debug.get("router_timing", {}), ensure_ascii=False, indent=2))
    out.append("")
    out.append("=== 检索结果摘要 ===")
    out.append(json.dumps(debug.get("retrieval_result", {}), ensure_ascii=False, indent=2))
    out.append("")
    out.append("=== 检索校验输出 ===")
    out.append(json.dumps(debug.get("verifier_output", {}), ensure_ascii=False, indent=2))
    out.append("")
    out.append("=== 检索校验耗时 ===")
    out.append(json.dumps(debug.get("verifier_timing", {}), ensure_ascii=False, indent=2))
    out.append("")
    if debug.get("memory_tool"):
        out.append("=== Akane 主动记忆检索工具 ===")
        out.append(json.dumps(debug.get("memory_tool", {}), ensure_ascii=False, indent=2))
        out.append("")
    out.append("=== 检索片段（按编号） ===")
    if memory_snippets:
        for index, snippet in enumerate(memory_snippets, start=1):
            out.append(f"[{index}]")
            out.append(str(snippet))
            out.append("")
    else:
        out.append("(无)")
    out.append("=== 被选中的记忆片段 ===")
    if selected_memory_snippets:
        for item in selected_memory_snippets:
            label = f"[{item.get('index')}]" if item.get("index") is not None else "[fallback]"
            out.append(label)
            out.append(str(item.get("snippet") or ""))
            out.append("")
    else:
        out.append("(无)")
    out.append("")
    out.append("=== 最终回复 JSON ===")
    visible: dict[str, object] = {}
    for key in (
        "thought",
        "emotion",
        "speech",
        "speech_segments",
        "tool_call",
        "code_snippet",
        "status",
        "choices",
        "npc_turns",
        "client_mode",
        "client",
        "character",
        "scene",
        "persona",
        "memory_metadata",
    ):
        if key == "thought" and key not in frame:
            continue
        visible[key] = frame.get(key)
    out.append(json.dumps(visible, ensure_ascii=False, indent=2))
    out.append("=" * 48)

    logger.info("\n".join(out))


def log_turn_result(
    route: str,
    payload: dict,
    frame: dict,
    duration_ms: float,
    *,
    log_event: LogEvent,
) -> None:
    debug = frame.get("_debug") or {}
    log_event(
        "turn_complete",
        route=route,
        session_id=str(payload.get("user_id") or ""),
        trace_id=str(frame.get("trace_id") or ""),
        duration_ms=round(float(duration_ms), 1),
        router_ready_at_ms=debug.get("router_timing", {}).get("ready_at_ms"),
        verifier_mode=debug.get("verifier_timing", {}).get("mode"),
        final_status=str(frame.get("status") or ""),
        emotion=str(frame.get("emotion") or ""),
    )
