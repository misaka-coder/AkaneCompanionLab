"""Project durable background job completions into timeline or Agent turns."""

from __future__ import annotations

import asyncio
from typing import Any

from ..host_jobs import HostJob
from ..plugin_turn_intents import HostJobTurnIntent, HostTurnResult


async def _dispatch_host_job_completion(engine: Any, router: Any, job: HostJob) -> HostTurnResult:
    from ..tool_continuation import job_followup
    from ..host_completion_batch import job_delivery_events

    store = getattr(engine, "job_store", None)
    current = store.get(job.job_id, owner=job.owner) if store is not None else job
    if current is None or current.completion_event_id != job.completion_event_id:
        return HostTurnResult(False, "failed", "host_job_completion_identity_mismatch")
    job = current
    if job.model_consumed_by:
        return HostTurnResult(True, "consumed", "completion_already_in_model_input")
    if not job_followup(job).requires_model:
        recorded = await asyncio.to_thread(_record_host_job_completion, engine, job)
        if not recorded.ok or not job_delivery_events(job):
            return recorded
    return await router.submit(_host_job_completion_request(job))


def _record_host_job_completion(engine: Any, job: HostJob) -> HostTurnResult:
    """Persist an opted-in terminal fact without creating a model/delivery turn."""
    if job.memory_mode != "timeline":
        return HostTurnResult(True, "silent", "")
    request = _host_job_completion_request(job)
    result = engine.record_plugin_timeline_event(
        {
            "source_id": job.completion_event_id,
            "event": {"event_type": request.event_type, "source": request.source, "fields": dict(request.data)},
            "timestamp": int(job.finished_at),
            "user_id": job.owner.session_id,
            "real_user_id": job.owner.profile_user_id,
            "character_pack_id": job.character_pack_id,
        }
    )
    if not isinstance(result, dict) or result.get("ok") is not True:
        return HostTurnResult(
            False,
            "failed",
            str(
                result.get("reason", "timeline_record_failed") if isinstance(result, dict) else "timeline_record_failed"
            ),
        )
    return HostTurnResult(True, "recorded", "")


def _host_job_completion_request(job: HostJob) -> HostJobTurnIntent:
    """Project one durable Job terminal fact into the normal Agent turn path."""

    status = str(job.status or "failed").strip().lower()
    succeeded = status == "succeeded"
    summary = str(job.result_summary or "").strip()[:4_000 if job.capability_source == "subagent" else 3_000]
    error = str(job.last_error or "").strip()[:500]
    handles = [
        str(item.get("handle") or "").strip()
        for item in job.artifacts
        if isinstance(item, dict) and str(item.get("handle") or "").strip()
    ]
    handle_text = ", ".join(handles)[:2_000]
    delivery_requests = ", ".join(
        f"{item['handle']}={item['delivery_mode']}"
        for item in job.artifacts
        if isinstance(item, dict)
        and item.get("handle")
        and item.get("send_to_user") is True
        and item.get("delivery_mode") in ("file", "voice", "both")
    )[:2_000]
    fields: list[tuple[str, str]] = [
        ("job_id", job.job_id),
        ("capability_id", job.capability_id),
        ("status", status),
        ("finished_at", str(job.finished_at)),
    ]
    if job.turn_id:
        fields.append(("origin_turn_id", job.turn_id))
    agent_id = str(job.payload.get("child_session_id") or "") if job.capability_source == "subagent" else ""
    if agent_id:
        fields.append(("agent_id", agent_id))
    if job.tool_call_id:
        fields.append(("tool_call_id", job.tool_call_id))
    task_directory = str(job.payload.get("working_directory") or "").strip()
    if task_directory:
        fields.append(("task_working_directory", task_directory))
    if summary:
        fields.append(("result_summary", summary))
    if handle_text:
        fields.append(("artifact_handles", handle_text))
        fields.append(("artifact_delivery_status", "available_not_delivered"))
        if delivery_requests:
            fields.append(("artifact_delivery_requests", delivery_requests))
        hashes = ", ".join(
            f"{item['handle']}={item['sha256']}"
            for item in job.artifacts
            if isinstance(item, dict) and item.get("handle") and item.get("sha256")
        )[:2_000]
        if hashes:
            fields.append(("artifact_sha256", hashes))
    if error:
        fields.append(("error", error))
    lines = [
        f"后台任务 {job.capability_id} 已{'完成' if succeeded else '结束'}。",
        f"job_id: {job.job_id}",
        f"status: {status}",
    ]
    if agent_id:
        lines.append(f"agent_id: {agent_id}")
    if summary:
        lines.append(f"结果摘要：{summary}")
    if task_directory:
        lines.append(f"该任务的执行目录：{task_directory}（不是切换当前会话的工作目录）。")
    if handle_text:
        lines.append(f"可用产物：{handle_text}")
        lines.append("这些产物已登记但尚未由本完成事件确认发送；需要交付时使用当前渠道的正常发送能力。")
        if delivery_requests:
            lines.append(
                f"原任务请求的交付形式：{delivery_requests}。这是发送意图，不是已发送状态；通过正常渠道发送能力完成交付。"
            )
    if error:
        lines.append(f"失败原因：{error}")
    return HostJobTurnIntent(
        message="\n".join(lines),
        data={key: value for key, value in fields},
        source="host.jobs",
        event_type=f"job.{status}",
        conversation_ref=job.delivery_target,
        trace_id=job.completion_event_id,
        idempotency_key=job.completion_event_id,
        memory_mode=job.memory_mode,
    )
