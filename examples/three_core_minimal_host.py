"""Runnable MemCore + CapCore + channelcore-onebot reference host.

This example deliberately imports no Akane ``companion_v01`` modules.  It uses
one deterministic chat-model stand-in so the package integration can be tested
without credentials or network access.  Production hosts must replace the
stand-in and the hashed demo embedding with real providers.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from importlib import metadata
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Mapping

import capcore
import channelcore_onebot
import memcore
from capcore import ApprovalPolicy, CapabilityIOSlot, InvocationContext
from capcore_adapter_python import PythonCapabilityAdapter, PythonCapabilitySpec
from capcore_provider_openai import (
    build_openai_chat_tool_messages,
    build_openai_chat_tool_set,
    parse_openai_chat_tool_calls,
    route_capabilities_from_adapter,
    run_openai_chat_tool_invocation,
)
from channelcore_onebot import (
    OutboundTarget,
    ReplyReferenceLedger,
    build_message_action,
    normalize_inbound_event,
    text_segment,
)
from memcore import (
    AnnotationStatus,
    EntryOrigin,
    HashedEmbeddingProvider,
    InMemoryVectorIndex,
    LLMClient,
    LLMRequest,
    LLMResult,
    MemoryConfig,
    MemorySystem,
    Namespace,
    OPENAI_PROFILE,
    OperationProjectionPolicy,
    SQLiteMemoryStore,
    TimelineEntryInput,
    TurnRole,
    build_native_memory_tool_specs,
    dispatch_native_memory_tool,
)


STABLE_SYSTEM_PROMPT = """You are a host-neutral assistant.
Use native tools when the answer depends on runtime facts. Preserve tool call
ids and never claim a tool succeeded when its structured result says otherwise.
Some completed tool results may appear as [compact_reloadable] cards. Open the
card by source_id only when its exact body is needed; open tool rounds stay full.
"""

_PUBLIC_CONTRACTS: dict[str, tuple[str, ...]] = {
    "memcore": (
        "MemorySystem",
        "MemoryConfig",
        "Namespace",
        "TimelineEntryInput",
        "build_native_memory_tool_specs",
        "dispatch_native_memory_tool",
        "parse_chat_output",
        "StreamingSpeechParser",
    ),
    "capcore": (
        "CapabilityDescriptor",
        "CapabilityResult",
        "CapabilityToolSpec",
        "InvocationContext",
        "ApprovalPolicy",
        "build_tool_specs",
        "prepare_invocation",
        "capability_success",
        "capability_error",
    ),
    "channelcore-onebot": (
        "InboundMessage",
        "MessageChain",
        "normalize_inbound_event",
        "OutboundTarget",
        "build_message_action",
        "ReplyReferenceLedger",
        "resolve_quoted_message",
        "resolve_forward_message",
    ),
}


class DemoMemoryLLM(LLMClient):
    """Deterministic compaction adapter; production must inject a real LLM."""

    def call(self, request: LLMRequest) -> LLMResult:
        return LLMResult(ok=True, data=request.fallback or {}, attempts=1)


def inspect_three_core(component: str) -> dict[str, Any]:
    """Inspect the actual installed public contracts without reading paths."""

    if component != "three-core":
        raise ValueError("component_must_be_three_core")
    modules = {
        "memcore": memcore,
        "capcore": capcore,
        "channelcore-onebot": channelcore_onebot,
    }
    packages: list[dict[str, Any]] = []
    all_present = True
    for distribution_name, names in _PUBLIC_CONTRACTS.items():
        module = modules[distribution_name]
        missing = [name for name in names if not hasattr(module, name)]
        all_present = all_present and not missing
        try:
            version = metadata.version(distribution_name)
        except metadata.PackageNotFoundError:
            version = "unavailable"
            all_present = False
        packages.append(
            {
                "distribution": distribution_name,
                "version": version,
                "checked_public_contracts": list(names),
                "missing_public_contracts": missing,
            }
        )
    return {
        "component": component,
        "all_present": all_present,
        "packages": packages,
        "integration_facts": [
            "channelcore-onebot is the ordered OneBot message and protocol authority",
            "MemCore is the append-only timeline and provider-context authority",
            "CapCore validates and executes provider-native capability calls",
            "the host owns credentials, model calls, namespace mapping, approval UX, and transport",
            "tool arguments and the exact model-visible result share one correlation id",
            "large closed-turn results remain reloadable from SQLite truth",
        ],
    }


def _openai_tool_name(tool: Mapping[str, Any]) -> str:
    function = tool.get("function") if isinstance(tool.get("function"), Mapping) else {}
    return str(function.get("name") or "").strip()


def _compose_stable_tools(capability_tools: tuple[dict[str, Any], ...]) -> tuple[dict[str, Any], ...]:
    memory_tools = tuple(
        build_native_memory_tool_specs(
            tool_format="openai",
            include_material_tool=False,
        )
    )
    tools = (*capability_tools, *memory_tools)
    names = [_openai_tool_name(tool) for tool in tools]
    if not all(names) or len(names) != len(set(names)):
        raise RuntimeError("three_core_tool_name_collision")
    return tools


def _stable_prefix_hash(tools: tuple[dict[str, Any], ...]) -> str:
    payload = json.dumps(
        {"system": STABLE_SYSTEM_PROMPT, "tools": tools},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _fake_tool_call(model_name: str) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_three_core_1",
                "type": "function",
                "function": {
                    "name": model_name,
                    "arguments": '{"component":"three-core"}',
                },
            }
        ],
    }


def _fake_final(tool_result_text: str) -> dict[str, str]:
    compact = tool_result_text.replace(" ", "").lower()
    if '"all_present":true' not in compact:
        raise RuntimeError("deterministic_model_missing_tool_evidence")
    return {
        "role": "assistant",
        "content": "三套核心的公开接口均可用，原生工具轨迹也已经进入同一条时间线。",
    }


async def run_demo_async() -> dict[str, Any]:
    inbound = normalize_inbound_event(
        {
            "post_type": "message",
            "message_type": "group",
            "self_id": "10001",
            "user_id": "20002",
            "group_id": "30003",
            "message_id": "onebot-demo-1",
            "time": 1_777_000_000,
            "sender": {"card": "伙伴", "role": "owner"},
            "message": [
                {"type": "at", "data": {"qq": "10001"}},
                {"type": "text", "data": {"text": " 检查三核接入状态"}},
            ],
        },
        bot_account_id="10001",
    )
    if not inbound.ok or inbound.message is None:
        raise RuntimeError(f"inbound_rejected:{inbound.reason}")
    message = inbound.message

    adapter = PythonCapabilityAdapter(
        provider_id="provider.python.three_core_demo",
        capabilities=[
            PythonCapabilitySpec.from_callable(
                inspect_three_core,
                capability_id="python.demo.inspect_three_core",
                display_name="Inspect Three-Core Runtime",
                short_hint="Inspect installed public MemCore, CapCore, and channelcore-onebot contracts.",
                visible_in=("qq",),
                prompt_exposed=True,
                risk="low",
                confirm="never",
                inputs=(CapabilityIOSlot(name="component", kind="string", required=True),),
            )
        ],
    )

    with TemporaryDirectory() as temp_dir:
        embedding = HashedEmbeddingProvider()
        store = SQLiteMemoryStore(str(Path(temp_dir) / "three-core.sqlite3"))
        memory = MemorySystem(
            llm=DemoMemoryLLM(),
            namespace=Namespace(
                tenant_id="demo",
                user_id=f"onebot:group:{message.conversation.id}",
                domain_id="three-core-reference",
                conversation_id=f"onebot:group:{message.conversation.id}",
            ),
            timezone="Asia/Shanghai",
            config=MemoryConfig(
                operation_projection_policy=OperationProjectionPolicy.COMPACT_AFTER_TERMINAL.value,
                raw_token_trigger=50_000,
            ),
            store=store,
            index=InMemoryVectorIndex(embedding=embedding),
            embedding=embedding,
        )
        handle = None
        completed = False
        try:
            actor = memcore.Actor(
                stable_id=message.actor.id,
                display_name=message.actor.display_name,
            )
            target_actor = memcore.Actor(stable_id=message.bot_account_id, display_name="Assistant")
            handle = memory.begin_turn(
                stimuli=[
                    TimelineEntryInput(
                        kind="message.user",
                        origin=EntryOrigin.USER,
                        turn_role=TurnRole.STIMULUS,
                        semantic_text=message.text,
                        payload={"text": message.text, "channel": message.public_summary()},
                        actor=actor,
                        target_actor=target_actor,
                        source_id=f"onebot:{message.event_id}",
                        timestamp=int(message.timestamp),
                    )
                ],
                turn_id="turn:onebot-demo-1",
            )
            current = handle.stimuli[0].to_record()

            capabilities = await adapter.list_capabilities()
            capability_tool_set = build_openai_chat_tool_set(capabilities, surface="qq")
            tools = _compose_stable_tools(capability_tool_set.tools)
            stable_hash = _stable_prefix_hash(tools)

            initial_projection = memory.build_context_projection(provider_profile=OPENAI_PROFILE)
            initial_request = (
                {"role": "system", "content": STABLE_SYSTEM_PROMPT},
                *initial_projection.payloads,
            )
            if not initial_request[-1].get("content"):
                raise RuntimeError("current_message_missing_from_provider_projection")

            assistant_tool_call = _fake_tool_call(capability_tool_set.to_model_name("python.demo.inspect_three_core"))
            invocations = parse_openai_chat_tool_calls(assistant_tool_call, capability_tool_set)
            if len(invocations) != 1:
                raise RuntimeError("capcore_tool_call_not_parsed")
            invocation = invocations[0]
            routes = await route_capabilities_from_adapter(adapter)
            invocation_context = InvocationContext(
                profile_user_id=memory.namespace.user_id,
                session_id=memory.namespace.conversation_id,
                client_mode="qq",
            )
            run_result = await run_openai_chat_tool_invocation(
                invocation,
                routes=routes,
                ctx=invocation_context,
                approval_policy=ApprovalPolicy(default_mode="ask_each_time"),
            )
            tool_message = build_openai_chat_tool_messages((run_result,))[0]
            if run_result.status != "ok":
                raise RuntimeError(f"capcore_tool_failed:{run_result.status}")

            schema_hash = capability_tool_set.tool_specs[0].schema_hash
            action = memory.append_action(
                turn_id=handle.turn_id,
                kind="tool.three_core_inspect.call",
                correlation_id=invocation.id,
                semantic_text="inspect_three_core requested",
                payload={"input": dict(invocation.arguments)},
                source_id="onebot-demo-1:tool-call",
                timestamp=int(message.timestamp) + 1,
                trace_metadata={
                    "tool_name": invocation.model_name,
                    "capability_id": invocation.capability_id,
                    "status": "running",
                },
                retention_anchor={
                    "capability_id": invocation.capability_id,
                    "schema_hash": schema_hash,
                },
            )
            observation = memory.append_observation(
                turn_id=handle.turn_id,
                kind="tool.three_core_inspect.result",
                correlation_id=invocation.id,
                semantic_text=str(tool_message["content"]),
                payload={"source": "capcore", "output": str(tool_message["content"])},
                source_id="onebot-demo-1:tool-result",
                timestamp=int(message.timestamp) + 2,
                status="success",
                trace_metadata={
                    "tool_name": invocation.model_name,
                    "capability_id": invocation.capability_id,
                    "status": "success",
                },
                retention_anchor={
                    "capability_id": invocation.capability_id,
                    "schema_hash": schema_hash,
                },
            )

            open_projection = memory.build_context_projection(provider_profile=OPENAI_PROFILE)
            projected_action, projected_result = open_projection.payloads[-2:]
            if projected_action != assistant_tool_call or projected_result != tool_message:
                raise RuntimeError("model_visible_tool_trajectory_changed")

            final_message = _fake_final(str(tool_message["content"]))
            completion = memory.complete_turn(
                turn_id=handle.turn_id,
                semantic_text=final_message["content"],
                provider_output_raw=json.dumps(final_message, ensure_ascii=False, separators=(",", ":")),
                memory_annotation={
                    "memory_facets": ["state"],
                    "about_roles": ["external"],
                    "entity_anchors": ["MemCore", "CapCore", "channelcore-onebot"],
                    "topic_terms": ["公开接口", "接入状态"],
                    "retrieval_priority": "normal",
                },
                annotation_status=AnnotationStatus.ACCEPTED_HOST,
                source_id="onebot-demo-1:assistant-final",
                timestamp=int(message.timestamp) + 3,
                provider_profile=OPENAI_PROFILE,
                provider_projection=final_message,
            )
            if not completion.completed:
                raise RuntimeError(f"turn_not_completed:{completion.status}")
            completed = True

            settled_projection = memory.build_context_projection(provider_profile=OPENAI_PROFILE)
            compact_cards = [
                str(payload.get("content") or "")
                for payload in settled_projection.payloads
                if "[compact_reloadable]" in str(payload.get("content") or "")
            ]
            if not compact_cards:
                raise RuntimeError("tool_result_not_settled")
            reload_result = dispatch_native_memory_tool(
                "open_memory",
                {
                    "memory_id": observation.source_id,
                    "view": "content",
                    "detail": "full",
                },
                mem=memory,
                current=current,
            )
            reload_payload = reload_result.get("result")
            reload_text = str(reload_payload.get("text") or "") if isinstance(reload_payload, Mapping) else ""
            exact_tool_result_reloaded = str(tool_message["content"]) in reload_text
            if not reload_result.get("ok") or not exact_tool_result_reloaded:
                raise RuntimeError("settled_tool_result_not_reloadable")

            target = OutboundTarget(message.conversation.kind, message.conversation.id)
            reply_to = ReplyReferenceLedger().claim(target, message.event_id, ["text"])
            outbound = build_message_action(
                target,
                [text_segment(final_message["content"])],
                reply_to=reply_to,
            )
            metrics = memory.settlement_metrics()
            return {
                "status": "ok",
                "imports_companion_v01": False,
                "demo_embedding_only": memory.embedding_status().get("degraded", False),
                "inbound": message.public_summary(),
                "stable_prefix_hash": stable_hash,
                "stable_prefix_repeat_hash": _stable_prefix_hash(tools),
                "tool_count": len(tools),
                "memory_tool_names": [
                    _openai_tool_name(tool)
                    for tool in tools
                    if _openai_tool_name(tool) in {"retrieve_for_turn", "browse_memory", "open_memory", "read_timeline"}
                ],
                "tool_call_id": invocation.id,
                "capability_id": invocation.capability_id,
                "action_source_id": action.source_id,
                "observation_source_id": observation.source_id,
                "open_turn_tool_wire_exact": True,
                "settlement_status": metrics[0]["settlement_status"] if metrics else "missing",
                "settled_has_compact_history": settled_projection.has_compact_history,
                "compact_card": compact_cards[0],
                "exact_tool_result_reloaded": exact_tool_result_reloaded,
                "final_speech": final_message["content"],
                "outbound_action": outbound.action,
                "outbound_params": outbound.params(),
            }
        finally:
            if handle is not None and not completed:
                memory.abort_turn(handle.turn_id, reason="three_core_demo_failed")
            await adapter.aclose()
            memory.close()
            store.close()


def run_demo() -> dict[str, Any]:
    return asyncio.run(run_demo_async())


def main() -> None:
    print(json.dumps(run_demo(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
