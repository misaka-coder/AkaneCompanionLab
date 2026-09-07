"""Pure projection for host Job completions, shared by all durable channels.

Execution and ownership stay in HostJobStore. The session inbox freezes a
bounded envelope at claim time; this module neither schedules jobs nor sends
artifacts. Plugin-authored notices are deliberately not batchable here.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from typing import Any, Mapping

from .plugin_api import PluginAgentEventRequest
from .session_inbox import SessionInboxItem


def completion_metadata(request: PluginAgentEventRequest, resolved: Mapping[str, str]) -> dict[str, Any]:
    if request.event.source != "host.jobs" or request.event.event_type not in {
        "job.succeeded",
        "job.failed",
        "job.cancelled",
    }:
        return {}
    fields = dict(request.event.fields)
    job_id = fields.get("job_id", "")
    origin = fields.get("origin_turn_id", "")
    if not job_id or not origin or request.memory_idempotency_key != f"job-completed:{job_id}":
        return {}
    return {"origin_turn_id": origin, "context": dict(resolved)}


def completion_timestamp(request: PluginAgentEventRequest, fallback: int) -> int:
    if request.event.source == "host.jobs":
        try:
            value = float(dict(request.event.fields).get("finished_at", ""))
            if math.isfinite(value) and value > 0:
                return int(value)
        except (TypeError, ValueError, OverflowError):
            pass
    return fallback


def completion_input_fingerprint(request: PluginAgentEventRequest, resolved: Mapping[str, str]) -> str:
    """Idempotency covers immutable facts/authority, not transient instructions.

    The inbox retains the first admitted payload. A retry cannot replace its model,
    presentation, or context snapshot by changing these non-identity fields.
    """
    if not completion_metadata(request, resolved):
        return ""
    identity = {
        "context": dict(resolved),
        "event_type": request.event.event_type,
        "source": request.event.source,
        "fields": dict(request.event.fields),
        "memory_mode": request.delivery,
        "text_delivery": request.text_delivery,
        "text_prefix": request.text_prefix,
        "text_suffix": request.text_suffix,
        "strip_addresses": request.text_strip_leading_addresses,
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
    fields = event.get("fields")
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
        for name, value in event["fields"].items():
            if len(prefix + name) > 64:
                raise ValueError("completion_field_name_too_long")
            fields[prefix + name] = value
        messages.append(str(original.get("message") or ""))
    payload["message"] = "\n\n".join(messages)
    payload["memory_message"] = payload["message"]
    payload["memory_idempotency_key"] = f"host-completions:{batch_id}"
    payload["source_message_id"] = f"host-completions:{batch_id}"
    payload["plugin_external_event"] = {"event_type": "jobs.completed", "source": "host.jobs", "fields": fields}
    delivery = payload.get("qq_delivery_context")
    if isinstance(delivery, dict):
        delivery.update(
            clean_message=payload["message"],
            raw_message=payload["message"],
            source_message_id=payload["source_message_id"],
        )
    return payload
