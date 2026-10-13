"""Live-turn references over HostJobStore; no second task or output authority."""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable

from .host_jobs import HostJob, HostJobOwner
from .task_work import TaskWork
from .tool_continuation import job_followup
from .tool_handlers.core import ToolExecutionResult, ToolFollowupEnvelope

logger = logging.getLogger("akane.host_completion_work")
COMPLETION_BATCH_MAX_BYTES = 128 * 1024
COMPLETION_BATCH_MAX_ITEMS = 16


def _scope(job):
    return (job.owner, job.character_pack_id, job.channel, job.turn_id)


@dataclass
class CompletionEvidence:
    job: HostJob
    result: ToolExecutionResult


class TurnCompletionWork:
    def __init__(self, registry, key, *, request_bound=False, cancelled=None):
        self.registry, self.key = registry, key
        self.work = TaskWork()
        self.references = {}
        self.ready = {}
        self.presentations = {}
        self.offered = {}
        self.queried = {}
        self.terminal_observations = set()
        self.request_bound = request_bound
        self.request_jobs = {}
        self.cancelled = cancelled or (lambda: False)
        self.condition = threading.Condition(threading.RLock())
        self.closed = False
        self._remaining_bytes = 0
        self._remaining_items = 0

    def track(self, job, *, read=None, cancel=None, wait=True):
        with self.condition:
            if self.closed:
                return False
            # The reader/cancel closures retain the original execution owner.
            self.references.setdefault(job.job_id, (job, read, cancel, wait))
            if read is not None:
                old = self.references[job.job_id]
                self.references[job.job_id] = (job, read, cancel, wait)
            self.work.track(job.job_id, inspect=lambda: self._inspect(job.job_id),
                            cancel=cancel or (lambda: self.registry.store.request_cancel(job.job_id, owner=job.owner)))
            self.condition.notify_all()
        return True

    def notify(self, job):
        with self.condition:
            if self.closed:
                return False
            if job.job_id not in self.references:
                self.track(job)
            self.ready[job.job_id] = job
            self.condition.notify_all()
        self.work.notify()
        return True

    def query(self, job, *, terminal_observed=False):
        with self.condition:
            if self.closed:
                return False
            # Restored evidence has no in-memory admission reference. Keep it
            # until a response confirms receipt, or close releases it unread.
            self.references.setdefault(job.job_id, (job, None, None, False))
            self.queried[job.job_id] = job
            if terminal_observed:
                self.terminal_observations.add(job.job_id)
            self.work.acknowledge(job.job_id)
            self.condition.notify_all()
        return True

    def observe_request(self, jobs):
        """Freeze only terminal evidence in the actual provider input.

        Replace (never union) the previous attempt: a failed request followed
        by a successful request which omits a result cannot consume that result.
        The observer restores references, not durable consumption receipts.
        """
        with self.condition:
            if self.closed:
                return
            self.request_jobs = {job.job_id: job for job in jobs}
            for job in jobs:
                self.query(job)

    def observe(self, job, mapped):
        with self.condition:
            reference = self.references.get(job.job_id)
            observe = getattr(reference[1], "observe", None) if reference else None
            if callable(observe):
                observe(mapped)

    def discard(self, job_id):
        with self.condition:
            self.work.acknowledge(job_id)
            self.references.pop(job_id, None)
            self.ready.pop(job_id, None)
            self.condition.notify_all()

    def _inspect(self, job_id):
        with self.condition:
            job = self.ready.get(job_id)
            if job is None or job_id in self.queried or not self._remaining_items or self._remaining_bytes <= 0:
                return None
            if not job_followup(job).requires_model:
                return None
            evidence = self.presentations.get(job_id)
            reference = self.references[job_id]
            self._remaining_items -= 1
        # Only ready references are read. No history/job-library scan occurs.
        current = self.registry.store.completion_state(job)
        if current is None or current["completion_event_id"] != job.completion_event_id:
            raise RuntimeError("host_job_completion_identity_mismatch")
        if current["model_consumed_by"] or current["scope_revoked_reason"]:
            self.discard(job_id)
            return None
        if evidence is None:
            read = reference[1]
            from .host_tool_job_runtime.results import completion_result
            result = read() if read is not None else completion_result(job)
            if read is None:
                handles = [str(item.get("handle") or "") for item in job.artifacts
                           if item.get("handle") and str(item["handle"]) not in result.followup_context]
                if handles:
                    result.followup_context = "available artifacts: " + ", ".join(handles) + "\n" + result.followup_context
            metadata = job.result.get("model_presentation")
            if read is None and isinstance(metadata, dict):
                result.followup_envelope = ToolFollowupEnvelope(content=result.followup_context,
                    producer_bounded=bool(metadata.get("producer_bounded")),
                    complete=bool(metadata.get("complete", True)), continuation=metadata.get("continuation"))
            if len(result.followup_context.encode("utf-8")) > COMPLETION_BATCH_MAX_BYTES and not (
                result.followup_envelope and result.followup_envelope.producer_bounded):
                result = self.registry.bound_result(job, result)
            evidence = CompletionEvidence(job, result)
            with self.condition:
                self.presentations[job_id] = evidence
        size = len(evidence.result.followup_context.encode("utf-8"))
        with self.condition:
            if size > self._remaining_bytes:
                return None
            self._remaining_bytes -= size
        return {"evidence": evidence}

    def collect(self):
        self._remaining_bytes = COMPLETION_BATCH_MAX_BYTES
        self._remaining_items = COMPLETION_BATCH_MAX_ITEMS
        collected = [item["evidence"] for item in self.work.collect()]
        with self.condition:
            self.offered.update((item.job.job_id, item) for item in collected)
        return collected

    def commit(self):
        """A valid response proves the frozen input reached the model."""
        with self.condition:
            if self.closed or self.cancelled():
                return
            jobs = (dict(self.request_jobs) if self.request_bound else
                    {**{key: value.job for key, value in self.offered.items()}, **self.queried})
        if jobs:
            receipt = self.registry.store.consume_model_completions(list(jobs.values()), consumed_by=self.key[-1],
                terminal_observed_ids=self.terminal_observations, mark_delivered=True)
            if not receipt.get("ok"):
                raise RuntimeError("model_completion_receipt_not_saved")
        for job_id in jobs:
            with self.condition:
                self.offered.pop(job_id, None)
                self.queried.pop(job_id, None)
                self.ready.pop(job_id, None)
                self.presentations.pop(job_id, None)
                self.references.pop(job_id, None)
                self.terminal_observations.discard(job_id)
                self.request_jobs.pop(job_id, None)
        # Model consumption is not user delivery. Non-model artifact delivery
        # still follows its original completion policy on release.

    @property
    def pending(self):
        with self.condition:
            return self.work.pending

    @property
    def pending_ids(self):
        """Read tracked task IDs without collecting or acknowledging results."""
        with self.condition:
            return self.work.pending_ids

    def waiting_ids(self):
        with self.condition:
            return [key for key, (job, _read, _cancel, wait) in self.references.items()
                    if wait and key not in self.ready and key not in self.queried
                    and job_followup(job).requires_model]

    def wait(self, cancelled, *, deadline=None):
        """Wait for actual work, with controls; never poll the model."""
        with self.condition:
            while not cancelled():
                waiting = self.waiting_ids()
                if not waiting:
                    return
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    return
                self.condition.wait(timeout=min(0.2, remaining) if remaining is not None else 0.2)


class HostCompletionWork:
    """Route a terminal fact to its live origin or the existing idle dispatcher."""
    def __init__(self, store, publish, *, bound_result=None):
        self.store, self.publish = store, publish
        from .host_completion_presentation import bound_completion_result
        self.bound_result = bound_result or bound_completion_result
        self._active = {}
        self._lock = threading.RLock()

    def begin(self, *, owner: HostJobOwner, character_pack_id: str, channel: str, turn_id: str,
              request_bound=False, cancelled=None):
        if not turn_id:
            return None
        key = (owner, character_pack_id, channel, turn_id)
        with self._lock:
            if key in self._active:
                raise RuntimeError("completion_turn_already_active")
            work = TurnCompletionWork(self, key, request_bound=request_bound, cancelled=cancelled)
            self._active[key] = work
        return work

    def track(self, job, **kwargs):
        if job.model_consumed_by:
            return False
        with self._lock:
            work = self._active.get(_scope(job))
            return bool(work is not None and work.track(job, **kwargs))

    def current(self, *, owner, character_pack_id, channel, turn_id):
        with self._lock:
            return self._active.get((owner, character_pack_id, channel, turn_id))

    def request_work(self, *, owner, character_pack_id, turn_id):
        """Find the sole live request owner; never guess across channels."""
        with self._lock:
            matches = [work for key, work in self._active.items()
                       if key[0] == owner and key[1] == character_pack_id and (not turn_id or key[3] == turn_id)]
            return matches[0] if len(matches) == 1 else None

    def hold(self, job):
        with self._lock:
            work = self._active.get(_scope(job))
            if job.model_consumed_by or not job_followup(job).requires_model:
                if work is not None:
                    work.discard(job.job_id)
                return False
            return bool(work is not None and work.notify(job))

    def observe(self, job, mapped):
        with self._lock:
            work = self._active.get(_scope(job))
            if work is not None:
                work.observe(job, mapped)

    def query(self, job, *, consumed_by, terminal_observed=False):
        with self._lock:
            # A legitimate query can be in a later turn. Execution ownership
            # was checked by the original provider before reaching here.
            work = self._active.get((job.owner, job.character_pack_id, job.channel, consumed_by))
            if work is not None and work.query(job, terminal_observed=terminal_observed):
                return True
        # A tool return is not a model response. Without a live request owner
        # leave the fact unread; recovery/idle delivery can still present it.
        return False

    def close(self, work):
        if work is None:
            return
        with self._lock, work.condition:
            if work.closed:
                return
            self._active.pop(work.key, None)
            work.closed = True
            references = tuple(work.references.values())
            work.condition.notify_all()
        # Do not cancel intentional background work on normal turn completion.
        # Stop/revocation still uses the original coordinator and providers.
        for original, _read, _cancel, _wait in references:
            job = self.store.get(original.job_id, owner=original.owner)
            if job is not None and job.completion_status == "pending":
                self.publish(job)


__all__ = ["HostCompletionWork", "TurnCompletionWork", "CompletionEvidence"]
