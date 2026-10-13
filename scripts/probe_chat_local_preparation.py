"""Measure local text preparation without touching production or calling an API.

Run from the repository with its Python environment. --baseline-ref compares
only the two preparation entry functions; shared services use the current
checkout. This is a local fixture benchmark, not QQ transport or UI latency.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
import math
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
from time import perf_counter
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config
from companion_v01.engine import AkaneMemoryEngine
from companion_v01.engine_services import response_builder, turn_preparation
from companion_v01.llm_runtime import LLMRuntime
from companion_v01.runtime_settings import BotSettingsView


def baseline_function(ref, name, function):
    source = subprocess.check_output(["git", "show", f"{ref}:companion_v01/engine_services/{name}.py"], cwd=ROOT)
    module = ModuleType("companion_v01.engine_services._benchmark_" + name)
    module.__package__ = "companion_v01.engine_services"
    exec(compile(source.decode("utf-8"), name + ".py", "exec"), module.__dict__)
    return getattr(module, function)


def measure(engine, functions, mode, label, samples, *, request_boundary=False):
    prepare_turn, prepare_context = functions
    timings = []
    for index in range(samples + 2):
        started = perf_counter()
        turn = prepare_turn(engine, {"user_id": label, "real_user_id": "local-probe",
            "client_mode": mode, "message": "你好，接着聊聊今天的安排。"}, mode="stream")
        context = prepare_context(engine, session_id=turn.identity.session_id,
            profile_user_id=turn.identity.profile_user_id, user_message=turn.request.user_message,
            recent_raw=turn.prompt.recent_raw, recent_episodic_summaries=[], recent_semantic_summaries=[],
            confirmed_snippets=[], now_ts=turn.request.now_ts, client_context=turn.request.client_context,
            extra_user_context=turn.prompt.extra_user_context, resource_manifest=turn.prompt.resource_manifest,
            character_pack_id=turn.identity.character_pack_id, enable_native_tools=True,
            execution_target=turn.prompt.execution_target, current_user_source_id=turn.memory.source_id,
            request_projection_state=turn.prompt.request_projection_state)
        if context.get("memcore_projection_failure"):
            raise RuntimeError("local_probe_projection_failed")
        if request_boundary:
            llm = engine.llm
            bundle = turn.prompt.execution_target.bundle
            payload = llm._build_completion_kwargs(bundle=bundle, system_prompt=context["system_prompt"],
                user_prompt=context["user_prompt"], temperature=.1, history_turns=context.get("history_turns"),
                ephemeral_turns=context.get("ephemeral_turns"), post_user_turns=context.get("post_user_turns"),
                system_extra_blocks=context.get("system_extra_blocks"), native_tools=context.get("native_tools"),
                native_tool_choice=context.get("native_tool_choice"), prompt_cache_key=engine._final_prompt_cache_key(context))
            observer = engine._build_memcore_request_observer(generation_context=context,
                profile_user_id=turn.identity.profile_user_id, session_id=turn.identity.session_id,
                character_pack_id=turn.identity.character_pack_id, request_projection_state=turn.prompt.request_projection_state)
            persistent = llm._persistent_turn_messages_from_payload(payload=payload, bundle=bundle,
                history_turns=context.get("history_turns"), ephemeral_turns=context.get("ephemeral_turns"),
                post_user_turns=context.get("post_user_turns"))
            llm._observe_completion_request(bundle=bundle, payload=payload, observer=observer, persistent_turn_messages=persistent)
        elapsed = (perf_counter() - started) * 1000
        closed = engine.memcore_manager.complete_input_turn(profile_user_id=turn.identity.profile_user_id,
            session_id=turn.identity.session_id, character_pack_id=turn.identity.character_pack_id,
            turn_id=turn.memory.turn_id,
            assistant_record={"source_id": f"{label}:{index}:answer", "role": "assistant", "content": "好呀。",
                              "timestamp": turn.request.now_ts}, memory_metadata=None,
            provider_output_raw='{"emotion":"normal","utterances":[{"text":"好呀。","say":"はい。","style":"穏やか"}]}',
            provider_profile="openai_chat")
        if not closed.get("ok"):
            raise RuntimeError("local_probe_turn_close_failed")
        if index >= 2:
            timings.append(elapsed)
    ordered = sorted(timings)
    return {"samples": samples, "median_ms": round(statistics.median(timings), 2),
            "p95_ms": round(ordered[math.ceil(samples * .95) - 1], 2), "max_ms": round(max(timings), 2)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=20)
    parser.add_argument("--baseline-ref", default="")
    parser.add_argument("--request-boundary", action="store_true", help="include request assembly and MemCore observation; no API")
    parser.add_argument("--compaction-mode", choices=("independent", "chat_prefix"), default="independent")
    args = parser.parse_args()
    if args.samples < 1:
        parser.error("--samples must be positive")
    versions = {"current": (turn_preparation.prepare_turn, response_builder.prepare_context)}
    if args.baseline_ref:
        versions = {"baseline": (baseline_function(args.baseline_ref, "turn_preparation", "prepare_turn"),
                                baseline_function(args.baseline_ref, "response_builder", "prepare_context")), **versions}
    with tempfile.TemporaryDirectory(prefix="akane-local-preparation-") as folder, ExitStack() as stack:
        root = Path(folder)
        for name, value in dict(DATA_DIR=str(root / "users"), LOG_DIR=str(root / "logs"),
            AKANE_WORKSPACE_ROOT=str(root / "workspace"), MEMORY_BACKEND="memcore",
            MEMCORE_STORAGE_PATH=str(root / "memory.db"), MEMCORE_RETRIEVAL_MODE="navigation",
            MEMCORE_COMPACTION_REQUEST_MODE=args.compaction_mode).items():
            stack.enter_context(patch.object(config, name, value, create=True))
        client = SimpleNamespace(base_url="http://local-probe.invalid/v1", _akane_protocol="openai_chat")
        stack.enter_context(patch.object(LLMRuntime, "_build_llm_client", return_value=client))
        stack.enter_context(patch.object(LLMRuntime, "_build_memcore_summary_bundle", return_value=None))
        engine = AkaneMemoryEngine(root / "engine", settings=BotSettingsView(chat_api_key="fixture-only",
            chat_base_url="http://local-probe.invalid/v1", chat_model_name="fixture"))
        stack.callback(engine.close)
        print(json.dumps({version: {mode: measure(engine, functions, mode, version + "-" + mode, args.samples,
                                               request_boundary=args.request_boundary)
            for mode in ("qq_text", "desktop_pet")} for version, functions in versions.items()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
