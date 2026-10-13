"""A child's persistent history and safe checkpoints for oversized open tasks."""
from __future__ import annotations

import json
import time
import uuid
from typing import Any

from .text_utils import estimate_prompt_tokens
from .engine_services.response_budget import _trim_oldest_provider_history_group
from .engine_services.response_catalog import _overlay_ephemeral_provider_evidence


class SubagentMemory:
    def __init__(self, engine, request, llm, target, *, fixed_tokens: int = 0):
        self.engine, self.manager, self.request = engine, engine.memcore_manager, request
        self.target = target
        self.scope = dict(profile_user_id=request.child_session_id, session_id=request.child_session_id, character_pack_id="")
        self.run_id = request.run_id or uuid.uuid4().hex
        self.segment = 0
        self.turn_id = self.source_id = ""
        self.input_content = ""
        self.past: list[dict[str, Any]] = []
        self.current_messages: list[dict[str, Any]] = []
        self.past_messages: list[dict[str, Any]] = []
        self.prefix_mode = getattr(self.manager._memory_config, "compaction_request_mode", "independent") == "chat_prefix"
        settings = llm._settings_view()
        configured = settings.llm_auto_compact_token_limit
        if configured:
            configured = max(1, configured - fixed_tokens)
        if settings.llm_context_window > 0:
            reserve = settings.llm_chat_max_output_tokens or min(8192, settings.llm_context_window // 4)
            capacity = settings.llm_context_window - reserve - fixed_tokens
            if capacity <= 0:
                raise RuntimeError("subagent_prompt_exceeds_context_window")
            configured = min(configured, capacity) if configured else capacity
        trigger = self.manager._memory_config.raw_token_trigger
        self.hard_limit = configured
        self.limit = min(trigger, configured) if configured else trigger
        self.manager.bind_subagent_runtime(request.child_session_id, llm, token_limit=self.limit)
        self.trimmed_messages = 0

    def open(self, content: str) -> None:
        if self.segment == 0:
            self.manager.prepare_subagent_run(self.request.child_session_id)
        self.input_content = content
        self.source_id = f"{self.request.child_session_id}:{self.run_id}:input:{self.segment}"
        opened = self.manager.begin_input_turn({"source_id": self.source_id, "role": "user", "content": content,
                                               "timestamp": int(time.time())}, **self.scope)
        if not opened.get("ok") or not opened.get("writable", True):
            raise RuntimeError("subagent_trace_open_failed")
        self.turn_id = str(opened.get("turn_id") or "")
        if not self.turn_id:
            raise RuntimeError("subagent_trace_open_failed")
        self.load_past()

    def load_past(self):
        projected = self.manager.build_context_projection(provider_profile=self.target.protocol, **self.scope)
        if not projected.get("ok"):
            raise RuntimeError("subagent_history_projection_failed")
        self.past = [dict(item["payload"]) for item in projected["messages"]
                     if item.get("turn_id") != self.turn_id and item.get("payload")]
        while self.past and estimate_prompt_tokens(json.dumps(self.past, ensure_ascii=False, default=str)) > self.limit:
            self.trimmed_messages += _trim_oldest_provider_history_group(self.past)

    def current(self) -> list[dict[str, Any]]:
        if self.prefix_mode:
            surface = self.manager.build_context_surface(provider_profile=self.target.protocol,
                                                         current_source_id=self.source_id, **self.scope)
            if not surface.get("ok"):
                raise RuntimeError("subagent_input_projection_failed")
            history = list(surface.get("history_messages") or [])
            current = surface.get("current_message")
            active = list(surface.get("active_turn_messages") or [])
            metadata = list(surface.get("message_projection_metadata") or [])
            self.past = [dict(item) for item in history]
            self.past_messages = [{**dict(meta), "payload": dict(payload)}
                                  for payload, meta in zip(history, metadata[:len(history)])]
            payloads = ([current] if current else []) + active
            self.current_messages = [{**dict(meta), "payload": dict(payload)}
                                     for payload, meta in zip(payloads, metadata[len(history):])]
            if current and isinstance(current.get("content"), str):
                self.input_content = current["content"]
            return [dict(item) for item in active]
        projected = self.manager.build_open_turn_projection(turn_id=self.turn_id, provider_profile=self.target.protocol, **self.scope)
        if not projected.get("ok"):
            raise RuntimeError("subagent_input_projection_failed")
        self.current_messages = projected.get("messages", [])
        return [dict(item["payload"]) for item in self.current_messages
                if self.source_id not in item.get("source_ids", []) and item.get("payload")]

    def observer(self, *, pending_task_ids=()):
        observer = self.engine._build_memcore_request_observer(generation_context={
            "history_turns": self.past, "memcore_history_start_index": 0, "memcore_projection_read": {
            "provider_profile": self.target.protocol, "current_turn_id": self.turn_id,
            "history_message_source_ids": [item.get("source_ids", []) for item in self.past_messages],
            "current_turn_messages": self.current_messages}}, **self.scope)
        if observer is None or not self.prefix_mode:
            return observer
        def observe(request):
            request["_pending_task_ids"] = list(pending_task_ids)
            return observer(request)
        observe.capture_compaction_context = bool(getattr(observer, "capture_compaction_context", False))
        return observe

    def checkpoint_if_due(self, history: list[dict[str, Any]], *, pending_ids: list[str]) -> bool:
        tokens = estimate_prompt_tokens(json.dumps([*self.past, *history], ensure_ascii=False, default=str))
        if self.prefix_mode:
            if self.hard_limit and tokens >= self.hard_limit:
                self.manager.compact_due_sync(provider_profile=self.target.protocol, **self.scope)
            # Adoption happens at a request boundary. Keep the same open turn
            # and pending jobs; never invent a terminal reply to force compaction.
            previous = list(history)
            current = self.current()
            history[:] = _overlay_ephemeral_provider_evidence(
                current, previous,
                allow_folded_history=any(str(item.get("turn_id") or "").startswith("working.")
                                         for item in self.current_messages),
            )
            return history != previous
        if tokens < self.limit or not history:
            return False
        closed = self.manager.complete_input_turn(turn_id=self.turn_id, assistant_record={},
            memory_metadata=None, append_final=False, provider_profile=self.target.protocol, **self.scope)
        if not closed.get("ok"):
            raise RuntimeError("subagent_checkpoint_failed")
        self.turn_id = ""
        self.segment += 1
        self.manager.compact_due_sync(provider_profile=self.target.protocol, **self.scope)
        self.open("Continue the current task: " + self.request.task
                  + ("\nPending work: " + ", ".join(pending_ids) if pending_ids else ""))
        history[:] = self.current()
        return True

    def close(self, result, raw_text: str = "") -> None:
        if not self.turn_id:
            return
        report = {"status": result.status, "summary": result.summary, "reason": result.reason,
                  "artifacts": list(result.artifacts)}
        raw = raw_text or json.dumps(report, ensure_ascii=False)
        closed = self.manager.complete_input_turn(turn_id=self.turn_id,
            assistant_record={"source_id": f"{self.request.child_session_id}:{self.run_id}:result",
                              "role": "assistant", "content": result.summary or result.reason, "timestamp": int(time.time())},
            memory_metadata=None, provider_output_raw=raw, provider_profile=self.target.protocol,
            provider_projection={"role": "assistant", "content": raw}, **self.scope)
        if not closed.get("ok"):
            self.manager.abort_input_turn(turn_id=self.turn_id, reason="subagent_trace_complete_failed", **self.scope)
            raise RuntimeError("subagent_trace_complete_failed")
        self.turn_id = ""
        self.manager.compact_due_background(provider_profile=self.target.protocol, **self.scope)
