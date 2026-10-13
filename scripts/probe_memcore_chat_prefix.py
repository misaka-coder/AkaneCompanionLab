"""Synthetic, isolated prefix-compaction probe; --live explicitly calls the configured chat API.

Default runs only local request/capture timing. No production DB, QQ/UI, or tool
execution is used. Credentials are read in-process and never included in output.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import statistics
import sys
import tempfile
import threading
from time import perf_counter
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config
from companion_v01.llm_runtime import LLMRuntime, ModelBundle
from companion_v01.memcore_integration.adapters import build_akane_llm_client
from companion_v01.memcore_integration.manager import MemcoreManager
from companion_v01.model_runtime.memory_prefix import CapturedCompletionRequest
from companion_v01.runtime_settings import BotSettingsView
from memcore import (
    Actor,
    ChatContextSnapshot,
    EntryOrigin,
    MemoryConfig,
    MemorySystem,
    Namespace,
    ProjectionMessageInput,
    SQLiteMemoryStore,
    TimelineEntryInput,
    TurnRole,
)
from memcore.prompts import build_summary_prompts


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "inspect_fixture",
            "description": "Read an isolated test report.",
            "parameters": {"type": "object", "properties": {"report": {"type": "string"}}},
        },
    }
]
SYSTEM = (
    "You are a test development assistant. Normal replies are JSON with completed and pending strings. "
    "Internal memory maintenance requests use their own memory JSON. Do not call tools in this probe. "
    "Background-only fact, do not turn it into a new event: BACKGROUND_ONLY_SENTINEL is a historical label."
)


def timing(values):
    return {"median_ms": round(statistics.median(values), 2), "max_ms": round(max(values), 2)}


def local_probe(root, samples):
    llm = LLMRuntime.__new__(LLMRuntime)
    llm.settings = BotSettingsView(llm_context_window=1000000, llm_chat_max_output_tokens=2048)
    llm._metrics, llm._metrics_lock, llm._bundle_lock = {}, threading.RLock(), threading.RLock()
    llm.chat = ModelBundle(
        NS(_akane_protocol="openai", _akane_bundle_role="chat", base_url="https://fixture.invalid"), "fixture"
    )
    llm._record_prompt_audit_if_enabled = Mock()
    with patch.object(config, "MEMCORE_COMPACTION_REQUEST_MODE", "chat_prefix"):
        manager = MemcoreManager(
            backend="memcore",
            storage_path=root / "local.db",
            visible_scope="conversation",
            enable_flavor=False,
            shadow_compare=False,
            llm=llm,
            embedding_provider=None,
            retrieval_mode="navigation",
        )
    try:
        scope = dict(profile_user_id="probe", session_id="probe", character_pack_id="")
        result = {}
        for target in (4000, 48000, 200000):
            body = ("fixture report: completed a verified change; more work follows. " * (target // 20))[: target * 4]
            history = [
                {
                    "role": "assistant" if i % 2 else "user",
                    "content": f"part {i}: " + body[i * len(body) // 20 : (i + 1) * len(body) // 20],
                }
                for i in range(20)
            ]
            kwargs = dict(
                system_prompt=SYSTEM,
                user_prompt="continue",
                temperature=0.1,
                native_tools=TOOLS,
                history_turns=history,
                prompt_cache_key="fixture-prefix-probe",
            )
            context = {
                "history_turns": history,
                "memcore_history_start_index": 0,
                "memcore_projection_read": {
                    "provider_profile": "openai_chat",
                    "history_message_source_ids": [[f"fixture-{i}"] for i in range(20)],
                },
            }
            base, enabled = [], []
            with patch.object(manager, "compact_due_background", return_value={"ok": True, "status": "fixture"}):
                for index in range(samples + 2):
                    started = perf_counter()
                    payload = llm._build_completion_kwargs(
                        bundle=llm.chat, json_mode=False, validate_budget=False, observe_request=False, **kwargs
                    )
                    elapsed = (perf_counter() - started) * 1000
                    if index >= 2:
                        base.append(elapsed)
                    started = perf_counter()
                    captured = llm.capture_memcore_generation_request(**kwargs)
                    manager.capture_compaction_request(
                        request={"_compaction_request": captured}, generation_context=context, **scope
                    )
                    elapsed = (perf_counter() - started) * 1000
                    if index >= 2:
                        enabled.append(elapsed)
            estimate = llm._estimate_prompt_payload_tokens(payload)
            result[str(target)] = {
                "estimated_tokens": estimate,
                "assembly": timing(base),
                "assembly_and_capture": timing(enabled),
                "added_median_ms": round(statistics.median(enabled) - statistics.median(base), 2),
            }
        return result
    finally:
        manager.close()


def live_probe(root):
    settings = replace(BotSettingsView.from_config(config), llm_chat_max_output_tokens=2048)
    llm = LLMRuntime(settings=settings, log_dir=root / "logs", instance_id="memcore-prefix-probe")
    # Same provider/model for the independent control; only this isolated runtime
    # changes. The production summary model and saved settings are untouched.
    llm.memcore_summary = ModelBundle(llm.chat.client, llm.chat.model)
    records, phase = [], ["prime"]
    create = llm._create_completion

    def traced(**kwargs):
        started = perf_counter()
        response = create(**kwargs)
        usage = getattr(response, "usage", None)
        total = llm._usage_int(usage, "prompt_tokens") or llm._usage_int(usage, "input_tokens")
        cached = (
            llm._usage_int(usage, "prompt_cache_hit_tokens")
            or llm._usage_int(usage, "cache_read_input_tokens")
            or llm._nested_usage_int(usage, "prompt_tokens_details", "cached_tokens")
            or llm._nested_usage_int(usage, "input_tokens_details", "cached_tokens")
        )
        records.append(
            {
                "phase": phase[0],
                "model": kwargs["bundle"].model,
                "input_tokens": total,
                "cached_tokens": cached,
                "cache_read_reported": llm._cache_read_reported(usage),
                "cache_percent": round(cached * 100 / total, 2) if total else None,
                "provider_ms": round((perf_counter() - started) * 1000, 2),
            }
        )
        return response

    llm._create_completion = traced
    store = SQLiteMemoryStore(str(root / "live.db"))
    mem = MemorySystem(
        llm=build_akane_llm_client(llm),
        namespace=Namespace(user_id="synthetic-probe", conversation_id="probe"),
        timezone="Asia/Shanghai",
        store=store,
        config=MemoryConfig(
            retrieval_mode="navigation",
            compaction_request_mode="chat_prefix",
            working_token_trigger=1000,
            raw_token_trigger=12000,
            operation_projection_policy="compact_arguments_after_terminal",
            episodic_compact_trigger_count=99,
            llm_timeout_s=120,
            llm_max_retries=1,
            projection_profile="openai_chat",
        ),
    )
    try:
        for i in range(24):
            text = (
                f"SCOPE_A project. Part {i}: fix queue handling in fixture_queue.py; keep new messages while development runs. "
                + "Verified fixtures cover delayed callbacks, repeated requests, cancellation, and retries without replaying tools. "
                * 22
            ).strip()
            turn = mem.begin_turn(
                turn_id=f"t{i}",
                opened_at=100 + i * 2,
                stimuli=[
                    TimelineEntryInput(
                        source_id=f"u{i}",
                        kind="message.user",
                        origin=EntryOrigin.USER,
                        turn_role=TurnRole.STIMULUS,
                        actor=Actor(stable_id="owner", display_name="fixture owner"),
                        semantic_text=text,
                        payload={"text": text},
                        timestamp=100 + i * 2,
                    )
                ],
            )
            mem.complete_turn(
                turn_id=turn.turn_id,
                source_id=f"a{i}",
                semantic_text="The synthetic queue patch is completed and its focused checks passed.",
                provider_output_raw='{"speech":"The synthetic queue patch is completed and its focused checks passed."}',
                timestamp=101 + i * 2,
            )

        def captured_request(user, post=(), pending=()):
            projection = mem.build_context_projection(provider_profile="openai_chat")
            capture = llm.capture_memcore_generation_request(
                system_prompt=SYSTEM,
                user_prompt=user,
                temperature=0.1,
                history_turns=list(projection.payloads),
                post_user_turns=list(post),
                native_tools=TOOLS,
                native_tool_choice="none",
                prompt_cache_key="akane:isolated-prefix-probe",
            )
            groups = ((), *(m.source_ids for m in projection.messages), (), *(tuple() for _ in post))
            public = capture.public_messages()
            active = [i for i, m in enumerate(projection.messages) if m.turn_id == "working-probe"]
            if active:
                mem.record_request_projection(
                    turn_id="working-probe",
                    provider_profile="openai_chat",
                    turn_messages=[
                        ProjectionMessageInput(
                            provider_profile="openai_chat",
                            payload=public[i + 1],
                            source_ids=projection.messages[i].source_ids,
                            projection_index=projection.messages[i].projection_index,
                        )
                        for i in active
                    ],
                    history_messages=public[1:],
                    history_message_indexes=active,
                    system_prefix=SYSTEM,
                    tool_schema=TOOLS,
                )
            mem.capture_chat_context(
                ChatContextSnapshot("openai_chat", tuple(public), tuple(groups), capture, tuple(pending))
            )
            return projection, capture

        before, captured = captured_request(
            'TAIL_B_NOT_IN_SOURCE: a future request. Reply only {"completed":"ready","pending":"await instruction"}.'
        )
        prime = llm._create_completion(bundle=captured.bundle, payload=captured.payload)
        if llm._response_finish_reason(prime) == "tool_calls":
            raise RuntimeError("probe_prime_unexpected_tool_call")
        phase[0] = "stable_chat_before_compaction"
        warm = deepcopy(captured.payload)
        warm["messages"].extend(
            [
                {"role": "assistant", "content": llm._extract_text(prime)},
                {"role": "user", "content": "Continue briefly in the same JSON."},
            ]
        )
        llm._create_completion(bundle=captured.bundle, payload=warm)
        warm_capture = CapturedCompletionRequest.capture(
            captured.bundle, warm, context_window=settings.llm_context_window
        )
        mem.capture_chat_context(
            ChatContextSnapshot(
                "openai_chat",
                tuple(warm_capture.public_messages()),
                ((), *(m.source_ids for m in before.messages), (), (), ()),
                warm_capture,
            )
        )
        phase[0] = "raw_prefix_summary"
        stats = mem.compact_due_sync(provider_profile="openai_chat")
        if stats.get("summary_request_mode") != "chat_prefix" or stats.get("status") != "compacted":
            raise RuntimeError(
                "probe_raw_prefix_not_selected: "
                + str({key: stats.get(key) for key in ("status", "reason", "summary_request_mode")})
            )
        summaries = store.get_visible_episodic_summaries(namespace=mem.namespace, limit=99, cross_conversation=False)
        episode = next(s for s in summaries if s.get("kind") == "memory.episode_summary")
        summary_text = json.dumps(episode, ensure_ascii=False)
        scoped = "TAIL_B_NOT_IN_SOURCE" not in summary_text and "BACKGROUND_ONLY_SENTINEL" not in summary_text
        phase[0] = "independent_control"
        selected = set(stats["summary_source_ids"])
        control_system, control_user = build_summary_prompts(
            transcript="\n".join(str(m.payload) for m in before.messages if selected.intersection(m.source_ids)),
            batch_size=stats["source_entry_count"],
        )
        control = llm.call_memcore_json(
            system_prompt=control_system,
            user_prompt=control_user,
            fallback={},
            prompt_cache_key="memcore:isolated-control",
            request_timeout_s=120,
        )
        phase[0] = "post_compaction_chat_rebuild"
        _, after_capture = captured_request(
            "Explain completed queue changes and pending work in completed/pending JSON; concise."
        )
        resumed = llm._create_completion(bundle=after_capture.bundle, payload=after_capture.payload)
        text = llm._extract_text(resumed)
        continued = bool(llm._extract_json(text).get("completed")) and "queue" in text.lower()
        phase[0] = "stable_chat"
        stable = deepcopy(after_capture.payload)
        stable["messages"] = [
            *stable["messages"],
            {"role": "assistant", "content": text},
            {"role": "user", "content": "Reply briefly in the same JSON format."},
        ]
        latest = llm._create_completion(bundle=after_capture.bundle, payload=stable)
        for index in (2, 3):
            phase[0] = f"stable_chat_{index}"
            stable["messages"].extend(
                [
                    {"role": "assistant", "content": llm._extract_text(latest)},
                    {"role": "user", "content": "Continue briefly in the same JSON."},
                ]
            )
            latest = llm._create_completion(bundle=after_capture.bundle, payload=stable)
        mem.config.raw_token_trigger = 999999
        mem.begin_turn(
            turn_id="working-probe",
            opened_at=200,
            stimuli=[
                TimelineEntryInput(
                    source_id="working-input",
                    kind="message.user",
                    origin=EntryOrigin.USER,
                    turn_role=TurnRole.STIMULUS,
                    semantic_text="Implement queue fixes; verify them; then update the docs after the pending fixture finishes.",
                    payload={
                        "text": "Implement queue fixes; verify them; then update the docs after the pending fixture finishes."
                    },
                    timestamp=200,
                )
            ],
        )
        for i in range(6):
            mem.record_tool_exchange(
                turn_id="working-probe",
                tool_name="inspect_fixture",
                tool_call_id=f"tool-{i}",
                tool_input={"report": f"fixture-{i}.json"},
                source="fixture",
                source_id_prefix=f"operation-{i}",
                result="Queue patch in fixture_queue.py is verified. Synthetic checks passed; update docs next. " * 60
                + "Pending async job synthetic-pending-A is still running; read its result before final delivery.",
            )
        phase[0] = "working_prime"
        _, working_capture = captured_request(
            "Report current progress in normal JSON; do not claim final delivery.", pending=["synthetic-pending-A"]
        )
        llm._create_completion(bundle=working_capture.bundle, payload=working_capture.payload)
        phase[0] = "working_prefix_summary"
        working_stats = mem.compact_due_sync(provider_profile="openai_chat")
        if working_stats.get("working_status") != "committed":
            raise RuntimeError(
                "probe_working_prefix_not_committed: "
                + str({key: working_stats.get(key) for key in ("status", "reason", "working_status")})
            )
        working_projection = mem.build_context_projection(provider_profile="openai_chat")
        working_summary = next(
            m.payload["content"] for m in working_projection.messages if "[working summary;" in str(m.payload)
        )
        phase[0] = "post_working_summary_resume"
        _, resume_capture = captured_request(
            "Report completed verification and remaining work in completed/pending JSON. "
            "Do not claim the pending job has finished.",
            pending=["synthetic-pending-A"],
        )
        working_resumed = llm._create_completion(bundle=resume_capture.bundle, payload=resume_capture.payload)
        working_reply = llm._extract_json(llm._extract_text(working_resumed))
        continued_working = bool(working_reply.get("completed")) and "synthetic-pending-A" in str(
            working_reply.get("pending")
        )
        return {
            "calls": records,
            "source_range_respected": scoped,
            "continued_with_task_facts": continued,
            "raw_summary_created": stats["summaries_created"],
            "control_valid": bool(control.get("diary_summary")),
            "before_estimated_tokens": stats["before_raw_projected_tokens"],
            "after_estimated_tokens": stats["after_raw_projected_tokens"],
            "working_before_estimated_tokens": working_stats["before_raw_projected_tokens"],
            "working_after_estimated_tokens": working_stats["after_raw_projected_tokens"],
            "working_summary_has_goal_and_pending_id": "fixture_queue.py" in working_summary
            and "synthetic-pending-A" in working_summary,
            "continued_working_with_pending_id": continued_working,
        }
    finally:
        mem.close()
        store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=15)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if args.samples < 1:
        parser.error("samples must be positive")
    with tempfile.TemporaryDirectory(prefix="akane-memcore-prefix-probe-") as folder:
        root = Path(folder)
        result = {"local": local_probe(root, args.samples)}
        print(json.dumps(result, ensure_ascii=False), flush=True)
        if args.live:
            print(json.dumps({"live": live_probe(root)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
