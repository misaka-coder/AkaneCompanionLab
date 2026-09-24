"""Durable execution bridge for control-center workflow jobs."""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .host_jobs import HostJob, HostJobOwner, HostJobStore
from .local_workflow_execution import (
    WorkflowExecutionAsset,
    WorkflowExecutionRequest,
    call_workflow_execution_runner,
    normalize_workflow_asset_handle,
)


WORKFLOW_JOB_SOURCE = "workflow"
_HOST_JOB_ID_RE = re.compile(r"^job_[a-f0-9]{32}$")
logger = logging.getLogger("akane.host_workflow_jobs")


class WorkflowJobAssetStore:
    """Keep workflow bytes outside SQLite behind opaque job/asset identities."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def write(self, job_id: str, kind: str, asset: WorkflowExecutionAsset) -> None:
        target = self._path(job_id, kind, asset.handle)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_bytes(bytes(asset.data))
            os.replace(temporary, target)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def read(self, job_id: str, kind: str, handle: str) -> bytes | None:
        target = self._path(job_id, kind, handle)
        try:
            return target.read_bytes() if target.is_file() else None
        except OSError:
            return None

    def remove_inputs(self, job_id: str, handles: list[str]) -> None:
        self._remove(job_id, "input", handles)

    def remove_outputs(self, job_id: str, handles: list[str]) -> None:
        self._remove(job_id, "output", handles)

    def _remove(self, job_id: str, kind: str, handles: list[str]) -> None:
        for handle in handles:
            try:
                self._path(job_id, kind, handle).unlink(missing_ok=True)
            except OSError:
                pass

    def _path(self, job_id: str, kind: str, handle: str) -> Path:
        clean_job_id = str(job_id or "").strip()
        clean_kind = str(kind or "").strip().lower()
        normalized = normalize_workflow_asset_handle(handle)
        if not _HOST_JOB_ID_RE.fullmatch(clean_job_id) or clean_kind not in {"input", "output"} or not normalized.get("ok"):
            raise ValueError("workflow_job_asset_identity_invalid")
        digest = hashlib.sha256(str(normalized["handle"]).encode("utf-8")).hexdigest()
        return self.root / clean_job_id / clean_kind / digest


class HostWorkflowJobRuntime:
    """Run configured workflows against the shared durable Host Job authority."""

    def __init__(
        self,
        *,
        store: HostJobStore,
        asset_store: WorkflowJobAssetStore,
        workflow_runner: Any,
        background_tasks: Any,
        executor_broker: Any,
    ) -> None:
        self.store = store
        self.asset_store = asset_store
        self.workflow_runner = workflow_runner
        self.background_tasks = background_tasks
        self.executor_broker = executor_broker
        self._scheduled: set[str] = set()
        self._lock = threading.RLock()

    def start(
        self,
        *,
        preflight: Mapping[str, Any],
        profile_user_id: str,
        session_id: str,
        input_assets: Mapping[str, WorkflowExecutionAsset],
    ) -> dict[str, Any]:
        owner = HostJobOwner(profile_user_id, session_id)
        payload = _workflow_payload(preflight, input_assets)
        idempotency_key = f"workflow:{uuid.uuid4().hex}"
        fingerprint = "sha256:" + hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        created = self.store.create(
            owner=owner,
            capability_source=WORKFLOW_JOB_SOURCE,
            capability_id=str(preflight.get("capabilityId") or preflight.get("workflowId") or "workflow"),
            payload=payload,
            idempotency_key=idempotency_key,
            argument_fingerprint=fingerprint,
            channel="control_center",
            completion_mode="silent",
            memory_mode="current_turn",
        )
        if not created.get("ok"):
            return {"ok": False, "status": "failed", "reason": str(created.get("reason") or "workflow_job_create_failed")}
        job_id = str(created.get("job_id") or "")
        written_inputs: list[str] = []
        try:
            for asset in input_assets.values():
                self.asset_store.write(job_id, "input", asset)
                written_inputs.append(asset.handle)
        except Exception:
            if self._fail_queued(job_id, "workflow_input_persist_failed"):
                self.asset_store.remove_inputs(job_id, written_inputs)
            job = self.store.get(job_id, owner=owner)
            return self._start_result(job, ok=False)
        scheduled = self._schedule(job_id)
        if not scheduled:
            if self._fail_queued(job_id, "workflow_scheduler_failed"):
                self.asset_store.remove_inputs(job_id, written_inputs)
        job = self.store.get(job_id, owner=owner)
        return self._start_result(job, ok=scheduled)

    def recover(self) -> int:
        scheduled = 0
        for job_id in self.store.pending_job_ids(capability_source=WORKFLOW_JOB_SOURCE):
            if self._schedule(job_id):
                scheduled += 1
        return scheduled

    def get(self, job_id: str, *, owner: HostJobOwner) -> HostJob | None:
        job = self.store.get(job_id, owner=owner)
        return job if job is not None and job.capability_source == WORKFLOW_JOB_SOURCE else None

    def public(self, job: HostJob) -> dict[str, Any]:
        payload = job.payload if isinstance(job.payload, dict) else {}
        status = _public_status(job.status)
        reason = _public_reason(job)
        created_at = _iso(job.created_at)
        updated_at = _iso(job.finished_at or job.started_at or job.created_at)
        events = [{"status": "queued", "reason": "workflow_job_submitted", "createdAt": created_at}]
        if job.started_at:
            events.append({"status": "running", "reason": "workflow_running", "createdAt": _iso(job.started_at)})
        if job.status in {"succeeded", "failed", "cancelled"}:
            events.append({"status": status, "reason": reason, "createdAt": updated_at})
        return {
            "jobId": job.job_id,
            "kind": "workflow_job",
            "workflowId": str(payload.get("workflow_id") or ""),
            "capabilityId": job.capability_id,
            "status": status,
            "reason": reason,
            "executionReady": status == "queued",
            "canRun": False,
            "createdAt": created_at,
            "updatedAt": updated_at,
            "inputs": copy.deepcopy(payload.get("inputs") or {}),
            "checks": copy.deepcopy(payload.get("checks") or {}),
            "runner": {"bound": True, "lane": "workflow", "backgroundTaskId": "", "reason": ""},
            "outputs": [dict(item) for item in job.artifacts if isinstance(item, Mapping)],
            "events": events[-20:],
            **({"resourceLimit": copy.deepcopy(job.result["resource_limit"])}
               if isinstance(job.result.get("resource_limit"), dict) else {}),
            **({"policyFailure": copy.deepcopy(job.result["policy_failure"])}
               if isinstance(job.result.get("policy_failure"), dict) else {}),
        }

    def output(self, job: HostJob, handle: str) -> WorkflowExecutionAsset | None:
        if job.status != "succeeded":
            return None
        normalized = normalize_workflow_asset_handle(handle)
        if not normalized.get("ok"):
            return None
        clean_handle = str(normalized.get("handle") or "")
        record = next(
            (
                item
                for item in job.artifacts
                if isinstance(item, Mapping) and str(item.get("handle") or "") == clean_handle
            ),
            None,
        )
        if record is None:
            return None
        data = self.asset_store.read(job.job_id, "output", clean_handle)
        if data is None:
            return None
        return WorkflowExecutionAsset(
            handle=clean_handle,
            data=data,
            content_type=str(record.get("contentType") or "application/octet-stream"),
        )

    def _schedule(self, job_id: str) -> bool:
        with self._lock:
            if job_id in self._scheduled:
                return True
            self._scheduled.add(job_id)
        try:
            self.background_tasks.submit(
                lane="workflow",
                name="capability-workflow",
                fn=self._run,
                args=(job_id,),
            )
            return True
        except Exception:
            with self._lock:
                self._scheduled.discard(job_id)
            return False

    def _run(self, job_id: str) -> None:
        claim_token = ""
        job: HostJob | None = None
        input_handles: list[str] = []
        output_handles: list[str] = []
        terminal_settled = False
        try:
            claimed = self.store.claim(job_id, worker_id=f"workflow:{threading.get_ident()}", lease_seconds=3600)
            if not claimed.get("ok"):
                return
            claim_token = str(claimed.get("claim_token") or "")
            job = claimed.get("job") if isinstance(claimed.get("job"), HostJob) else None
            if job is None or job.capability_source != WORKFLOW_JOB_SOURCE:
                raise RuntimeError("workflow_job_record_invalid")
            payload = job.payload if isinstance(job.payload, dict) else {}
            input_assets: dict[str, WorkflowExecutionAsset] = {}
            for record in list(payload.get("input_assets") or []):
                if not isinstance(record, Mapping):
                    continue
                handle = str(record.get("handle") or "")
                data = self.asset_store.read(job.job_id, "input", handle)
                if data is None:
                    raise RuntimeError("workflow_input_asset_unavailable")
                input_handles.append(handle)
                input_assets[handle] = WorkflowExecutionAsset(
                    handle=handle,
                    data=data,
                    content_type=str(record.get("contentType") or "application/octet-stream"),
                )
            request = WorkflowExecutionRequest(
                job_id=job.job_id,
                workflow_id=str(payload.get("workflow_id") or ""),
                capability_id=job.capability_id,
                profile_user_id=job.owner.profile_user_id,
                session_id=job.owner.session_id,
                inputs=dict(payload.get("inputs") or {}),
                input_assets=input_assets,
                workflow=copy.deepcopy(payload.get("workflow") or {}),
            )
            broker = self.executor_broker.execute_server_local(
                tool_id=request.capability_id or request.workflow_id or "workflow",
                invocation_id=job.job_id,
                dispatch=lambda: call_workflow_execution_runner(self.workflow_runner, request),
                retain_result=False,
                input_arguments=dict(request.inputs or {}),
                policy_client_mode="control_center",
                ledger_scope=f"{request.profile_user_id}\x1f{request.session_id}",
                request_data={
                    "workflow_id": request.workflow_id,
                    "capability_id": request.capability_id,
                    "inputs": dict(request.inputs or {}),
                },
            )
            if broker.status == "succeeded" and broker.result is None:
                settled = self.store.fail(
                    job.job_id,
                    claim_token=claim_token,
                    error="workflow_execution_result_unavailable",
                    retryable=False,
                )
                terminal_settled = _is_terminal_settlement(settled)
                return
            if broker.status != "succeeded" or not isinstance(broker.result, Mapping):
                reason = str(broker.reason or "workflow_runner_failed")
                if reason == "server_local_dispatch_failed":
                    reason = "workflow_runner_failed"
                settled = self.store.fail(job.job_id, claim_token=claim_token, error=reason, retryable=False,
                    result={key: getattr(broker, key) for key in ("resource_limit", "policy_failure") if getattr(broker, key, None) is not None})
                terminal_settled = _is_terminal_settlement(settled)
                return
            result = dict(broker.result)
            if not result.get("ok"):
                settled = self.store.fail(
                    job.job_id,
                    claim_token=claim_token,
                    error=str(result.get("reason") or "workflow_runner_failed"),
                    retryable=False,
                )
                terminal_settled = _is_terminal_settlement(settled)
                return
            outputs = [dict(item) for item in list(result.get("outputs") or []) if isinstance(item, Mapping)]
            allowed_handles = {str(item.get("handle") or "") for item in outputs}
            for asset in list(result.get("outputAssets") or []):
                if isinstance(asset, WorkflowExecutionAsset) and asset.handle in allowed_handles:
                    self.asset_store.write(job.job_id, "output", asset)
                    output_handles.append(asset.handle)
            settled = self.store.succeed(
                job.job_id,
                claim_token=claim_token,
                result_summary=str(result.get("reason") or "workflow_completed"),
                artifacts=outputs,
            )
            if not settled.get("ok"):
                raise RuntimeError("workflow_job_settle_failed")
            terminal_settled = True
        except Exception as exc:
            if claim_token and job is not None:
                settled = self.store.fail(
                    job.job_id,
                    claim_token=claim_token,
                    error=str(exc) if str(exc).startswith("workflow_") else f"workflow_{type(exc).__name__}",
                    retryable=False,
                )
                terminal_settled = _is_terminal_settlement(settled)
            logger.exception("host workflow job failed: %s", job_id)
        finally:
            if job is not None and terminal_settled:
                self.asset_store.remove_inputs(job.job_id, input_handles)
            if job is not None and output_handles and not terminal_settled:
                self.asset_store.remove_outputs(job.job_id, output_handles)
            with self._lock:
                self._scheduled.discard(job_id)

    def _fail_queued(self, job_id: str, reason: str) -> bool:
        claim = self.store.claim(job_id, worker_id="workflow-admission", lease_seconds=30)
        if claim.get("ok"):
            settled = self.store.fail(
                job_id,
                claim_token=claim.get("claim_token"),
                error=reason,
                retryable=False,
            )
            return _is_terminal_settlement(settled)
        return False

    def _start_result(self, job: HostJob | None, *, ok: bool) -> dict[str, Any]:
        if job is None:
            return {"ok": False, "status": "failed", "reason": "workflow_job_unavailable"}
        public = self.public(job)
        status = "queued" if ok else public["status"]
        reason = "workflow_job_submitted" if ok else public["reason"]
        return {
            "ok": bool(ok),
            "status": status,
            "reason": reason,
            "executionReady": bool(ok),
            "canRun": False,
            "jobId": job.job_id,
            "jobStatus": public["status"],
            "job": public,
        }


def _workflow_payload(
    preflight: Mapping[str, Any],
    input_assets: Mapping[str, WorkflowExecutionAsset],
) -> dict[str, Any]:
    accepted = preflight.get("acceptedInputs") if isinstance(preflight.get("acceptedInputs"), Mapping) else {}
    checks = preflight.get("checks") if isinstance(preflight.get("checks"), Mapping) else {}
    return {
        "workflow_id": str(preflight.get("workflowId") or ""),
        "workflow": copy.deepcopy(preflight.get("workflow") or {}),
        "inputs": {
            "inputImageHandle": str(accepted.get("inputImageHandle") or ""),
            "outputImageHandle": str(accepted.get("outputImageHandle") or ""),
        },
        "input_assets": [
            {"handle": asset.handle, "contentType": asset.content_type}
            for asset in input_assets.values()
        ],
        "checks": {
            "providerConfigured": bool(checks.get("providerConfigured")),
            "workflowConfigured": bool(checks.get("workflowConfigured")),
            "inputImageHandle": bool(checks.get("inputImageHandle")),
            "outputImageHandle": bool(checks.get("outputImageHandle")),
            "runnerBound": True,
        },
    }


def _public_status(status: str) -> str:
    return "completed" if status == "succeeded" else str(status or "failed")


def _public_reason(job: HostJob) -> str:
    if job.status == "queued":
        return "workflow_job_submitted"
    if job.status == "running":
        return "workflow_running"
    if job.status == "succeeded":
        return str(job.result_summary or "workflow_completed")
    return str(job.last_error or f"workflow_{job.status or 'failed'}")


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(float(timestamp or 0), timezone.utc).isoformat()


def _is_terminal_settlement(result: Mapping[str, Any]) -> bool:
    return bool(result.get("ok")) and str(result.get("status") or "") in {"succeeded", "failed", "cancelled"}


__all__ = ["HostWorkflowJobRuntime", "WORKFLOW_JOB_SOURCE", "WorkflowJobAssetStore"]
