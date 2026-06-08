from __future__ import annotations

import asyncio
import re
import time
from typing import Any, Callable

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse


LogEvent = Callable[..., None]

QQ_REPLY_OBJECT_TERMS = ("工作台", "文件", "结果", "成果", "产物", "音频", "视频", "人声", "伴奏", "任务")
QQ_REPLY_ACTION_TERMS = (
    "清理",
    "清空",
    "清除",
    "收拾",
    "干净",
    "删除",
    "归档",
    "发",
    "发送",
    "转",
    "转换",
    "转成",
    "分离",
    "拆",
    "完成",
    "做好",
)


def _normalize_reply_text(value: Any) -> str:
    return " ".join(str(value or "").replace("\r\n", "\n").replace("\r", "\n").split()).strip()


def _reply_similarity(left: str, right: str) -> float:
    left_text = re.sub(r"[\s，。！？!?~～、,.]+", "", str(left or "").strip().lower())
    right_text = re.sub(r"[\s，。！？!?~～、,.]+", "", str(right or "").strip().lower())
    if not left_text or not right_text:
        return 0.0
    if left_text in right_text or right_text in left_text:
        return min(len(left_text), len(right_text)) / max(len(left_text), len(right_text))
    if len(left_text) < 4 or len(right_text) < 4:
        return 0.0
    left_grams = {left_text[index : index + 2] for index in range(len(left_text) - 1)}
    right_grams = {right_text[index : index + 2] for index in range(len(right_text) - 1)}
    if not left_grams or not right_grams:
        return 0.0
    return len(left_grams & right_grams) / len(left_grams | right_grams)


def _is_similar_reply(left: str, right: str) -> bool:
    if _reply_similarity(left, right) >= 0.42:
        return True
    left_text = str(left or "")
    right_text = str(right or "")
    shared_objects = [term for term in QQ_REPLY_OBJECT_TERMS if term in left_text and term in right_text]
    shared_actions = [term for term in QQ_REPLY_ACTION_TERMS if term in left_text and term in right_text]
    return bool(shared_objects and shared_actions)


def _filter_unsent_reply_messages(messages: list[str], sent_messages: list[str]) -> list[str]:
    sent_normalized = {_normalize_reply_text(item) for item in sent_messages if _normalize_reply_text(item)}
    sent_joined = "".join(str(item or "").strip() for item in sent_messages if str(item or "").strip()).strip()
    sent_joined_normalized = _normalize_reply_text(sent_joined)
    unsent: list[str] = []
    for message in messages:
        text = str(message or "").strip()
        if not text:
            continue
        normalized = _normalize_reply_text(text)
        if normalized and normalized in sent_normalized:
            continue
        if sent_joined and text.startswith(sent_joined):
            text = text[len(sent_joined) :].strip()
            normalized = _normalize_reply_text(text)
            if not normalized:
                continue
        elif sent_joined_normalized and normalized == sent_joined_normalized:
            continue
        trimmed_by_sent_prefix = False
        for sent_item in sent_messages:
            sent_text = str(sent_item or "").strip()
            if sent_text and text.startswith(sent_text):
                text = text[len(sent_text) :].strip()
                normalized = _normalize_reply_text(text)
                trimmed_by_sent_prefix = True
                break
        if trimmed_by_sent_prefix:
            if not normalized:
                continue
            generic_tail = re.sub(r"[\s，。！？!?~～、,.]+", "", text)
            if len(generic_tail) < 10:
                continue
        if any(_is_similar_reply(text, sent_item) for sent_item in sent_messages):
            continue
        unsent.append(text)
    return unsent


def _send_pending_stage_messages(
    *,
    qq_gateway: Any,
    context: Any,
    pending_messages: list[str],
    streamed_messages: list[str],
    stream_send_results: list[dict[str, Any]],
    max_streamed: int,
) -> list[str]:
    for text in pending_messages:
        if len(streamed_messages) >= max_streamed:
            break
        normalized = _normalize_reply_text(text)
        if not normalized or normalized in {_normalize_reply_text(item) for item in streamed_messages}:
            continue
        if any(_is_similar_reply(text, sent_item) for sent_item in streamed_messages):
            continue
        result = qq_gateway.send_reply(context, text[:1800].strip())
        streamed_messages.append(text)
        stream_send_results.append(result)
    return []


def _process_qq_turn_streaming(
    *,
    engine: Any,
    qq_gateway: Any,
    context: Any,
    turn_payload: dict[str, Any],
    config_module: Any,
) -> dict[str, Any]:
    pending_stage_messages: list[str] = []
    streamed_messages: list[str] = []
    stream_send_results: list[dict[str, Any]] = []
    frame: dict[str, Any] = {}
    max_streamed = max(0, min(20, int(getattr(config_module, "QQ_STREAM_MAX_SEGMENTS", getattr(config_module, "QQ_REPLY_MAX_SEGMENTS", 8)) or 0)))
    stream_enabled = bool(getattr(config_module, "QQ_STREAM_REPLIES_ENABLED", True)) and max_streamed > 0

    for stream_event in engine.process_turn_stream(turn_payload):
        if not isinstance(stream_event, dict):
            continue
        event_type = str(stream_event.get("type") or "").strip()
        if event_type == "speech_segment" and stream_enabled:
            text = str(stream_event.get("text") or "").strip()
            if not text:
                continue
            normalized = _normalize_reply_text(text)
            if not normalized or normalized in {_normalize_reply_text(item) for item in pending_stage_messages}:
                continue
            pending_stage_messages.append(text)
            continue
        if event_type == "assistant_stage_decision" and pending_stage_messages:
            pending_stage_messages = _send_pending_stage_messages(
                qq_gateway=qq_gateway,
                context=context,
                pending_messages=pending_stage_messages,
                streamed_messages=streamed_messages,
                stream_send_results=stream_send_results,
                max_streamed=max_streamed,
            )
            continue
        if event_type == "final_ui" and isinstance(stream_event.get("payload"), dict):
            frame = dict(stream_event.get("payload") or {})

    if stream_enabled and pending_stage_messages and not frame:
        pending_stage_messages = _send_pending_stage_messages(
            qq_gateway=qq_gateway,
            context=context,
            pending_messages=pending_stage_messages,
            streamed_messages=streamed_messages,
            stream_send_results=stream_send_results,
            max_streamed=max_streamed,
        )

    if not frame and not streamed_messages:
        frame = engine.process_turn(turn_payload)

    reply_messages = qq_gateway.render_reply_messages(frame)
    unsent_reply_messages = _filter_unsent_reply_messages(reply_messages, streamed_messages)
    send_result = qq_gateway.send_replies(context, unsent_reply_messages)
    if streamed_messages:
        combined_results = [*stream_send_results, *list(send_result.get("results") or [])]
        send_result = {
            "ok": all(bool(item.get("ok")) for item in combined_results) if combined_results else True,
            "count": len(combined_results),
            "streamed_count": len(streamed_messages),
            "deferred_count": len(unsent_reply_messages),
            "results": combined_results,
        }

    file_send_result = qq_gateway.send_generated_files(
        context,
        list(frame.get("tool_events") or []),
    )
    sticker_send_result = qq_gateway.send_stickers(
        context,
        list(frame.get("tool_events") or []),
    )
    return {
        "frame": frame,
        "reply_messages": [*streamed_messages, *unsent_reply_messages],
        "send_result": send_result,
        "file_send_result": file_send_result,
        "sticker_send_result": sticker_send_result,
    }


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

            character_command_result = qq_gateway.handle_character_command(
                context,
                character_resource_service=getattr(engine, "desktop_pet_character_resources", None),
            )
            if isinstance(character_command_result, dict):
                reply = str(character_command_result.get("reply") or "").strip()
                send_result = qq_gateway.send_reply(context, reply) if reply else {"ok": False, "reason": "empty_reply"}
                duration_ms = (time.perf_counter() - started_at) * 1000
                runtime_metrics.observe_request(
                    "qq_napcat_event",
                    duration_ms=duration_ms,
                    ok=bool(send_result.get("ok")),
                )
                log_event(
                    "qq_character_command",
                    session_id=context.session_id,
                    profile_user_id=context.profile_user_id,
                    command_status=str(character_command_result.get("status") or ""),
                    command_ok=bool(character_command_result.get("ok")),
                    character_pack_id=str(character_command_result.get("character_pack_id") or ""),
                    sent=bool(send_result.get("ok")),
                    duration_ms=round(duration_ms, 1),
                )
                return JSONResponse(
                    {
                        "status": "ok" if send_result.get("ok") else "send_failed",
                        "reason": "qq_character_command",
                        "command_status": str(character_command_result.get("status") or ""),
                        "command_ok": bool(character_command_result.get("ok")),
                        "session_id": context.session_id,
                        "profile_user_id": context.profile_user_id,
                        "character_pack_id": str(character_command_result.get("character_pack_id") or ""),
                        "send_result": send_result,
                    }
                )

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
                                "character_pack_id": str(getattr(context, "character_pack_id", "") or ""),
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
            turn_result = await asyncio.to_thread(
                _process_qq_turn_streaming,
                engine=engine,
                qq_gateway=qq_gateway,
                context=context,
                turn_payload=turn_payload,
                config_module=config_module,
            )
            frame = dict(turn_result.get("frame") or {})
            reply_messages = list(turn_result.get("reply_messages") or [])
            send_result = dict(turn_result.get("send_result") or {"ok": False, "reason": "missing_send_result", "results": []})
            file_send_result = dict(turn_result.get("file_send_result") or {"ok": True, "count": 0, "results": []})
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
            character_pack_id=str(getattr(context, "character_pack_id", "") or ""),
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
                "character_pack_id": str(getattr(context, "character_pack_id", "") or ""),
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
