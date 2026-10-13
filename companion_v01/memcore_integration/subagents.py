"""Task namespaces reuse MemCore without companion persona or voice reminders."""
from __future__ import annotations

from typing import Any
from dataclasses import replace
import time


class _SessionSummaryRuntime:
    def __init__(self, manager: Any, key: tuple[str, str, str]) -> None:
        self.manager, self.key = manager, key

    def call_memcore_json(self, **kwargs):
        with self.manager._lock:
            runtime = self.manager._session_llms[self.key]
        return runtime.call_memcore_json(**kwargs)

    def call_memcore_prefix_json(self, **kwargs):
        with self.manager._lock:
            runtime = self.manager._session_llms[self.key]
        return runtime.call_memcore_prefix_json(**kwargs)


class MemcoreSubagentsMixin:
    def prepare_subagent_run(self, agent_id: str) -> None:
        """Close only orphaned slices before a new, exclusively admitted run.

        Host Jobs allow one live run per child. A process crash can leave its
        previous MemCore slice open; waiting for the ordinary stale-age window
        would block compaction of the recovered history. This public store
        operation retains the records and marks their outcome as interrupted.
        """
        system = self._get_system(profile_user_id=agent_id, session_id=agent_id, character_pack_id="")
        now = int(time.time())
        system.store.abort_stale_open_turns(namespace=system.namespace, opened_before=now + 1,
            closed_at=now, reason="subagent_previous_execution_interrupted")

    def _subagent_summary_runtime(self, key):
        self._session_llms.setdefault(key, self.llm)
        return _SessionSummaryRuntime(self, key)

    def _subagent_memory_config(self, key):
        limit = self._session_token_limits.get(key, 0)
        trigger = self._memory_config.raw_token_trigger
        return replace(self._memory_config, visible_memory_scope="conversation",
                       raw_token_trigger=min(trigger, limit) if limit else trigger,
                       working_token_trigger=min(self._memory_config.working_token_trigger, limit)
                       if limit and self._memory_config.working_token_trigger else self._memory_config.working_token_trigger)

    @staticmethod
    def _subagent_prompt_overrides():
        from memcore.prompts import PromptOverrides
        return PromptOverrides(extra_summary_guidance="Summarize task goals, completed work, checks, unresolved issues and artifact IDs. "
                               "Distinguish instructions from verified results; preserve what is needed to continue the task.")

    def bind_subagent_runtime(self, agent_id: str, llm: Any, *, token_limit: int = 0) -> None:
        key = (agent_id, agent_id, "")
        with self._lock:
            if key in self._deleting_namespaces:
                raise RuntimeError("subagent_memory_deleting")
            self._session_llms[key] = llm
            self._session_token_limits[key] = max(0, int(token_limit))
            system = self._systems.get(key)
            if system is not None:
                # MemoryConfig is public and mutable; the writer shares this
                # instance. Apply a new run's budget without recreating its
                # history or touching the parent's configuration.
                system.config.raw_token_trigger = self._subagent_memory_config(key).raw_token_trigger
                system.config.working_token_trigger = self._subagent_memory_config(key).working_token_trigger

    def forget_subagent(self, agent_id: str, *, retry_source_ids: list[str] | None = None) -> dict[str, Any]:
        """Quiesce only this namespace, then delete its records and index entries."""
        key = (agent_id, agent_id, "")
        with self._lock:
            system = self._systems.get(key)
            if system is None:
                self._deleting_namespaces.discard(key)
                system = self._get_system(profile_user_id=agent_id, session_id=agent_id,
                                          character_pack_id="", warm_index=False)
            self._deleting_namespaces.add(key)
            hard_key = self._compaction_namespace_key(system)
            self._pending_compactions.pop(hard_key, None)
            system.request_shutdown()
            futures = [self._background_compactions.get(hard_key), self._index_warmup_futures.get(hard_key)]
            for future in futures:
                if future is not None:
                    future.cancel()
            if any(future is not None and (not future.done() or future in self._background_futures) for future in futures):
                return {"ok": False, "status": "deleting", "reason": "subagent_memory_work_stopping"}
        if retry_source_ids and system.index is not None:
            try:
                system.index.delete(retry_source_ids)
            except Exception:
                return {"ok": False, "status": "partial", "reason": "subagent_index_delete_failed",
                        "retry_source_ids": retry_source_ids}
        result = system.forget_namespace()
        if not result.get("ok"):
            return {"ok": False, "status": "partial", "reason": "subagent_index_delete_failed",
                    "retry_source_ids": result.get("source_ids", [])}
        with self._lock:
            self._systems.pop(key, None)
            self._session_llms.pop(key, None)
            self._session_token_limits.pop(key, None)
            self._warmed_index_keys.discard(hard_key)
            self._index_warmup_futures.pop(hard_key, None)
            self._compaction_retry_after.pop(hard_key, None)
        return {"ok": True, "status": "deleted", "deleted_count": result.get("deleted_count", 0)}
