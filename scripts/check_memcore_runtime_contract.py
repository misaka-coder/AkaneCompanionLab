"""Read-only deployment gate for MemCore action completion and chat authorship.

Run with the exact interpreter/environment that will launch the backend.
No model calls, credentials, databases, or user messages are used.
"""

from __future__ import annotations

import inspect
import json


def check_runtime() -> dict:
    try:
        from memcore import MemoryConfig, MemorySystem, PROJECTION_VERSION, TimelineEntry, build_native_memory_tool_specs
        from memcore.projection import ProjectionAdapter, default_renderer_registry
    except ImportError:
        return {"status": "failed", "reason": "memcore_contract_import_unavailable"}
    if PROJECTION_VERSION < 6:
        return {"status": "failed", "reason": "chat_authorship_v6_required", "projection_version": PROJECTION_VERSION}
    if "append_final" not in inspect.signature(MemorySystem.complete_turn).parameters:
        return {"status": "failed", "reason": "action_only_completion_required"}
    if (not callable(getattr(MemorySystem, "import_completed_turn", None))
            or not callable(getattr(MemorySystem, "get_turn", None))
            or "roles" not in inspect.signature(MemorySystem.get_turn_entries).parameters
            or "kinds" not in inspect.signature(MemorySystem.get_turn_entries).parameters
            or "defer_index" not in inspect.signature(MemorySystem.import_completed_turn).parameters):
        return {"status": "failed", "reason": "completed_history_import_required"}
    if ("retrieval_mode" not in inspect.signature(MemoryConfig).parameters
            or "retrieval_mode" not in inspect.signature(build_native_memory_tool_specs).parameters):
        return {"status": "failed", "reason": "memory_retrieval_mode_required"}
    navigation_tools = build_native_memory_tool_specs(retrieval_mode="navigation", tool_format="plain",
                                                     include_material_tool=False)
    if {item["name"] for item in navigation_tools} != {"browse_memory", "open_memory", "read_timeline"}:
        return {"status": "failed", "reason": "navigation_tool_contract_mismatch"}
    adapter = ProjectionAdapter(renderer_registry=default_renderer_registry(), timezone="Asia/Shanghai")
    for raw in ("hello back", '{"emotion":"smile","speech":"hello back"}'):
        entry = TimelineEntry.from_record(
            {
                "source_id": "deployment-contract-fixture",
                "user_id": "deployment-contract-fixture",
                "conversation_id": "deployment-contract-fixture",
                "kind": "message.assistant",
                "origin": "assistant",
                "role": "assistant",
                "turn_role": "final",
                "turn_id": "deployment-contract-fixture",
                "timestamp": 1788707829,
                "semantic_text": "hello back",
                "payload": {"provider_output_raw": raw},
            }
        )
        for profile in ("openai_chat", "openai_responses", "anthropic_messages"):
            payload = adapter.context_surface_payload(entry, provider_profile=profile)
            if payload is None:
                return {"status": "failed", "reason": "assistant_authorship_surface_unavailable"}
            content = payload.get("content")
            if isinstance(content, list):
                content = content[0].get("text") if len(content) == 1 else None
            if payload.get("role") != "assistant" or content != raw:
                return {"status": "failed", "reason": "assistant_authorship_changed", "provider_profile": profile}
    entry = TimelineEntry.from_record({
        "source_id": "channel-contract-fixture", "user_id": "deployment-contract-fixture",
        "conversation_id": "deployment-contract-fixture", "kind": "message.user",
        "origin": "user", "role": "user", "semantic_text": "hello",
        "payload": {"text": "hello", "channel": "desktop"}, "timestamp": 1788707829,
    })
    for profile in ("openai_chat", "openai_responses", "anthropic_messages"):
        payload = adapter.context_surface_payload(entry, provider_profile=profile)
        if "channel: desktop" not in json.dumps(payload):
            return {"status": "failed", "reason": "user_channel_projection_required"}
    return {"status": "ok", "projection_version": PROJECTION_VERSION, "authorship_cases_checked": 6,
            "channel_cases_checked": 3}


def main() -> int:
    try:
        result = check_runtime()
    except Exception as exc:
        result = {"status": "failed", "reason": "runtime_contract_check_failed", "error_type": type(exc).__name__}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
