"""One QQ model stream: staged replies, artifact delivery and final outcome recording."""

from __future__ import annotations

import time
from typing import Any, Callable

from companion_v01.channels.qq.delivery.artifacts import (
    _hydrate_plugin_managed_artifact_events,
    _qq_file_delivery_event_identity,
    _qq_has_visible_action_receipt,
)
from companion_v01.channels.qq.delivery.replies import (
    _load_qq_delivery_config,
    _normalize_reply_medium,
    _send_qq_delivery,
    _send_qq_emotion_image_fallback,
    _streaming_allows_text,
    _streaming_allows_tool_preface,
)
from companion_v01.channels.qq.delivery.stream import (
    _coalesce_deferred_stream_messages,
    _filter_unsent_reply_messages,
    _send_pending_stage_messages,
    _StageSpeechReplay,
)
from companion_v01.channels.qq.formatting import _normalize_reply_text
from companion_v01.channels.qq.resources import (
    qq_current_outfit_id_from_turn_payload as _qq_current_outfit_id_from_turn_payload,
)
from companion_v01.plugin_text_presentation import apply_plugin_text_presentation_policy
from companion_v01.turn_coordination import cancellation_requested


def _process_qq_turn_streaming(
    *,
    engine: Any,
    qq_gateway: Any,
    context: Any,
    turn_payload: dict[str, Any],
    config_module: Any,
    tts_client: Any = None,
    settings: Any = None,
    prepared_frame: dict[str, Any] | None = None,
    additional_delivery_events: list[dict[str, Any]] | Callable | None = None,
    file_target_allowed: Callable | None = None,
    begin_attention_reply: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    timing_started_at = time.perf_counter()
    timing: dict[str, float] = {}
    # Add live, Bot-bound session identity only when a queued/direct/ambient
    # turn actually runs. Do not rewrite its saved message or quoted history.
    turn_payload = dict(turn_payload)
    turn_payload.pop("_host_dialogue_target", None)
    target = getattr(context, "dialogue_target", None)
    if target is not None:
        from companion_v01.dialogue_targets import registry_for
        registry_for(engine).validate(target)
        turn_payload["_host_dialogue_target"] = target
        turn_payload["character_pack_id"] = target.memory_domain
    identity_builder = getattr(qq_gateway, "build_group_identity_context", None)
    if bool(getattr(context, "is_group", False)) and callable(identity_builder):
        identity_context = identity_builder(int(getattr(context, "group_id", 0) or 0))
        turn_payload["extra_context"] = "\n\n".join(
            part for part in (identity_context, str(turn_payload.get("extra_context") or "").strip()) if part
        )
    pending_stage_messages: list[str] = []
    deferred_stage_messages: list[str] = []
    streamed_messages: list[str] = []
    stream_send_results: list[dict[str, Any]] = []
    streamed_delivery_events: list[dict[str, Any]] = []
    frame: dict[str, Any] = dict(prepared_frame) if prepared_frame is not None else {}
    final_frame_received = prepared_frame is not None
    model_status = "not_requested"
    tool_preface_delivered = False
    final_streamed_text_delivered = False
    current_stage_streamed_count = 0
    stage_replay = _StageSpeechReplay()
    tool_delivered_messages: dict[str, str] = {}

    def remember_tool_delivery(event: dict[str, Any]) -> None:
        if (event.get("type") != "qq_visible_action_receipt" or event.get("ok") is not True
                or event.get("status") != "success"):
            return
        text = event.get("current_conversation_text")
        if isinstance(text, str) and text.strip():
            key = str(event.get("message_id") or text)
            if key not in tool_delivered_messages:
                tool_delivered_messages[key] = text
                stage_replay.remember_delivered(text)
    single_message_delivery = (
        str(turn_payload.get("plugin_text_delivery") or "").strip().lower() == "single_message"
    )
    active_reply_mode = (
        _normalize_reply_medium(getattr(context, "reply_mode", ""))
        or _normalize_reply_medium(qq_gateway.resolve_reply_mode(getattr(context, "session_id", "")))
        or "text"
    )
    max_streamed = max(
        0,
        min(
            100,
            int(
                getattr(
                    config_module,
                    "QQ_STREAM_IMMEDIATE_SEGMENTS",
                    0,
                )
                or 0
            ),
        ),
    )
    # Streaming enablement and an optional transport quota are separate
    # decisions.  Zero means no quota; it must not silently disable the
    # model-authored progress stream.
    stream_enabled = bool(getattr(config_module, "QQ_STREAM_REPLIES_ENABLED", True)) and not single_message_delivery

    engine_started_at = time.perf_counter()
    run_model = prepared_frame is None and not cancellation_requested()
    if run_model:
        model_status = "running"
    stream_events = engine.process_turn_stream(turn_payload) if run_model else ()
    for stream_event in stream_events:
        if not isinstance(stream_event, dict):
            continue
        event_type = str(stream_event.get("type") or "").strip()
        remember_tool_delivery(stream_event)
        if begin_attention_reply is not None:
            if cancellation_requested():
                close_stream = getattr(stream_events, "close", None)
                if callable(close_stream):
                    close_stream()
                break
            candidate = stream_event.get("payload") if event_type == "final_ui" else {}
            starts_reply = (
                event_type == "speech_segment" and stream_enabled
                and bool(str(stream_event.get("text") or "").strip())
                and _streaming_allows_tool_preface(active_reply_mode)
            ) or (isinstance(candidate, dict) and bool(
                str(candidate.get("speech") or "").strip() or _qq_has_visible_action_receipt(candidate.get("tool_events"))
            ))
            if starts_reply and not begin_attention_reply():
                continue
        if event_type in {"generated_file_ready", "file_ready", "file_unavailable"}:
            streamed_delivery_events.append(dict(stream_event))
        if event_type == "speech_segment" and stream_enabled:
            text = str(stream_event.get("text") or "").strip()
            if not text:
                continue
            if not _streaming_allows_tool_preface(active_reply_mode):
                continue
            text = stage_replay.push(text)
            normalized = _normalize_reply_text(text)
            if not normalized or normalized in {_normalize_reply_text(item) for item in pending_stage_messages}:
                continue
            pending_stage_messages.append(text)
            streamed_before = len(streamed_messages)
            pending_stage_messages = _send_pending_stage_messages(
                qq_gateway=qq_gateway,
                context=context,
                pending_messages=pending_stage_messages,
                deferred_messages=deferred_stage_messages,
                streamed_messages=streamed_messages,
                stream_send_results=stream_send_results,
                max_streamed=max_streamed,
            )
            current_stage_streamed_count += max(0, len(streamed_messages) - streamed_before)
            continue
        if event_type == "assistant_stage_decision":
            stage_replay.finish_stage()
            has_tool_call = bool(stream_event.get("has_tool_call"))
            if has_tool_call and current_stage_streamed_count:
                tool_preface_delivered = True
            elif not has_tool_call and current_stage_streamed_count:
                final_streamed_text_delivered = True
            current_stage_streamed_count = 0
            if not pending_stage_messages:
                continue
            text_allowed = (
                _streaming_allows_tool_preface(active_reply_mode)
                if has_tool_call
                else _streaming_allows_text(active_reply_mode)
            )
            if not text_allowed:
                if has_tool_call:
                    pending_stage_messages = []
                continue
            streamed_before = len(streamed_messages)
            pending_stage_messages = _send_pending_stage_messages(
                qq_gateway=qq_gateway,
                context=context,
                pending_messages=pending_stage_messages,
                deferred_messages=deferred_stage_messages,
                streamed_messages=streamed_messages,
                stream_send_results=stream_send_results,
                max_streamed=max_streamed,
            )
            if len(streamed_messages) > streamed_before:
                tool_preface_delivered = True
            continue
        if event_type == "turn_stopped":
            model_status = "stopped"
            frame = dict(stream_event.get("payload") or {})
            frame["_turn_stopped"] = True
            final_frame_received = True
            pending_stage_messages = []
            continue
        if event_type == "final_ui" and isinstance(stream_event.get("payload"), dict):
            frame = dict(stream_event.get("payload") or {})
            model_status = "failed" if frame.get("_transient_final_failure") else "stopped" if frame.get("status") == "stopped" else "completed"
            final_frame_received = True
            if (
                pending_stage_messages
                and bool(frame.get("_transient_final_failure"))
                and _streaming_allows_text(active_reply_mode)
            ):
                # The final contract failed after real speech was parsed.  Keep
                # that truthful partial delivery, while the generic fallback in
                # the transient frame remains suppressed below.
                pending_stage_messages = _send_pending_stage_messages(
                    qq_gateway=qq_gateway,
                    context=context,
                    pending_messages=pending_stage_messages,
                    deferred_messages=deferred_stage_messages,
                    streamed_messages=streamed_messages,
                    stream_send_results=stream_send_results,
                    max_streamed=max_streamed,
                )

    if (
        stream_enabled
        and pending_stage_messages
        and not frame
        and _streaming_allows_text(active_reply_mode)
    ):
        pending_stage_messages = _send_pending_stage_messages(
            qq_gateway=qq_gateway,
            context=context,
            pending_messages=pending_stage_messages,
            deferred_messages=deferred_stage_messages,
            streamed_messages=streamed_messages,
            stream_send_results=stream_send_results,
            max_streamed=max_streamed,
        )

    if prepared_frame is None and not frame and not streamed_messages and not cancellation_requested():
        frame = engine.process_turn(turn_payload)
        final_frame_received = bool(frame)
        model_status = "failed" if frame.get("_transient_final_failure") else "stopped" if frame.get("status") == "stopped" else "completed" if frame else "failed"
    # The generator includes provider calls, tool execution, and any streamed
    # QQ sends performed while it yields. Keep the label honest: this is not a
    # provider-only latency measurement.
    timing["turn_processing_ms"] = round((time.perf_counter() - engine_started_at) * 1000, 1)

    apply_plugin_text_presentation_policy(
        frame,
        strip_leading_addresses_from=turn_payload.get(
            "plugin_text_strip_leading_addresses",
            (),
        ),
    )

    if cancellation_requested():
        model_status = "stopped"
        frame = {"status": "stopped", "speech": "", "speech_segments": [], "tool_events": [],
                 "reason": str(frame.get("reason") or "user_stopped"),
                 "_turn_stopped": True, "_deliberate_silence": True}
        streamed_delivery_events = []
        final_frame_received = True
    extra_events = additional_delivery_events() if callable(additional_delivery_events) else additional_delivery_events
    frame_delivery_events = [*(frame.get("tool_events") if isinstance(frame.get("tool_events"), list) else []),
                             *(extra_events or [])]
    visible_action_delivered = _qq_has_visible_action_receipt(frame_delivery_events)
    for event in frame_delivery_events:
        if isinstance(event, dict):
            remember_tool_delivery(event)
    visible_action_delivered = visible_action_delivered or bool(tool_delivered_messages)
    deliberate_silence = bool(frame.get("_deliberate_silence") or frame.get("_notification_suppressed"))
    retained_frame_events = [
        dict(event)
        for event in frame_delivery_events
        if isinstance(event, dict)
        and str(event.get("type") or "").strip() not in {"generated_file_ready", "file_ready", "file_unavailable"}
    ]
    merged_file_events: list[dict[str, Any]] = []
    delivery_event_indexes: dict[tuple[str, str], int] = {}
    for raw_event in [*streamed_delivery_events, *frame_delivery_events]:
        if not isinstance(raw_event, dict):
            continue
        event = dict(raw_event)
        event_type = str(event.get("type") or "").strip()
        if event_type not in {"generated_file_ready", "file_ready", "file_unavailable"}:
            continue
        identity = _qq_file_delivery_event_identity(event)
        if identity:
            existing_index = delivery_event_indexes.get(identity)
            if existing_index is not None:
                existing = merged_file_events[existing_index]
                # One physical artifact may first appear as a generation
                # receipt (send_to_user=false) and later as an explicit
                # send_file handoff.  The delivery decision is the stronger,
                # later state and must not be discarded as a duplicate.
                if bool(event.get("send_to_user")) and not bool(existing.get("send_to_user")):
                    merged_file_events[existing_index] = event
                continue
            delivery_event_indexes[identity] = len(merged_file_events)
        merged_file_events.append(event)
    if merged_file_events:
        frame["tool_events"] = [*retained_frame_events, *merged_file_events]

    delivery_events, artifact_resolution_failures = _hydrate_plugin_managed_artifact_events(
        engine=engine,
        context=context,
        tool_events=list(frame.get("tool_events") or []),
    )
    file_delivery_started_at = time.perf_counter()
    file_send_result = qq_gateway.send_generated_files(
        context,
        delivery_events,
        **({"target_allowed": file_target_allowed} if file_target_allowed else {}),
    )
    if artifact_resolution_failures:
        delivery_results = [
            *list(file_send_result.get("results") or []),
            *artifact_resolution_failures,
        ]
        any_sent = any(bool(item.get("ok")) for item in delivery_results)
        file_send_result = {
            "ok": False,
            "status": "partial" if any_sent else "failed",
            "count": len(delivery_results),
            "results": delivery_results,
        }

    card_results = list(file_send_result.get("results") or [])
    shop_cards_only = bool(card_results) and all(item.get("source_type") == "shop_confirmation" for item in card_results)
    file_delivery_attempted = int(file_send_result.get("count") or 0) > 0
    file_delivery_failed = file_delivery_attempted and not bool(file_send_result.get("ok"))
    timing["file_delivery_ms"] = round((time.perf_counter() - file_delivery_started_at) * 1000, 1)
    # Deliver artifacts before the final text so transport feedback can follow
    # the model's completed reply.  An auxiliary delivery failure must not turn
    # a successfully completed LLM turn into an apparent system crash.
    deferred_stream_messages = _coalesce_deferred_stream_messages(
        [*deferred_stage_messages, *pending_stage_messages]
    )
    final_reply_messages = (
        []
        if bool(frame.get("_transient_final_failure") or deliberate_silence)
        else qq_gateway.render_reply_messages(frame)
    )
    if single_message_delivery and final_reply_messages:
        body = str(frame.get("speech") or "").strip() or "\n".join(final_reply_messages).strip()
        prefix = str(turn_payload.get("plugin_text_prefix") or "").strip()
        suffix = str(turn_payload.get("plugin_text_suffix") or "").strip()
        if prefix and body.startswith(prefix):
            body = body[len(prefix) :].lstrip()
        if suffix and body.endswith(suffix):
            body = body[: -len(suffix)].rstrip()
        final_reply_messages = ["\n".join(part for part in (prefix, body, suffix) if part)] if body else []
    reply_messages = final_reply_messages
    if streamed_messages and bool(frame.get("_transient_final_failure")):
        # A complete speech field may already have reached QQ before a malformed
        # JSON tail forces the final frame to its generic persona fallback. The
        # model's speech remains the sole body authority; never append that
        # host fallback as a second, contradictory bubble.
        unsent_reply_messages = deferred_stream_messages
    else:
        final_tail_messages = _filter_unsent_reply_messages(
            reply_messages,
            [*tool_delivered_messages.values(), *streamed_messages, *deferred_stream_messages],
        )
        unsent_reply_messages = [*deferred_stream_messages, *final_tail_messages]
    delivered_reply_messages = [*streamed_messages, *unsent_reply_messages]
    text_delivery_started_at = time.perf_counter()
    send_result = _send_qq_delivery(
        engine=engine,
        qq_gateway=qq_gateway,
        context=context,
        frame=frame,
        reply_messages=reply_messages,
        unsent_reply_messages=unsent_reply_messages,
        streamed_messages=streamed_messages,
        config_module=config_module,
        tts_client=tts_client,
        settings=settings,
    )
    timing["text_and_voice_delivery_ms"] = round((time.perf_counter() - text_delivery_started_at) * 1000, 1)
    visible_text_delivered = bool(streamed_messages or unsent_reply_messages)
    visible_file_delivered = bool(file_send_result.get("count") or 0) and bool(file_send_result.get("ok"))
    final_failure_notice_result = {"ok": True, "status": "skipped", "reason": "visible_delivery_present"}
    # Plugin events are durable, host-initiated work.  Their caller receives
    # the structured failure below and owns retry/backoff; sending the
    # interactive "please try again" notice would expose an internal retry as
    # a user-facing message and can spam the destination on every attempt.
    # Successful plugin replies still use the exact same rendering path.
    surface_incomplete_notice = (
        str(turn_payload.get("turn_kind") or "").strip().lower() != "plugin_event" and not shop_cards_only
    )
    if surface_incomplete_notice and not bool(frame.get("_turn_stopped") or deliberate_silence) and (
        (not visible_text_delivered and not visible_file_delivered and not visible_action_delivered)
        or (not final_frame_received and not visible_file_delivered and not visible_action_delivered)
        or (
            bool(frame.get("_transient_final_failure"))
            and tool_preface_delivered
            and not final_streamed_text_delivered
            and not visible_file_delivered
            and not visible_action_delivered
        )
    ):
        # A streamed tool preface or emotion is not an authoritative final
        # reply.  If the turn ends without a final frame or delivered file,
        # surface a transport-level status instead of silently treating the
        # provisional text as completion.  Do not rerun the turn here: a tool
        # may already have produced irreversible side effects.
        final_failure_notice = (
            "这次没有形成可交付的文字结果，刚才的处理没有完整结束。请再试一次。"
        )
        final_failure_notice_result = qq_gateway.send_reply(context, final_failure_notice)
        if final_failure_notice_result.get("ok"):
            reply_messages.append(final_failure_notice)
            delivered_reply_messages.append(final_failure_notice)
            send_result = dict(send_result)
            send_result_results = [
                *list(send_result.get("results") or []),
                dict(final_failure_notice_result),
            ]
            send_result.update(
                {
                    "ok": all(bool(item.get("ok")) for item in send_result_results),
                    "count": len(send_result_results),
                    "results": send_result_results,
                    "final_failure_notice": True,
                }
            )
    if streamed_messages:
        combined_results = [*stream_send_results, *list(send_result.get("results") or [])]
        send_result = {
            "ok": all(bool(item.get("ok")) for item in combined_results) if combined_results else True,
            "count": len(combined_results),
            "streamed_count": len(streamed_messages),
            "deferred_count": len(unsent_reply_messages),
            "results": combined_results,
            "delivery": send_result.get("delivery"),
            "text_result": send_result.get("text_result"),
            "voice_result": send_result.get("voice_result"),
            "final_failure_notice": bool(send_result.get("final_failure_notice")),
        }
    if bool(frame.get("_turn_stopped")):
        send_result = {
            "ok": True,
            "status": "stopped",
            "reason": str(frame.get("reason") or "user_stopped"),
            "count": 0,
            "results": [],
        }

    emotion_image_result = {"ok": True, "status": "skipped", "reason": "not_attempted"}
    emotion_started_at = time.perf_counter()
    if frame.get("_emotion_model_authored") is False:
        emotion_mface_result = {
            "ok": True,
            "status": "skipped",
            "reason": "emotion_not_model_authored",
        }
        emotion_image_result = dict(emotion_mface_result)
    elif (
        send_result.get("ok")
        and not bool(frame.get("_transient_final_failure") or deliberate_silence)
        and (
        visible_text_delivered
        or visible_file_delivered
        or bool(final_failure_notice_result.get("ok") and final_failure_notice_result.get("status") != "skipped")
        )
    ):
        qq_delivery_config = _load_qq_delivery_config(engine, context)
        current_outfit_id = _qq_current_outfit_id_from_turn_payload(turn_payload)
        emotion_mface_result = qq_gateway.send_emotion_mface(
            context,
            frame,
            qq_delivery_config=qq_delivery_config,
        )
        if emotion_mface_result.get("status") == "skipped" and emotion_mface_result.get("reason") in {
            "dedupe_interval", "delivery_in_progress", "group_emotion_disabled", "artifact_delivery_turn",
        }:
            emotion_image_result = dict(emotion_mface_result)
        elif emotion_mface_result.get("status") != "sent":
            emotion_image_result = _send_qq_emotion_image_fallback(
                engine=engine,
                qq_gateway=qq_gateway,
                context=context,
                frame=frame,
                qq_delivery_config=qq_delivery_config,
                current_outfit_id=current_outfit_id,
            )
    else:
        emotion_mface_result = {
            "ok": True,
            "status": "skipped",
            "reason": (
                str(frame.get("_notification_suppressed") or "model_silence")
                if deliberate_silence
                else "main_delivery_failed"
            ),
        }
    timing["emotion_delivery_ms"] = round((time.perf_counter() - emotion_started_at) * 1000, 1)

    final_reply_fallback_result = {"ok": True, "status": "skipped", "reason": "final_reply_present"}
    if not final_reply_messages and int(file_send_result.get("count") or 0) > 0 and not shop_cards_only:
        if bool(file_send_result.get("ok")):
            fallback_text = "结果已经生成，我发给你了。"
            final_reply_fallback_result = qq_gateway.send_reply(context, fallback_text)
            final_reply_fallback_result["status"] = (
                "generated_result_notice_sent" if final_reply_fallback_result.get("ok")
                else "generated_result_notice_failed"
            )
            if final_reply_fallback_result.get("ok"):
                reply_messages.append(fallback_text)
                delivered_reply_messages.append(fallback_text)
                send_result = dict(send_result)
                send_result_results = [
                    *list(send_result.get("results") or []),
                    dict(final_reply_fallback_result),
                ]
                send_result.update(
                    {
                        "ok": all(bool(item.get("ok")) for item in send_result_results),
                        "count": len(send_result_results),
                        "results": send_result_results,
                    }
                )
        else:
            final_reply_fallback_result = {
                "ok": True,
                "status": "skipped",
                "reason": "delivery_failure_notice_will_be_sent",
            }
    file_delivery_feedback_result = {"ok": True, "status": "skipped", "reason": "no_delivery_issue"}
    file_delivery_status = str(file_send_result.get("status") or "").strip().lower()
    _sid = str(getattr(context, "session_id", "") or "")
    if shop_cards_only:
        failed_ids = {item.get("source_id") for item in card_results if not item.get("ok")}
        if failed_ids:
            notices = [str(event.get("fallback_text") or "").strip() for event in delivery_events
                       if isinstance(event, dict) and isinstance(event.get("file"), dict)
                       and event["file"].get("source_id") in failed_ids and event.get("fallback_text")]
            text = "商品变更已保存，确认图没能送达，先给你文字版。"
            if notices:
                text += "\n\n" + "\n\n".join(dict.fromkeys(notices))
            file_delivery_feedback_result = qq_gateway.send_reply(context, text)
            notice_sent = bool(file_delivery_feedback_result.get("ok"))
            file_delivery_feedback_result["status"] = "text_fallback_sent" if notice_sent else "text_fallback_failed"
            qq_gateway.add_delivery_note(_sid, "【上一轮交付状态】商品变更已保存，确认图发送失败；" +
                                         ("文字确认已发送。" if notice_sent else "文字确认也未能发送。"))
        else:
            qq_gateway.add_delivery_note(_sid, "【上一轮交付状态】商品确认图已发送。")
    elif file_delivery_status == "partial":
        file_delivery_feedback_result = qq_gateway.send_reply(
            context,
            "这次只有一部分音频/文件发送成功，完整生成结果仍保留在工作台，可以稍后补发。",
        )
        file_delivery_feedback_result["status"] = (
            "partial_notice_sent" if file_delivery_feedback_result.get("ok") else "partial_notice_failed"
        )
        qq_gateway.add_delivery_note(_sid, "【上一轮交付状态】生成结果只发送成功一部分，完整文件仍在工作台。")
    elif file_delivery_status == "failed" or (
        int(file_send_result.get("count") or 0) > 0 and not bool(file_send_result.get("ok"))
    ):
        file_delivery_feedback_result = qq_gateway.send_reply(
            context,
            "文件这次发送失败了，现有结果仍保留着，可以稍后再试。",
        )
        notice_sent = bool(file_delivery_feedback_result.get("ok"))
        file_delivery_feedback_result["status"] = "failure_notice_sent" if notice_sent else "failure_notice_failed"
        notice_outcome = "失败提示已发送。" if notice_sent else "失败提示也未发送成功。"
        qq_gateway.add_delivery_note(_sid, "【上一轮交付状态】文件发送失败，文件仍在工作台。" + notice_outcome)
    elif int(file_send_result.get("count") or 0) > 0 and bool(file_send_result.get("ok")):
        qq_gateway.add_delivery_note(
            _sid,
            f"【上一轮交付状态】文件发送成功（共 {file_send_result.get('count', 0)} 个）。",
        )
    sticker_started_at = time.perf_counter()
    sticker_send_result = qq_gateway.send_stickers(
        context,
        list(frame.get("tool_events") or []),
    )
    timing["sticker_delivery_ms"] = round((time.perf_counter() - sticker_started_at) * 1000, 1)
    if (
        deliberate_silence
        and not delivered_reply_messages
        and not visible_file_delivered
        and not visible_action_delivered
        and not (sticker_send_result.get("ok") and sticker_send_result.get("count"))
        and send_result.get("ok")
    ):
        send_result = {
            **send_result,
            "status": "suppressed",
            "reason": str(frame.get("_notification_suppressed") or "model_silence"),
        }
    timing["total_ms"] = round((time.perf_counter() - timing_started_at) * 1000, 1)
    if cancellation_requested():
        frame["status"] = "stopped"
    # A successfully transmitted error notice is not a completed model reply.
    # Preserve transport facts separately so partial speech/files stay auditable.
    model_incomplete = not final_frame_received or bool(frame.get("_transient_final_failure"))
    memory_failure = frame.get("_memcore_failure")
    persistence_failed = isinstance(memory_failure, dict) and memory_failure.get("reason") == "input_turn_completion_failed"
    if not bool(frame.get("_turn_stopped") or frame.get("status") == "stopped") and (
        model_incomplete or persistence_failed
    ):
        if model_incomplete:
            model_status = "failed"
        send_result = {
            **send_result,
            "ok": False,
            "status": "failed",
            "reason": "model_turn_incomplete" if model_incomplete else "reply_persistence_failed",
            "transport_ok": bool(send_result.get("ok")) and bool(final_failure_notice_result.get("ok")),
            "failure_notice_sent": bool(final_failure_notice_result.get("ok"))
                and final_failure_notice_result.get("status") != "skipped",
            "partial_delivery": bool(streamed_messages or visible_file_delivered or visible_action_delivered
                                     or (unsent_reply_messages and send_result.get("ok"))),
        }
    return {
        "frame": frame,
        "reply_messages": delivered_reply_messages,
        "send_result": send_result,
        "emotion_mface_result": emotion_mface_result,
        "emotion_image_result": emotion_image_result,
        "file_send_result": file_send_result,
        "final_reply_fallback_result": final_reply_fallback_result,
        "file_delivery_feedback_result": file_delivery_feedback_result,
        "final_failure_notice_result": final_failure_notice_result,
        "sticker_send_result": sticker_send_result,
        "visible_action_delivered": visible_action_delivered,
        "final_frame_received": bool(final_frame_received),
        "model_status": model_status,
        "text_suppression": {
            "status": "suppressed" if frame.get("_notification_suppressed") else "not_suppressed",
            "reason": str(frame.get("_notification_suppressed") or ""),
        },
        "timing": timing,
    }
