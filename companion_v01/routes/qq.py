from __future__ import annotations

import asyncio
import time
from typing import Any, Callable

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse


LogEvent = Callable[..., None]


def build_qq_router(
    *,
    engine: Any,
    config_module: Any,
    qq_gateway: Any,
    runtime_metrics: Any,
    logger: Any,
    log_event: LogEvent,
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/qq/napcat/status")
    async def qq_napcat_status() -> JSONResponse:
        return JSONResponse({"status": "ok", "data": qq_gateway.status()})

    @router.post("/api/qq/napcat/event")
    async def qq_napcat_event(request: Request) -> JSONResponse:
        started_at = time.perf_counter()
        event: dict = {}
        context = None
        try:
            event = await request.json()
            if not bool(getattr(config_module, "QQ_BRIDGE_ENABLED", False)):
                runtime_metrics.observe_request(
                    "qq_napcat_event",
                    duration_ms=(time.perf_counter() - started_at) * 1000,
                    ok=True,
                )
                return JSONResponse({"status": "disabled", "message": "QQ bridge is disabled"})

            context = qq_gateway.build_message_context(event)
            if not context.should_respond:
                runtime_metrics.observe_request(
                    "qq_napcat_event",
                    duration_ms=(time.perf_counter() - started_at) * 1000,
                    ok=True,
                )
                return JSONResponse({"status": "ignored", "reason": context.reason})

            attachments_registered = []
            if context.attachments:
                attachments_registered = await asyncio.to_thread(
                    engine.ingest_qq_attachments,
                    profile_user_id=context.profile_user_id,
                    session_id=context.session_id,
                    attachments=list(context.attachments),
                    timestamp=int(event.get("time") or time.time()),
                )
                attachment_ids = [
                    str(item.get("attachment_id") or "").strip()
                    for item in attachments_registered
                    if isinstance(item, dict) and str(item.get("attachment_id") or "").strip()
                ]
                debounce_token = qq_gateway.register_attachment_debounce(context, attachment_ids=attachment_ids)
                if bool(debounce_token.get("enabled")):
                    await asyncio.sleep(float(debounce_token.get("delay_seconds") or 0.0))
                    debounce_result = qq_gateway.consume_attachment_debounce(debounce_token)
                    if not bool(debounce_result.get("process")):
                        duration_ms = (time.perf_counter() - started_at) * 1000
                        runtime_metrics.observe_request("qq_napcat_event", duration_ms=duration_ms, ok=True)
                        log_event(
                            "qq_napcat_event_buffered",
                            session_id=context.session_id,
                            profile_user_id=context.profile_user_id,
                            reason=str(debounce_result.get("reason") or "attachment_debounce"),
                            attachment_count=len(context.attachments or []),
                            attachments_registered=len(attachments_registered),
                            duration_ms=round(duration_ms, 1),
                        )
                        return JSONResponse(
                            {
                                "status": "buffered",
                                "reason": str(debounce_result.get("reason") or "attachment_debounce"),
                                "session_id": context.session_id,
                                "profile_user_id": context.profile_user_id,
                                "attachments_registered": len(attachments_registered),
                            }
                        )
                    attachment_ids = [
                        str(item or "").strip()
                        for item in list(debounce_result.get("attachment_ids") or attachment_ids)
                        if str(item or "").strip()
                    ]
                if attachment_ids:
                    await asyncio.to_thread(
                        engine.wait_for_qq_attachments_settled,
                        profile_user_id=context.profile_user_id,
                        session_id=context.session_id,
                        attachment_ids=attachment_ids,
                        timeout_seconds=float(getattr(config_module, "QQ_ATTACHMENT_READY_WAIT_SECONDS", 8.0) or 0.0),
                    )

            turn_payload = context.to_turn_payload()
            remote_prefetch_result = await asyncio.to_thread(
                engine.prefetch_remote_media_links_for_message,
                profile_user_id=context.profile_user_id,
                session_id=context.session_id,
                message=context.clean_message or context.raw_message,
                timestamp=int(event.get("time") or time.time()),
            )
            if isinstance(remote_prefetch_result, dict) and str(remote_prefetch_result.get("followup_context") or "").strip():
                prefetch_context = str(remote_prefetch_result.get("followup_context") or "").strip()
                original_extra_context = str(turn_payload.get("extra_context") or "").strip()
                turn_payload["extra_context"] = "\n\n".join(
                    part
                    for part in (
                        original_extra_context,
                        "【链接素材预处理结果】\n" + prefetch_context,
                    )
                    if part
                )
            frame = await asyncio.to_thread(engine.process_turn, turn_payload)
            reply_messages = qq_gateway.render_reply_messages(frame)
            reply_text = "\n".join(reply_messages).strip()
            send_result = await asyncio.to_thread(qq_gateway.send_replies, context, reply_messages)
            file_send_result = await asyncio.to_thread(
                qq_gateway.send_generated_files,
                context,
                list(frame.get("tool_events") or []),
            )
            await asyncio.to_thread(
                qq_gateway.send_stickers,
                context,
                list(frame.get("tool_events") or []),
            )
            for item in list(file_send_result.get("results") or []):
                generated_id = str(item.get("generated_id") or "").strip()
                if not generated_id:
                    continue
                await asyncio.to_thread(
                    engine.mark_generated_file_delivery,
                    profile_user_id=context.profile_user_id,
                    session_id=context.session_id,
                    generated_id=generated_id,
                    delivery_status="sent" if item.get("ok") else "failed",
                    timestamp=int(time.time()),
                )
        except Exception as exc:
            duration_ms = (time.perf_counter() - started_at) * 1000
            runtime_metrics.observe_request("qq_napcat_event", duration_ms=duration_ms, ok=False)
            logger.exception("qq_napcat_event failed")
            log_event(
                "qq_napcat_event_error",
                session_id=getattr(context, "session_id", ""),
                profile_user_id=getattr(context, "profile_user_id", ""),
                post_type=str(event.get("post_type") or "") if isinstance(event, dict) else "",
                message_type=str(event.get("message_type") or "") if isinstance(event, dict) else "",
                reason=str(exc),
                duration_ms=round(duration_ms, 1),
            )
            return JSONResponse(
                {
                    "status": "error",
                    "reason": "qq_event_processing_failed",
                    "message": str(exc)[:500],
                }
            )

        duration_ms = (time.perf_counter() - started_at) * 1000
        runtime_metrics.observe_request("qq_napcat_event", duration_ms=duration_ms, ok=bool(send_result.get("ok")))
        log_event(
            "qq_napcat_event",
            session_id=context.session_id,
            profile_user_id=context.profile_user_id,
            reason=context.reason,
            sent=bool(send_result.get("ok")),
            attachment_count=len(context.attachments or []),
            attachments_registered=len(attachments_registered),
            duration_ms=round(duration_ms, 1),
        )
        return JSONResponse(
            {
                "status": "ok" if send_result.get("ok") else "send_failed",
                "reason": context.reason,
                "session_id": context.session_id,
                "profile_user_id": context.profile_user_id,
                # NapCat / OneBot HTTP report supports "quick operation" replies.
                # Do not include a "reply" field here, or it may send a second
                # aggregated message after our explicit send_replies() call.
                "sent_count": len(reply_messages),
                "attachment_count": len(context.attachments or []),
                "attachments_registered": len(attachments_registered),
                "send_result": send_result,
                "file_send_result": file_send_result,
            }
        )

    return router
