"""QQSessionQueue: per-Bot state and explicit conversation dependencies."""

from __future__ import annotations

import asyncio
import sqlite3
import time
from contextlib import nullcontext
from typing import Any

from companion_v01.channels.qq.inbound.attachment_context import (
    _append_qq_attachment_handles_to_message,
    _merge_qq_attachments,
    _normalize_passive_image_placeholder,
    _with_qq_sender_context,
)
from companion_v01.channels.qq.inbound.attachments import (
    qq_attachment_ready_wait_seconds as _qq_attachment_ready_wait_seconds,
)
from companion_v01.channels.qq.inbound.record_ids import _passive_record_source_id, _passive_replay_identity
from companion_v01.channels.qq.inbound.references import _qq_structured_forward_references
from companion_v01.channels.qq.turns.work import (
    _durable_qq_work_payload,
    _session_item_wait_ms,
    _session_work_key,
    passive_batch_key,
    passive_retry_can_yield,
)
from companion_v01.durable_session_queue import (
    DurableSessionWorkQueue,
    RetryableSessionWorkError,
    SessionWorkError,
    _is_sqlite_lock_error,
)
from companion_v01.host_completion_batch import (
    completion_batch_key,
    prepare_completion_turn,
    record_completion_delivery,
    record_completion_error,
)
from companion_v01.local_timings import LocalTimings, timing_scope
from companion_v01.plugin_turn_intents import PluginTurnError, apply_turn_intent
from companion_v01.turn_coordination import SessionWorkQueue

# A failed passive memory write is retried, because the admitted envelope is
# the only surviving copy of the group history. The attempts bound keeps a
# permanently broken backend from spinning on the same batch forever.
_PASSIVE_RECORD_MAX_ATTEMPTS = 3
_PASSIVE_RECORD_RETRY_DELAY_SECONDS = 5.0
# Replaying these cannot make them recordable; they fail as a host defect.
_PASSIVE_RECORD_TERMINAL_STATUSES = frozenset({"empty_batch", "mixed_batch_scope"})


class QQSessionQueue:
    def __init__(
        self, *, engine, config_module, qq_gateway, runtime_metrics, logger, log_event,
        turn_coordinator, plugin_inputs, plugin_turn_router, attention,
        run_unlocked, queue, schedule_task,
    ):
        self.engine = engine
        self.config_module = config_module
        self.qq_gateway = qq_gateway
        self.runtime_metrics = runtime_metrics
        self.logger = logger
        self.log_event = log_event
        self.turn_coordinator = turn_coordinator
        self.plugin_inputs = plugin_inputs
        self.plugin_turn_router = plugin_turn_router
        self.attention = attention
        self.run_unlocked = run_unlocked
        self.queue = queue
        if self.queue is None:
            self.queue = SessionWorkQueue(
                self.handle, schedule_task=schedule_task, batchable_kinds={"passive"}, on_error=self.on_error,
            )
        elif isinstance(self.queue, DurableSessionWorkQueue):
            self.queue.register_handler(
                "qq", self.handle, on_error=self.on_error,
                batch_key=lambda item: passive_batch_key(item) or completion_batch_key(item),
                can_yield_retry=passive_retry_can_yield,
            )

    async def _load_recorded_passive_payload(self, context, payload):
        """Use the first durable record after a partial write; do not enrich it again."""
        source_id = _passive_record_source_id(
            payload, profile_user_id=context.profile_user_id,
            character_pack_id=str(getattr(context, "character_pack_id", "") or ""),
        )
        read = getattr(getattr(self.engine, "store", None), "get_message_by_source_id", None)
        if not source_id or not callable(read):
            return None
        record = await asyncio.to_thread(read, source_id)
        if record is None:
            return None
        if any(str(record.get(field) or "") != str(expected or "") for field, expected in (
            ("profile_user_id", context.profile_user_id), ("session_id", context.session_id),
            ("character_pack_id", getattr(context, "character_pack_id", "")), ("role", "user"),
        )):
            raise SessionWorkError("passive_record_scope_mismatch")
        restored = dict(payload)
        restored.update(
            message=str(record.get("content") or ""), memory_message=str(record.get("content") or ""),
            timestamp=int(record["timestamp"]), passive_memory_source_id=source_id,
        )
        metadata = record.get("memory_metadata") or {}
        for field in ("message_addressing", "forward_references"):
            if field in metadata:
                restored[field] = metadata[field]
        return restored

    async def _prepare_passive_payload(self,
        *,
        context: Any,
        event: dict[str, Any],
        base_payload: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, int | float], list[dict[str, Any]]]:
        payload = dict(base_payload)
        effective_attachments = [dict(item) for item in list(getattr(context, "attachments", None) or [])]
        metrics = {
            "forward_count": 0,
            "forward_resolved_count": 0,
            "attachment_count": len(effective_attachments),
            "attachments_registered": 0,
        }
        stages = LocalTimings()
        if getattr(context, "forward_refs", ()):
            forward_result = await asyncio.to_thread(
                self.qq_gateway.resolve_forward_message_evidence,
                event,
                context=context,
            )
            stages.mark("forward_resolution_ms")
            forward_payload = forward_result if isinstance(forward_result, dict) else {}
            metrics["forward_count"] = int(forward_payload.get("forward_count") or 0)
            metrics["forward_resolved_count"] = int(forward_payload.get("resolved_count") or 0)
            forwarded_attachments = [
                dict(item) for item in list(forward_payload.get("attachments") or []) if isinstance(item, dict)
            ]
            effective_attachments = _merge_qq_attachments(effective_attachments, forwarded_attachments)
            forward_references = _qq_structured_forward_references(forward_payload)
            if forward_references:
                payload["forward_references"] = [dict(item) for item in forward_references]

        metrics["attachment_count"] = len(effective_attachments)
        registered_items: list[dict[str, Any]] = []
        if effective_attachments:
            stages.last = time.perf_counter()
            registered = await asyncio.to_thread(
                self.engine.ingest_qq_attachments,
                profile_user_id=context.profile_user_id,
                session_id=context.session_id,
                attachments=_with_qq_sender_context(effective_attachments, context),
                character_pack_id=str(getattr(context, "character_pack_id", "") or ""),
                timestamp=int(event.get("time") or time.time()),
                observe_images=False,
            )
            stages.mark("attachment_registration_ms")
            registered_items = [item for item in list(registered or []) if isinstance(item, dict)]
            metrics["attachments_registered"] = len(registered_items)
            payload["message"] = _normalize_passive_image_placeholder(
                str(payload.get("message") or ""),
                registered_items,
            )
            payload["message"] = _append_qq_attachment_handles_to_message(
                str(payload.get("message") or ""),
                registered_items,
            )
        metrics.update({key: round(value, 1) for key, value in stages.values.items()})
        return payload, metrics, registered_items

    def restore_context(self, item: Any, payload: dict[str, Any]) -> Any:
        legacy_context = payload.get("context")
        if legacy_context is not None:
            return legacy_context
        event = dict(payload.get("event") or {})
        turn_payload = payload.get("turn_payload") if isinstance(payload.get("turn_payload"), dict) else {}
        delivery_context = turn_payload.get("qq_delivery_context")
        resolver = getattr(self.qq_gateway, "context_from_delivery_context", None)
        stored_context = (
            resolver(delivery_context)
            if callable(resolver) and isinstance(delivery_context, dict)
            else None
        )
        context = stored_context
        if str(getattr(item, "kind", "") or "") == "passive":
            restorer = getattr(self.qq_gateway, "restore_admitted_passive_context", None)
            if not callable(restorer) or not isinstance(delivery_context, dict):
                raise ValueError("queued_passive_context_restore_unavailable")
            context = restorer(event, delivery_context)
        if context is None:
            raise ValueError("queued_context_restore_failed")
        expected_profile = str(getattr(item, "profile_user_id", "") or "").strip()
        expected_session = str(getattr(item, "session_id", "") or "").strip()
        if expected_profile and expected_profile != str(getattr(context, "profile_user_id", "") or "").strip():
            raise ValueError("queued_context_profile_mismatch")
        if expected_session and expected_session != str(getattr(context, "session_id", "") or "").strip():
            raise ValueError("queued_context_session_mismatch")
        return context

    async def _restore_native_images(self, context: Any, turn_payload: dict[str, Any]) -> None:
        if list(turn_payload.get("native_user_images") or []):
            return
        attachment_ids = [
            str(item or "").strip()
            for item in list(turn_payload.get("qq_current_attachment_ids") or [])[:5]
            if str(item or "").strip()
        ]
        if not attachment_ids:
            return
        prepare = getattr(self.engine, "prepare_qq_native_image_inputs", None)
        if not callable(prepare):
            return
        result = await asyncio.to_thread(
            prepare,
            profile_user_id=str(getattr(context, "profile_user_id", "") or ""),
            session_id=str(getattr(context, "session_id", "") or ""),
            attachment_ids=attachment_ids,
            chat_model_override=str(getattr(context, "chat_model_override", "") or ""),
            timeout_seconds=_qq_attachment_ready_wait_seconds(context, self.config_module),
        )
        images = [
            dict(item)
            for item in list((result or {}).get("images") or [])[:5]
            if isinstance(item, dict) and str(item.get("data_url") or "").startswith("data:image/")
        ]
        if images:
            turn_payload["native_user_images"] = images

    async def handle(self, key, items):
        if not items:
            return
        first_payload = items[0].payload if isinstance(items[0].payload, dict) else {}
        if items[0].kind == "passive" or first_payload.get("plugin_turn_request_id"):
            return await self._handle_admitted(key, items)
        context = self.restore_context(items[0], first_payload)
        marker = first_payload.get("turn_payload") or {}
        with self.plugin_inputs.scope(context, marker) as inbound:
            return await self._handle_admitted(key, items, inbound=inbound)

    async def _handle_admitted(self, _key: str, items: list[Any], *, inbound="") -> None:
        if not items:
            return
        first_payload = items[0].payload if isinstance(items[0].payload, dict) else {}
        context = self.restore_context(items[0], first_payload)
        profile_user_id = str(getattr(context, "profile_user_id", "") or "")
        session_id = str(getattr(context, "session_id", "") or "")
        group_id = int(getattr(context, "group_id", 0) or 0)
        if items[0].kind == "passive":
            started_at = time.perf_counter()
            stages = LocalTimings()
            queue_wait_ms = _session_item_wait_ms(items[0])
            payloads: list[dict[str, Any]] = []
            registered_items_by_payload: list[list[dict[str, Any]]] = []
            enrichment_totals = {
                "forward_count": 0,
                "forward_resolved_count": 0,
                "attachment_count": 0,
                "attachments_registered": 0,
                "forward_resolution_ms": 0,
                "attachment_registration_ms": 0,
            }
            for item in items:
                item_payload = item.payload if isinstance(item.payload, dict) else {}
                item_context = context if item is items[0] else self.restore_context(item, item_payload)
                item_event = dict(item_payload.get("event") or {})
                base_payload = (
                    dict(item_payload.get("turn_payload") or {})
                    if isinstance(item_payload.get("turn_payload"), dict)
                    else {}
                )
                replay = getattr(item, "attempts", 0) > 1 or bool(base_payload.get("passive_memory_source_id"))
                source_id = _passive_record_source_id(
                    base_payload, profile_user_id=item_context.profile_user_id,
                    character_pack_id=str(getattr(item_context, "character_pack_id", "") or ""),
                )
                if not source_id and getattr(item, "item_id", ""):
                    source_id = f"qq-passive-inbox:{item.item_id}"
                if source_id:
                    base_payload["passive_memory_source_id"] = source_id
                try:
                    prepared = await self._load_recorded_passive_payload(item_context, base_payload) if replay else None
                except sqlite3.OperationalError as exc:
                    if not _is_sqlite_lock_error(exc):
                        raise
                    self._raise_passive_record_failure(items=items, payloads=[base_payload], result={})
                if prepared is None:
                    prepared, item_metrics, registered_items = await self._prepare_passive_payload(
                        context=item_context, event=item_event, base_payload=base_payload,
                    )
                else:
                    item_metrics, registered_items = {}, []
                if source_id:
                    prepared["passive_memory_source_id"] = source_id
                payloads.append(prepared)
                registered_items_by_payload.append(registered_items)
                for key in enrichment_totals:
                    enrichment_totals[key] += item_metrics.get(key) or 0
            stages.mark("content_preparation_ms")
            async with self.turn_coordinator.hold(
                profile_user_id, session_id, turn_kind="passive_memory", accepts_steering=False,
            ):
                stages.mark("turn_ownership_ms")
                batch_recorder = getattr(self.engine, "record_passive_qq_messages", None)
                recorder = getattr(self.engine, "record_passive_qq_message", None)
                if callable(batch_recorder):
                    record_result = await asyncio.to_thread(batch_recorder, payloads)
                elif callable(recorder):
                    fallback_results = [await asyncio.to_thread(recorder, payload) for payload in payloads]
                    failed_count = sum(
                        1 for result in fallback_results if not isinstance(result, dict) or not bool(result.get("ok"))
                    )
                    record_result = {
                        "ok": failed_count == 0,
                        "status": "recorded" if failed_count == 0 else "partially_recorded",
                        "count": len(fallback_results),
                        "recorded_count": len(fallback_results) - failed_count,
                        "failed_count": failed_count,
                        "results": fallback_results,
                    }
                else:
                    record_result = {
                        "ok": False,
                        "status": "recorder_unavailable",
                        "count": len(payloads),
                        "recorded_count": 0,
                        "failed_count": len(payloads),
                        "results": [],
                    }
                stages.mark("memory_record_and_schedule_ms")
            result_payload = record_result if isinstance(record_result, dict) else {}
            duration_ms = (time.perf_counter() - started_at) * 1000
            self.runtime_metrics.observe_request(
                "qq_passive_group_message_batch",
                duration_ms=duration_ms,
                ok=bool(result_payload.get("ok")),
            )
            self.log_event(
                "qq_passive_group_message_batch_recorded",
                session_id=session_id,
                profile_user_id=profile_user_id,
                group_id=group_id,
                batch_count=len(payloads),
                recorded_count=int(result_payload.get("recorded_count") or 0),
                failed_count=int(result_payload.get("failed_count") or 0),
                record_status=str(result_payload.get("status") or ""),
                timing_scope=timing_scope(profile_user_id, session_id, str(getattr(context, "character_pack_id", "") or "")),
                queue_wait_ms=round(queue_wait_ms, 1),
                duration_ms=round(duration_ms, 1),
                local_stages=stages.snapshot(),
                memory_stages=result_payload.get("local_stages") or {},
                **enrichment_totals,
            )
            if not bool(result_payload.get("ok")):
                # A lost memory write must never settle as a delivered history.
                self._raise_passive_record_failure(items=items, payloads=payloads, result=result_payload)
            if bool(result_payload.get("ok")):
                batch_results = list(result_payload.get("results") or [])
                for index, (item, registered_items) in enumerate(zip(items, registered_items_by_payload)):
                    item_payload = item.payload if isinstance(item.payload, dict) else {}
                    item_context = self.restore_context(item, item_payload)
                    item_event = dict(item_payload.get("event") or {})
                    item_record_result = (
                        batch_results[index]
                        if index < len(batch_results) and isinstance(batch_results[index], dict)
                        else {}
                    )
                    if not bool(item_payload.get("record_only")):
                        self.attention.stage_media(
                            item_context,
                            item_event,
                            registered_items,
                            timeline_source_id=str(item_record_result.get("source_id") or ""),
                        )
                attention_items = [
                    (index, item)
                    for index, item in enumerate(items)
                    if not bool((item.payload if isinstance(item.payload, dict) else {}).get("record_only"))
                ]
                attention_result = {"scheduled": False, "reason": "record_only"}
                if attention_items:
                    last_index, last_item = attention_items[-1]
                    last_item_payload = last_item.payload if isinstance(last_item.payload, dict) else {}
                    last_context = self.restore_context(last_item, last_item_payload)
                    last_event = dict(last_item_payload.get("event") or {})
                    last_record_result = (
                        batch_results[last_index]
                        if last_index < len(batch_results) and isinstance(batch_results[last_index], dict)
                        else {}
                    )
                    attention_result = self.attention.schedule(
                        last_context,
                        last_event,
                        projection_anchor_source_id=str(last_record_result.get("source_id") or ""),
                        forward_references=tuple(
                            item for item in list(payloads[last_index].get("forward_references") or [])
                            if isinstance(item, dict)
                        ),
                        participant_keys=tuple(
                            self.attention.actor_key(self.restore_context(item, item.payload))
                            for _, item in attention_items
                        ),
                    )
                self.log_event(
                    "qq_group_attention_considered",
                    session_id=session_id,
                    profile_user_id=profile_user_id,
                    group_id=group_id,
                    scheduled=bool(attention_result.get("scheduled")),
                    reason=str(attention_result.get("reason") or ""),
                )
            return

        event = dict(first_payload.get("event") or {})
        turn_payload = dict(first_payload.get("turn_payload") or {})
        request_id = str(first_payload.get("plugin_turn_request_id") or "")
        if request_id:
            if self.plugin_turn_router is None:
                raise PluginTurnError("agent_turn_scope_expired")
            self.plugin_turn_router.require_pending(request_id)
        qq_user_id = int(getattr(context, "user_id", 0) or 0)
        actor_id = str(turn_payload.get("actor_stable_id") or (f"qq:{qq_user_id}" if qq_user_id else f"qq-profile:{profile_user_id}"))
        started_at = time.perf_counter()
        stages = LocalTimings()
        admitted_wait_ms = _session_item_wait_ms(items[0])
        try:
            async with self.turn_coordinator.hold(
                profile_user_id,
                session_id,
                actor_id=actor_id,
                channel="qq",
                turn_kind=str(turn_payload.get("turn_kind") or ""),
                memory_domain=str(getattr(context, "character_pack_id", "") or ""),
            ) as turn_control_id:
                stages.mark("turn_ownership_ms")
                queue_wait_ms = _session_item_wait_ms(items[0])
                # Every non-passive QQ turn can reach a non-idempotent model or
                # delivery side effect. Fence it before preparation/model work,
                # not only for completion batches and plugin-originated turns.
                if isinstance(self.queue, DurableSessionWorkQueue):
                    await self.queue.begin_processing(items)
                completion = prepare_completion_turn(self.engine, items)
                turn_payload, prepared_frame = completion.payload, completion.prepared_frame
                turn_payload.pop("_plugin_inbound_turn_id", None)
                turn_payload.pop("_plugin_inbound_required", None)
                if len(items) > 1:
                    context = self.restore_context(items[0], {**first_payload, "turn_payload": turn_payload})
                stages.mark("completion_prepare_ms")
                await self._restore_native_images(context, turn_payload)
                stages.mark("image_ready_ms")
                turn_payload["_turn_control_id"] = turn_control_id
                if request_id:
                    apply_turn_intent(turn_payload, self.plugin_turn_router.begin(request_id, turn_control_id))
                    context = self.restore_context(items[0], {**first_payload, "turn_payload": turn_payload})
                processing_started_at = time.perf_counter()
                with completion.processing(self.turn_coordinator, turn_control_id), self.plugin_turn_router.processing(request_id) if request_id else self.plugin_turn_router.processing_inbound(inbound, turn_control_id) if inbound else nullcontext():
                    turn_result = await self.run_unlocked(
                        context=context,
                        event=event,
                        turn_payload=turn_payload,
                        prepared_frame=prepared_frame,
                        additional_delivery_events=completion.direct_events,
                        file_target_allowed=completion.allows_file_target if completion.jobs else None,
                    )
                processing_ms = (time.perf_counter() - processing_started_at) * 1000
            result_payload = turn_result if isinstance(turn_result, dict) else {}
            self.plugin_inputs.settle(inbound, result_payload)
            send_result = result_payload.get("send_result")
            send_payload = send_result if isinstance(send_result, dict) else {}
            model_failed = result_payload.get("final_frame_received") is False or bool(
                (result_payload.get("frame") or {}).get("_transient_final_failure"))
            files = result_payload.get("file_send_result") or {}
            delivered = bool(send_payload.get("ok")) and files.get("ok") is not False
            stopped = (result_payload.get("frame") or {}).get("status") == "stopped"
            record_completion_delivery(self.engine, items, ok=delivered and not model_failed and not stopped,
                model_status=str(result_payload.get("model_status") or (
                    "stopped" if stopped else "not_requested" if prepared_frame is not None else "failed" if model_failed else "completed")),
                delivery_status="failed" if not delivered else str((files.get("status") if files.get("count") else None) or send_payload.get("status") or "sent"),
                reason="host_completion_stopped" if stopped else "host_completion_turn_or_delivery_incomplete" if not delivered or model_failed else "", files=files)
            if request_id and (result_payload.get("frame") or {}).get("status") == "stopped":
                self.plugin_turn_router.finish(request_id, model_status="stopped",
                                          delivery_status=str(send_payload.get("status") or "not_sent"),
                                          reason=str((result_payload.get("frame") or {}).get("reason") or ""))
                return
            if (completion_batch_key(items[0]) or request_id) and (
                result_payload.get("final_frame_received") is False
                or bool((result_payload.get("frame") or {}).get("_transient_final_failure"))
                or not send_payload.get("ok")
                or (result_payload.get("file_send_result") or {}).get("ok") is False
            ):
                if request_id:
                    model_failed = (result_payload.get("final_frame_received") is False or
                                    bool((result_payload.get("frame") or {}).get("_transient_final_failure")))
                    self.plugin_turn_router.finish(request_id, model_status="failed" if model_failed else "completed",
                        delivery_status=str(send_payload.get("status") or "failed"),
                        reason="agent_turn_model_incomplete" if model_failed else "agent_turn_delivery_failed")
                raise SessionWorkError("host_completion_turn_or_delivery_incomplete")
            if request_id:
                self.plugin_turn_router.finish(request_id, model_status="completed",
                                          delivery_status=str(send_payload.get("status") or "sent"))
            duration_ms = (time.perf_counter() - started_at) * 1000
            self.runtime_metrics.observe_request(
                "qq_group_turn_queue",
                duration_ms=duration_ms,
                ok=bool(send_payload.get("ok")),
            )
            self.log_event(
                "qq_group_turn_queue_completed",
                session_id=session_id,
                profile_user_id=profile_user_id,
                group_id=group_id,
                user_id=qq_user_id,
                source_message_id=str(getattr(context, "source_message_id", "") or ""),
                queue_sequence=items[0].sequence,
                queue_wait_ms=round(queue_wait_ms, 1),
                admitted_queue_wait_ms=round(admitted_wait_ms, 1),
                turn_processing_ms=round(processing_ms, 1),
                duration_ms=round(duration_ms, 1),
                local_stages=stages.snapshot(),
                sent=bool(send_payload.get("ok")),
                delivery_status=str(send_payload.get("status") or ""),
                batch_count=len(items),
            )
        except Exception as exc:
            duration_ms = (time.perf_counter() - started_at) * 1000
            self.runtime_metrics.observe_request("qq_group_turn_queue", duration_ms=duration_ms, ok=False)
            self.logger.exception("queued qq group turn failed")
            self.log_event(
                "qq_group_turn_queue_failed",
                session_id=session_id,
                profile_user_id=profile_user_id,
                group_id=group_id,
                user_id=qq_user_id,
                source_message_id=str(getattr(context, "source_message_id", "") or ""),
                queue_sequence=items[0].sequence,
                reason=exc.reason if isinstance(exc, SessionWorkError) else exc.__class__.__name__,
                duration_ms=round(duration_ms, 1),
                local_stages=stages.snapshot(),
            )
            raise

    def _raise_passive_record_failure(self, *, items, payloads, result) -> None:
        """Refuse to commit a passive batch whose memory write failed.

        The durable envelope and first local record keep media and text stable
        on replay; a batch that cannot become recordable
        by replaying, or one that already used its attempts, fails loudly
        instead of spinning on the same history.
        """

        if str(result.get("status") or "") in _PASSIVE_RECORD_TERMINAL_STATUSES:
            raise SessionWorkError("passive_memory_batch_invalid")
        attempts = max(int(getattr(item, "attempts", 0) or 0) for item in items)
        replayable = all(_passive_replay_identity(payload) for payload in payloads)
        if replayable and attempts < _PASSIVE_RECORD_MAX_ATTEMPTS:
            raise RetryableSessionWorkError(
                "passive_memory_record_failed",
                retry_delay_seconds=_PASSIVE_RECORD_RETRY_DELAY_SECONDS,
            )
        raise SessionWorkError("passive_memory_record_failed")

    def on_error(self, _key: str, items: list[Any], exc: BaseException) -> None:
        if not isinstance(exc, RetryableSessionWorkError):
            record_completion_error(self.engine, items, exc)
        payload = items[0].payload if items and isinstance(items[0].payload, dict) else {}
        request_id = str(payload.get("plugin_turn_request_id") or "")
        if request_id and self.plugin_turn_router is not None:
            self.plugin_turn_router.fail(request_id, exc)
        context = payload.get("context")
        turn_payload = payload.get("turn_payload") if isinstance(payload.get("turn_payload"), dict) else {}
        inbound = str(turn_payload.get("_plugin_inbound_turn_id") or "")
        if inbound and self.plugin_turn_router is not None:
            self.plugin_turn_router.finish_inbound(inbound, model_status="failed", reason="inbound_queue_failed")
        delivery_context = (
            turn_payload.get("qq_delivery_context")
            if isinstance(turn_payload.get("qq_delivery_context"), dict)
            else {}
        )
        error_logger = getattr(self.logger, "error", None)
        if callable(error_logger):
            error_logger("qq session work handler failed: %s", exc.__class__.__name__)
        self.log_event(
            "qq_session_work_failed",
            session_id=str(getattr(context, "session_id", "") or delivery_context.get("session_id") or ""),
            profile_user_id=str(
                getattr(context, "profile_user_id", "") or delivery_context.get("profile_user_id") or ""
            ),
            work_kind=items[0].kind if items else "",
            batch_count=len(items),
            reason=exc.__class__.__name__,
        )

    async def enqueue(self,
        *,
        context: Any,
        event: dict[str, Any],
        turn_payload: dict[str, Any],
        kind: str,
        schedule: bool = True,
        record_only: bool = False,
    ) -> dict[str, Any]:
        key = _session_work_key(context)
        if isinstance(self.queue, DurableSessionWorkQueue):
            return await self.queue.enqueue(
                session_key=key,
                profile_user_id=str(getattr(context, "profile_user_id", "") or ""),
                session_id=str(getattr(context, "session_id", "") or ""),
                kind=kind,
                payload=_durable_qq_work_payload(
                    event=event,
                    turn_payload=turn_payload,
                    record_only=record_only,
                ),
                source="qq",
                source_event_id=str(getattr(context, "source_message_id", "") or ""),
                schedule=schedule,
            )
        return self.queue.enqueue(
            key,
            kind=kind,
            payload={"context": context, "event": dict(event), "turn_payload": dict(turn_payload), "record_only": record_only},
        )
