"""Projection and execution checks for completed Jobs across durable channels.

Execution and ownership stay in HostJobStore. The session inbox freezes a
bounded envelope at claim time; this module neither schedules jobs nor sends
artifacts. Processing receipts stay in the existing Job store; turn controls
stay in the coordinator. Plugin-authored notices are not batchable here.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from typing import Any, Mapping
from types import SimpleNamespace

from .plugin_turn_intents import HostJobTurnIntent
from .session_inbox import SessionInboxItem
from .host_jobs import HostJobOwner
from .durable_session_queue import SessionWorkError
from .tool_continuation import job_followup
from .plugin_result_delivery import plugin_artifact_events


def job_delivery_events(job):
    if job_followup(job).reason == "scope_revoked":
        return []
    return _job_artifact_events(job)


def _job_artifact_events(job):
    result = job.result.get("capability_result") or {}
    return plugin_artifact_events(SimpleNamespace(content=result.get("content"), is_error=result.get("is_error", True)),
        capability_id=job.capability_id, client_mode=job.channel)


def _completion_jobs(engine, items):
    store = getattr(engine, "job_store", None)
    if store is None or not items or not all(completion_batch_key(item) for item in items):
        return []
    jobs = []
    for item in items:
        fields = item.payload["turn_payload"]["plugin_external_event"]["data"]
        job = store.get(fields["job_id"], owner=HostJobOwner(item.profile_user_id, item.session_id))
        if job is None or job.completion_event_id != item.source_event_id or job.turn_id != fields["origin_turn_id"]:
            raise SessionWorkError("host_job_completion_identity_mismatch")
        jobs.append(job)
    return jobs


@dataclass
class CompletionTurn:
    payload: dict
    prepared_frame: dict | None
    jobs: tuple
    model_job_ids: frozenset
    store: Any

    def _current(self, job):
        current = self.store.get(job.job_id, owner=job.owner)
        if current is None or current.completion_event_id != job.completion_event_id:
            raise SessionWorkError("host_job_completion_identity_mismatch")
        return current

    def model_scope_revoked(self):
        return any(self._current(job).scope_revoked_reason for job in self.jobs if job.job_id in self.model_job_ids)

    def direct_events(self):
        return [event for job in self.jobs if job.job_id not in self.model_job_ids
                for event in job_delivery_events(self._current(job))]

    def allows_file_target(self, target):
        generated_id = str(target.get("generated_id") or "")
        owners = [job for job in self.jobs if job.job_id not in self.model_job_ids
                  and any(event["generated_file"].get("generated_id") == generated_id
                          for event in job_delivery_events(job))]
        return not owners or any(not self._current(job).scope_revoked_reason for job in owners)

    @contextmanager
    def processing(self, coordinator, token):
        with coordinator.execution_scope(token, cancelled=self.model_scope_revoked) if self.jobs else nullcontext():
            # This is the actual processing receipt, separate from queue admission
            # and the mutable grant. A later withdrawal cannot erase participation.
            for job in self.jobs:
                saved = self.store.record_delivery_receipt(job.job_id, owner=job.owner,
                    completion_event_id=job.completion_event_id,
                    receipt={"stage": "delivery", "status": "processing",
                             "model_status": "running" if job.job_id in self.model_job_ids else "not_requested",
                             "delivery_status": "not_sent", "reason": ""})
                if not saved.get("ok"):
                    raise SessionWorkError("host_job_delivery_receipt_not_saved")
            yield


def prepare_completion_turn(engine, items):
    """Project prompt and delivery together after acquiring the session turn.

    Queue admission is not execution authority. In particular, a revoked Job
    must not remain in an earlier prompt when another Job still needs a model.
    """
    jobs = _completion_jobs(engine, items)
    model_items = [item for item, job in zip(items, jobs) if job_followup(job).requires_model]
    payload = completion_turn_payload(model_items or items)
    prepared = None if not jobs or model_items else {
        "status": "ok", "speech": "", "speech_segments": [], "tool_events": [],
        "_deliberate_silence": True, "client_mode": jobs[0].channel}
    return CompletionTurn(payload, prepared, tuple(jobs),
        frozenset(job.job_id for job in jobs if job_followup(job).requires_model), getattr(engine, "job_store", None))


def _model_participated(job):
    if job.delivery_receipt.get("stage") == "delivery":
        return job.delivery_receipt.get("model_status") != "not_requested"
    return job_followup(job).requires_model


def record_completion_delivery(engine, items, *, ok, model_status, delivery_status, reason="", files=None, files_for_direct_jobs=False):
    for job in _completion_jobs(engine, items):
        participated = _model_participated(job)
        receipt = {"stage": "delivery", "status": "completed" if ok else "failed",
                   "model_status": model_status if participated else "not_requested",
                   "delivery_status": delivery_status, "reason": reason}
        if isinstance(files, Mapping) and (not files_for_direct_jobs or not participated):
            job_files = files
            if not participated:
                ids = {event["generated_file"]["generated_id"] for event in _job_artifact_events(job)}
                results = [result for result in files.get("results", []) if result.get("generated_id") in ids]
                if results:
                    all_ok = all(result.get("ok") for result in results)
                    any_ok = any(result.get("ok") or (result.get("voice_result") or {}).get("ok")
                                 or (result.get("file_result") or {}).get("ok") for result in results)
                    # Desktop ACK confirms a queued presentation, not physical file delivery.
                    completed_status = "queued" if all(result.get("status") == "queued" for result in results) else "sent"
                    statuses = {str(result.get("status") or "failed") for result in results}
                    failed_status = next(iter(statuses)) if len(statuses) == 1 else "failed"
                    job_files = {"ok": all_ok, "status": completed_status if all_ok else "partial" if any_ok else failed_status,
                                 "count": len(results)}
                    receipt.update(status="completed" if all_ok else "failed", delivery_status=job_files["status"],
                        reason="" if all_ok else "host_completion_file_delivery_incomplete")
                elif job.scope_revoked_reason:
                    job_files = {"ok": True, "status": "not_requested", "count": 0}
                    receipt.update(status="completed", delivery_status="not_requested", reason="host_completion_scope_revoked")
            receipt["files"] = {key: job_files[key] for key in ("ok", "status", "count") if key in job_files}
        saved = engine.job_store.record_delivery_receipt(job.job_id, owner=job.owner,
            completion_event_id=job.completion_event_id, receipt=receipt)
        if not saved.get("ok"):
            raise SessionWorkError("host_job_delivery_receipt_not_saved")


def record_completion_error(engine, items, exc):
    """Unexpected failures retain uncertainty; never claim an unobserved send."""
    for job in _completion_jobs(engine, items):
        if job.delivery_receipt.get("stage") == "delivery" and job.delivery_receipt.get("status") in {"completed", "failed"}:
            continue
        saved = engine.job_store.record_delivery_receipt(job.job_id, owner=job.owner,
            completion_event_id=job.completion_event_id,
            receipt={"stage": "delivery", "status": "failed", "model_status": "unknown" if _model_participated(job) else "not_requested",
                     "delivery_status": "unknown", "reason": str(getattr(exc, "reason", "") or type(exc).__name__)})
        if not saved.get("ok"):
            raise SessionWorkError("host_job_delivery_receipt_not_saved")


def completion_metadata(request: HostJobTurnIntent, resolved: Mapping[str, str]) -> dict[str, Any]:
    if not isinstance(request, HostJobTurnIntent):
        return {}
    if request.source != "host.jobs" or request.event_type not in {
        "job.succeeded",
        "job.failed",
        "job.cancelled",
    }:
        return {}
    fields = request.data
    job_id = fields.get("job_id", "")
    origin = fields.get("origin_turn_id", "")
    if not job_id or not origin or request.idempotency_key != f"job-completed:{job_id}":
        return {}
    return {"origin_turn_id": origin, "context": dict(resolved)}


def completion_timestamp(request: HostJobTurnIntent, fallback: int) -> int:
    if request.source == "host.jobs":
        try:
            value = float(request.data.get("finished_at", ""))
            if math.isfinite(value) and value > 0:
                return int(value)
        except (TypeError, ValueError, OverflowError):
            pass
    return fallback


def completion_input_fingerprint(request: HostJobTurnIntent, resolved: Mapping[str, str]) -> str:
    """Idempotency covers immutable facts/authority, not transient instructions.

    The inbox retains the first admitted payload. A retry cannot replace its model,
    presentation, or context snapshot by changing these non-identity fields.
    """
    if not completion_metadata(request, resolved):
        return ""
    identity = {
        "context": dict(resolved),
        "event_type": request.event_type,
        "source": request.source,
        "data": request.data,
        "memory_mode": request.memory_mode,
        "presentation_mode": request.presentation_mode,
        "presentation_prefix": request.presentation_prefix,
        "presentation_suffix": request.presentation_suffix,
        "strip_addresses": request.strip_leading_addresses,
    }
    return hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def completion_batch_key(item: SessionInboxItem) -> str:
    metadata = item.payload.get("host_completion")
    payload = item.payload.get("turn_payload")
    if item.kind != "turn" or not isinstance(metadata, dict) or not metadata or not isinstance(payload, dict):
        return ""
    if payload.get("turn_kind") != "plugin_event":
        return ""
    event = payload.get("plugin_external_event")
    if (
        not isinstance(event, dict)
        or event.get("source") != "host.jobs"
        or event.get("event_type")
        not in {
            "job.succeeded",
            "job.failed",
            "job.cancelled",
        }
    ):
        return ""
    fields = event.get("data")
    if not isinstance(fields, dict) or not fields.get("origin_turn_id"):
        return ""
    if metadata.get("origin_turn_id") != fields["origin_turn_id"]:
        return ""
    if item.source_event_id != f"job-completed:{fields.get('job_id', '')}":
        return ""
    # Compare the *whole* remaining execution/presentation context. New routing
    # or authorization fields automatically become fences, not silent omissions.
    context = copy.deepcopy(payload)
    for key in (
        "message",
        "memory_message",
        "timestamp",
        "source_message_id",
        "memory_idempotency_key",
        "plugin_external_event",
    ):
        context.pop(key, None)
    delivery = context.get("qq_delivery_context")
    if isinstance(delivery, dict):
        for key in ("clean_message", "raw_message", "source_message_id"):
            delivery.pop(key, None)
    material = [item.source, item.session_key, item.profile_user_id, item.session_id, metadata, context]
    return hashlib.sha256(json.dumps(material, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def completion_turn_payload(items: list[SessionInboxItem]) -> dict[str, Any]:
    if not items:
        raise ValueError("completion_items_required")
    payload = copy.deepcopy(items[0].payload.get("turn_payload") or {})
    if len(items) == 1:
        if completion_batch_key(items[0]):
            payload["message"] = str(payload.get("message") or "") + (
                "\n本通知只说明所列任务的终态；其他任务的当前状态未知，不要推断它们仍在运行或整项请求已全部完成。"
            )
        return payload
    key = completion_batch_key(items[0])
    if not key or any(completion_batch_key(item) != key for item in items[1:]):
        raise ValueError("incompatible_completion_batch")
    batch_id = items[0].batch_id
    if not batch_id or any(item.batch_id != batch_id for item in items):
        raise ValueError("completion_batch_not_frozen")
    fields = {"job_count": str(len(items))}
    messages = [
        f"以下 {len(items)} 个后台任务已有终态，现集中处理。",
        "逐项保留成功、失败和取消结果；不要重新执行已完成任务。",
        "需要交付的产物走正常发送能力；支持多个目标时集中提交，并统一说明真实结果。",
        "产物可用不等于已发送。未列出的任务状态未知，不要推断仍在运行或整项请求已全部完成。",
    ]
    for index, item in enumerate(items):
        original = item.payload["turn_payload"]
        event = original["plugin_external_event"]
        prefix = f"job_{index:03d}_"
        fields[prefix + "event_id"] = item.source_event_id
        fields[prefix + "event_type"] = event["event_type"]
        for name, value in event["data"].items():
            if len(prefix + name) > 64:
                raise ValueError("completion_field_name_too_long")
            fields[prefix + name] = value
        messages.append(str(original.get("message") or ""))
    payload["message"] = "\n\n".join(messages)
    payload["memory_message"] = payload["message"]
    payload["memory_idempotency_key"] = f"host-completions:{batch_id}"
    payload["source_message_id"] = f"host-completions:{batch_id}"
    payload["plugin_external_event"] = {"event_type": "jobs.completed", "source": "host.jobs", "data": fields}
    delivery = payload.get("qq_delivery_context")
    if isinstance(delivery, dict):
        delivery.update(
            clean_message=payload["message"],
            raw_message=payload["message"],
            source_message_id=payload["source_message_id"],
        )
    return payload
