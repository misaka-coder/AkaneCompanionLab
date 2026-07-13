from __future__ import annotations

import asyncio
import json
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo


from capcore import ApprovalPolicy, CapabilityIOSlot, InvocationContext  # noqa: E402
from capcore_adapter_python import PythonCapabilityAdapter, PythonCapabilitySpec  # noqa: E402
from capcore_provider_openai import (  # noqa: E402
    build_openai_chat_tool_messages,
    build_openai_chat_tool_set,
    parse_openai_chat_tool_calls,
    route_capabilities_from_adapter,
    run_openai_chat_tool_invocation,
)
from charpack_core import CharacterPackResourceService  # noqa: E402
from memcore import (  # noqa: E402
    HashedEmbeddingProvider,
    InMemoryVectorIndex,
    LLMClient,
    LLMRequest,
    LLMResult,
    MemoryConfig,
    MemorySystem,
    Namespace,
    SQLiteMemoryStore,
    build_chat_output_contract_prompt,
    parse_chat_output,
)
from promptpack_core import CacheRole, PromptAssembler, PromptPlacement  # noqa: E402


def _ts(year: int, month: int, day: int, hour: int, minute: int = 0) -> int:
    return int(datetime(year, month, day, hour, minute, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp())


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _write_bytes(path: Path, content: bytes = b"stub") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


class DemoMemoryLLM(LLMClient):
    def call(self, request: LLMRequest) -> LLMResult:
        return LLMResult(ok=True, data=request.fallback or {}, attempts=1)


def _create_character_pack(root: Path) -> CharacterPackResourceService:
    pack_dir = root / "characters" / "mika"
    _write_json(
        pack_dir / "character.json",
        {
            "identity": {
                "id": "mika",
                "name": "Mika",
                "app_name": "Mika Lab",
                "user_title": "Partner",
            },
            "appearance": {
                "default_outfit": "default",
                "default_emotion": "normal",
            },
        },
    )
    (pack_dir / "persona.md").write_text("Mika is a precise but warm AI product companion.", encoding="utf-8")
    _write_bytes(pack_dir / "assets" / "characters" / "default" / "normal.png")
    _write_bytes(pack_dir / "assets" / "characters" / "default" / "happy.png")
    return CharacterPackResourceService(characters_dir=root / "characters")


def inspect_project(topic: str) -> dict[str, Any]:
    return {
        "topic": topic,
        "summary": "The package ecosystem has memory, prompt assembly, character resources, and native tools.",
        "next_step": "Keep the host turn loop explicit before extracting a shared agent loop.",
    }


def _build_prompt(
    *,
    persona: Mapping[str, Any],
    resource_prompt: str,
    visible_memory: str,
    output_contract: str,
    user_text: str,
    tool_result_text: str = "",
) -> Any:
    assembler = PromptAssembler()
    assembler.add_section(
        "rules.product",
        "You are a host-loop smoke model. Return final output as memcore JSON.",
        placement=PromptPlacement.SYSTEM,
        cache_role=CacheRole.STABLE,
    )
    assembler.add_section(
        "rules.output_contract",
        output_contract,
        placement=PromptPlacement.SYSTEM,
        cache_role=CacheRole.STABLE,
    )
    assembler.add_section(
        "character.persona",
        str(persona["system_context"]),
        placement=PromptPlacement.SYSTEM,
        cache_role=CacheRole.SEMI_STABLE,
    )
    assembler.add_section(
        "character.resources",
        resource_prompt,
        placement=PromptPlacement.SYSTEM,
        cache_role=CacheRole.SEMI_STABLE,
    )
    assembler.add_section(
        "memory.visible",
        visible_memory,
        placement=PromptPlacement.USER,
        cache_role=CacheRole.DYNAMIC,
    )
    if tool_result_text:
        assembler.add_section(
            "tools.results",
            tool_result_text,
            placement=PromptPlacement.USER,
            cache_role=CacheRole.DYNAMIC,
        )
    assembler.add_section(
        "user.current",
        user_text,
        placement=PromptPlacement.USER,
        cache_role=CacheRole.DYNAMIC,
    )
    return assembler.assemble()


async def _run_smoke() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_root = Path(temp_dir)
        service = _create_character_pack(temp_root)
        manifest = service.get_manifest("mika")
        assert manifest is not None
        manifest.refresh()
        persona = service.build_persona_prompt_context("mika", client_mode="desktop_pet", resource_manifest=manifest)
        resource_prompt = manifest.build_character_prompt_context()

        embedding = HashedEmbeddingProvider()
        store = SQLiteMemoryStore(":memory:")
        index = InMemoryVectorIndex(embedding=embedding)
        mem = MemorySystem(
            llm=DemoMemoryLLM(),
            namespace=Namespace(user_id="blueprint-user", conversation_id="blueprint-turn"),
            timezone="Asia/Shanghai",
            store=store,
            index=index,
            embedding=embedding,
            config=MemoryConfig(raw_trigger_count=4, summary_batch_size=2, enable_verifier=False),
        )

        adapter = PythonCapabilityAdapter(
            provider_id="provider.python.blueprint",
            capabilities=[
                PythonCapabilitySpec.from_callable(
                    inspect_project,
                    capability_id="python.blueprint.inspect_project",
                    display_name="Inspect Project",
                    short_hint="Summarize the reusable package ecosystem.",
                    visible_in=("desktop",),
                    prompt_exposed=True,
                    risk="low",
                    confirm="never",
                    inputs=(CapabilityIOSlot(name="topic", kind="string", required=True),),
                )
            ],
        )

        try:
            user_text = "这些包现在适合继续支撑大型 AI 产品吗？"
            cur = mem.record_user_turn(user_text, timestamp=_ts(2026, 7, 2, 13, 0))
            visible_memory = mem.render_prompt_context(mem.build_prompt_context(current=cur))
            output_contract = build_chat_output_contract_prompt(
                categories=mem.config.categories,
                enable_flavor=mem.config.enable_flavor,
                enable_sentence_segments=True,
            )
            first_prompt = _build_prompt(
                persona=persona,
                resource_prompt=resource_prompt,
                visible_memory=visible_memory,
                output_contract=output_contract,
                user_text=user_text,
            )
            assert first_prompt.stable_prefix_hash

            capabilities = await adapter.list_capabilities()
            tool_set = build_openai_chat_tool_set(capabilities, surface="desktop")
            assistant_tool_message: Mapping[str, Any] = {
                "tool_calls": [
                    {
                        "id": "call_blueprint_1",
                        "type": "function",
                        "function": {
                            "name": tool_set.to_model_name("python.blueprint.inspect_project"),
                            "arguments": json.dumps({"topic": "large-ai-product"}, ensure_ascii=False),
                        },
                    }
                ]
            }
            invocations = parse_openai_chat_tool_calls(assistant_tool_message, tool_set)
            routes = await route_capabilities_from_adapter(adapter)
            ctx = InvocationContext(profile_user_id="blueprint-user", session_id="s1", client_mode="desktop")
            tool_results = [
                await run_openai_chat_tool_invocation(
                    invocation,
                    routes=routes,
                    ctx=ctx,
                    approval_policy=ApprovalPolicy(),
                )
                for invocation in invocations
            ]
            tool_messages = build_openai_chat_tool_messages(tool_results)
            assert len(tool_messages) == 1

            second_prompt = _build_prompt(
                persona=persona,
                resource_prompt=resource_prompt,
                visible_memory=visible_memory,
                output_contract=output_contract,
                user_text=user_text,
                tool_result_text=tool_messages[0]["content"],
            )
            assert first_prompt.stable_prefix_hash == second_prompt.stable_prefix_hash

            final_model_output = json.dumps(
                {
                    "speech": (
                        "可以。核心底座已经齐了，下一步应优先稳定 host turn loop，再观察是否值得抽 agentloop-core。"
                    ),
                    "memory_metadata": {
                        "keywords": ["AI产品", "host loop", "agentloop-core", "包生态"],
                        "subject_scopes": ["user"],
                        "categories": ["project_work", "plan_goal"],
                        "mood_tags": [],
                        "importance": 0.7,
                        "confidence": 0.9,
                    },
                    "visual": {"emotion": "happy"},
                },
                ensure_ascii=False,
            )
            parsed = parse_chat_output(
                final_model_output,
                mode="memcore_json",
                categories=mem.config.categories,
                enable_flavor=mem.config.enable_flavor,
            )
            assert parsed.ok, parsed.reason
            metadata_update = mem.update_turn_metadata(cur["source_id"], parsed.memory_metadata)
            assert metadata_update["ok"], metadata_update
            mem.record_assistant_turn(parsed.speech, in_reply_to=cur, timestamp=_ts(2026, 7, 2, 13, 1))
            compact_future = mem.compact_due_background()
            compact_stats = compact_future.result(timeout=10)
            normalized_visual = manifest.normalize_visual_output({"emotion": "happy"})

            return {
                "status": "ok",
                "prompt_sections_before_tools": len(first_prompt.sections),
                "prompt_sections_after_tools": len(second_prompt.sections),
                "stable_prefix_hash": first_prompt.stable_prefix_hash[:12],
                "tool_invocations": len(invocations),
                "tool_messages": len(tool_messages),
                "assistant_speech": parsed.speech,
                "metadata_keywords": parsed.memory_metadata.get("keywords", []),
                "normalized_emotion": normalized_visual["emotion"],
                "compaction": compact_stats,
            }
        finally:
            mem.close()
            store.close()


def main() -> None:
    result = asyncio.run(_run_smoke())
    print("AKANE_AI_PRODUCT_HOST_TURN_SMOKE_OK")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
