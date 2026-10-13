"""Record or enqueue passive QQ history and consider group attention."""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from typing import Any

from fastapi.responses import JSONResponse

from companion_v01.channels.qq.turns.work import _session_work_key

_QQ_GROUP_PASSIVE_MEMORY_MODES = frozenset({"all", "denylist", "off"})


def _resolve_group_passive_memory_policy(config_module: Any, group_id: Any) -> dict[str, Any]:
    """Resolve host-owned passive group memory policy without changing reply turns."""

    aliases = {
        # Passive history is context, not an execution permission. Old
        # allowlist deployments migrate to default recording so newly joined
        # groups cannot silently disappear from MemCore.
        "allowlist": "all",
        "whitelist": "all",
        "blacklist": "denylist",
        "enabled": "all",
        "disabled": "off",
    }
    raw_mode = str(getattr(config_module, "QQ_GROUP_PASSIVE_MEMORY_MODE", "all") or "all").strip().lower()
    mode = aliases.get(raw_mode, raw_mode)
    if mode not in _QQ_GROUP_PASSIVE_MEMORY_MODES:
        mode = "all"
    try:
        normalized_group_id = int(group_id or 0)
    except (TypeError, ValueError):
        normalized_group_id = 0
    raw_ids = str(getattr(config_module, "QQ_GROUP_PASSIVE_MEMORY_GROUP_IDS", "") or "")
    configured_ids: set[int] = set()
    for token in re.split(r"[,;，；\s]+", raw_ids):
        if not token.isdigit() or len(token) > 20:
            continue
        value = int(token)
        if value > 0:
            configured_ids.add(value)
    if normalized_group_id <= 0:
        enabled = False
    elif mode == "off":
        enabled = False
    elif mode == "denylist":
        enabled = normalized_group_id not in configured_ids
    else:
        enabled = True
    return {"enabled": enabled, "mode": mode}


class QQPassiveMessageHandler:
    def __init__(
        self, *,
        engine, config_module, runtime_metrics, log_event, turn_coordinator, session_queue,
        attention,
    ):
        self.engine = engine
        self.config_module = config_module
        self.runtime_metrics = runtime_metrics
        self.log_event = log_event
        self.turn_coordinator = turn_coordinator
        self.session_queue = session_queue
        self.attention = attention

    async def handle(self, *, context, event, started_at, record_only_command):
        if bool(getattr(context, "should_record", False)):
            passive_memory_policy = _resolve_group_passive_memory_policy(
                self.config_module,
                getattr(context, "group_id", 0),
            )
            if not bool(passive_memory_policy.get("enabled")):
                duration_ms = (time.perf_counter() - started_at) * 1000
                self.runtime_metrics.observe_request(
                    "qq_napcat_event",
                    duration_ms=duration_ms,
                    ok=True,
                )
                self.log_event(
                    "qq_passive_group_message_skipped",
                    session_id=context.session_id,
                    profile_user_id=context.profile_user_id,
                    group_id=int(getattr(context, "group_id", 0) or 0),
                    user_id=int(getattr(context, "user_id", 0) or 0),
                    reason="group_passive_memory_filtered",
                    policy_mode=str(passive_memory_policy.get("mode") or "all"),
                    duration_ms=round(duration_ms, 1),
                )
                return JSONResponse(
                    {
                        "status": "ignored",
                        "reason": (
                            "group_command_requires_explicit_mention"
                            if record_only_command else "group_passive_memory_filtered"
                        ),
                        "policy_mode": str(passive_memory_policy.get("mode") or "all"),
                    }
                )
            passive_queue_key = _session_work_key(context)
            needs_passive_enrichment = bool(
                getattr(context, "forward_refs", ()) or list(getattr(context, "attachments", None) or [])
            )
            if self.turn_coordinator.is_busy(
                context.profile_user_id,
                context.session_id,
            ) or self.session_queue.queue.has_work(passive_queue_key) or needs_passive_enrichment:
                deferred_payload = context.to_turn_payload()
                deferred_payload["timestamp"] = int(event.get("time") or time.time())
                queued = await self.session_queue.enqueue(
                    context=context,
                    event=dict(event),
                    turn_payload=dict(deferred_payload),
                    kind="passive",
                    record_only=record_only_command,
                )
                if queued.get("ok") and not record_only_command:
                    self.attention.supersede(context)
                duration_ms = (time.perf_counter() - started_at) * 1000
                self.runtime_metrics.observe_request(
                    "qq_napcat_event",
                    duration_ms=duration_ms,
                    ok=bool(queued.get("ok")),
                )
                self.log_event(
                    "qq_passive_group_message_buffered",
                    session_id=context.session_id,
                    profile_user_id=context.profile_user_id,
                    group_id=int(getattr(context, "group_id", 0) or 0),
                    user_id=int(getattr(context, "user_id", 0) or 0),
                    reason=("passive_content_enrichment" if needs_passive_enrichment else "active_turn_in_progress"),
                    queue_sequence=int(queued.get("sequence") or 0),
                    pending_count=int(queued.get("pending_count") or 0),
                    duration_ms=round(duration_ms, 1),
                )
                queue_status = "buffered" if queued.get("ok") else "record_failed"
                queue_reason = "passive_content_enrichment" if needs_passive_enrichment else "active_turn_in_progress"
                if record_only_command:
                    queue_status = "ignored" if queued.get("ok") else "record_failed"
                    queue_reason = "group_command_requires_explicit_mention"
                return JSONResponse(
                    {
                        "status": queue_status,
                        "reason": queue_reason,
                        "queue_reason": str(queued.get("reason") or "session_fifo"),
                        "session_id": context.session_id,
                        "profile_user_id": context.profile_user_id,
                        "queue_sequence": int(queued.get("sequence") or 0),
                        "pending_count": int(queued.get("pending_count") or 0),
                    }
                )
            recorder = getattr(self.engine, "record_passive_qq_message", None)
            turn_payload = context.to_turn_payload()
            turn_payload["timestamp"] = int(event.get("time") or time.time())
            if not turn_payload.get("source_message_id") and not turn_payload.get("passive_memory_source_id"):
                turn_payload["passive_memory_source_id"] = f"qq-passive-host:{uuid.uuid4().hex}"
            try:
                record_result = (
                    await asyncio.to_thread(recorder, turn_payload)
                    if callable(recorder)
                    else {"ok": False, "status": "recorder_unavailable"}
                )
            except Exception as exc:
                record_result = {"ok": False, "status": "record_failed", "reason": exc.__class__.__name__}
            record_payload = record_result if isinstance(record_result, dict) else {}
            record_ok = bool(record_payload.get("ok"))
            recovery = {}
            if not record_ok:
                retry_payload = dict(turn_payload)
                source_id = str(record_payload.get("source_id") or "").strip()
                if source_id:
                    retry_payload["passive_memory_source_id"] = source_id
                try:
                    recovery = await self.session_queue.enqueue(
                        context=context, event=dict(event), turn_payload=retry_payload,
                        kind="passive", record_only=record_only_command,
                    )
                except Exception as exc:
                    recovery = {"ok": False, "status": "enqueue_failed", "reason": exc.__class__.__name__}
            duration_ms = (time.perf_counter() - started_at) * 1000
            self.runtime_metrics.observe_request(
                "qq_napcat_event",
                duration_ms=duration_ms,
                ok=record_ok,
            )
            self.log_event(
                "qq_passive_group_message_recorded",
                session_id=context.session_id,
                profile_user_id=context.profile_user_id,
                group_id=int(getattr(context, "group_id", 0) or 0),
                user_id=int(getattr(context, "user_id", 0) or 0),
                reason=context.reason,
                record_status=str(record_payload.get("status") or ""),
                record_reason=str(record_payload.get("reason") or ""),
                recovery_status=str(recovery.get("status") or ""),
                recovery_reason=str(recovery.get("reason") or ""),
                duration_ms=round(duration_ms, 1),
            )
            if record_ok and not record_only_command:
                self.attention.supersede(context)
            attention_result = (
                self.attention.schedule(
                    context,
                    event,
                    projection_anchor_source_id=str(record_payload.get("source_id") or ""),
                )
                if record_ok and not record_only_command
                else {"scheduled": False, "reason": "record_only" if record_only_command else "record_failed"}
            )
            passive_status = "recorded" if record_ok else "record_failed"
            if recovery.get("ok"):
                passive_status = "buffered"
            if record_only_command and record_ok:
                passive_status = "ignored"
            return JSONResponse(
                {
                    "status": passive_status,
                    "reason": context.reason,
                    "session_id": context.session_id,
                    "profile_user_id": context.profile_user_id,
                    "character_pack_id": str(getattr(context, "character_pack_id", "") or ""),
                    "record_result": record_result,
                    **({"recovery": recovery} if not record_ok else {}),
                    "attention": attention_result,
                }
            )
        self.runtime_metrics.observe_request(
            "qq_napcat_event",
            duration_ms=(time.perf_counter() - started_at) * 1000,
            ok=True,
        )
        return JSONResponse({"status": "ignored", "reason": context.reason})
