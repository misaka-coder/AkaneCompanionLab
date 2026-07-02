from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
AKANE_PARENT = ROOT.parent


def _add_sibling_sources() -> None:
    for name in (
        "promptpack-core",
        "charpack-core",
        "memcore",
        "capcore",
        "capcore-adapter-python",
        "capcore-adapter-mcp",
        "capcore-provider-native-tools",
        "capcore-provider-openai",
        "capcore-provider-anthropic",
    ):
        path = AKANE_PARENT / name
        if path.exists() and str(path) not in sys.path:
            sys.path.insert(0, str(path))


_add_sibling_sources()

from capcore import ApprovalPolicy, CapabilityIOSlot, InvocationContext  # noqa: E402
from capcore_adapter_mcp import (  # noqa: E402
    McpStdioCapabilityAdapter,
    McpStdioServerConfig,
    McpToolOverride,
    McpToolRecord,
)
from capcore_adapter_python import PythonCapabilityAdapter, PythonCapabilitySpec  # noqa: E402
from capcore_provider_anthropic import (  # noqa: E402
    build_anthropic_messages_tool_result_message,
    build_anthropic_messages_tool_set,
    parse_anthropic_messages_tool_uses,
    route_capabilities_from_adapter as route_anthropic_capabilities_from_adapter,
    run_anthropic_messages_tool_invocation,
)
from capcore_provider_openai import (  # noqa: E402
    build_openai_chat_tool_messages,
    build_openai_chat_tool_set,
    parse_openai_chat_tool_calls,
    route_capabilities_from_adapter as route_openai_capabilities_from_adapter,
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
)
from promptpack_core import CacheRole, PromptAssembler, PromptPlacement  # noqa: E402


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _write_bytes(path: Path, content: bytes = b"stub") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _ts(year: int, month: int, day: int, hour: int, minute: int = 0) -> int:
    return int(datetime(year, month, day, hour, minute, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp())


class DemoMemoryLLM(LLMClient):
    def call(self, request: LLMRequest) -> LLMResult:
        return LLMResult(ok=True, data=request.fallback or {}, attempts=1)


class FakeMcpClient:
    async def list_tools(self, server: McpStdioServerConfig) -> tuple[McpToolRecord, ...]:
        return (
            McpToolRecord(
                name="echo",
                description="Echo one text argument from fake MCP.",
                input_schema={
                    "type": "object",
                    "properties": {"text": {"type": "string", "description": "Text to echo."}},
                    "required": ["text"],
                },
            ),
        )

    async def call_tool(
        self,
        server: McpStdioServerConfig,
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        return {
            "content": [{"type": "text", "text": f"mcp:{arguments.get('text')}"}],
            "isError": False,
        }


def python_echo(text: str) -> dict[str, str]:
    return {"echo": f"python:{text}"}


def _build_character_context(temp_root: Path) -> dict[str, Any]:
    characters_dir = temp_root / "characters"
    pack_dir = characters_dir / "mika"
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
            "dialogue": {
                "proactive_wake_prompt": "Keep replies warm and concise.",
            },
        },
    )
    (pack_dir / "persona.md").write_text("Mika is a careful engineering companion.", encoding="utf-8")
    _write_bytes(pack_dir / "assets" / "characters" / "default" / "normal.png")
    _write_bytes(pack_dir / "assets" / "characters" / "default" / "happy.png")

    service = CharacterPackResourceService(characters_dir=characters_dir)
    manifest = service.get_manifest("mika")
    assert manifest is not None
    manifest.refresh()
    persona = service.build_persona_prompt_context("mika", client_mode="desktop_pet", resource_manifest=manifest)
    resource_prompt = manifest.build_character_prompt_context()
    normalized_visual = manifest.normalize_visual_output({"emotion": "happy"})

    assert "Mika" in persona["system_context"]
    assert "happy" in resource_prompt
    assert normalized_visual["emotion"] == "happy"
    return {
        "persona": persona,
        "resource_prompt": resource_prompt,
        "normalized_visual": normalized_visual,
    }


def _build_memory_context(temp_root: Path) -> tuple[str, MemorySystem, SQLiteMemoryStore]:
    _ = temp_root
    embedding = HashedEmbeddingProvider()
    store = SQLiteMemoryStore(":memory:")
    index = InMemoryVectorIndex(embedding=embedding)
    mem = MemorySystem(
        llm=DemoMemoryLLM(),
        namespace=Namespace(user_id="ecosystem-user", conversation_id="ecosystem-smoke"),
        timezone="Asia/Shanghai",
        store=store,
        index=index,
        embedding=embedding,
        config=MemoryConfig(raw_trigger_count=4, summary_batch_size=2, enable_verifier=False),
    )
    mem.record_user_turn(
        "我喜欢稳定的 prompt 前缀和清晰的工具反馈。",
        timestamp=_ts(2026, 7, 2, 9, 0),
        memory_metadata={
            "keywords": ["prompt", "工具反馈"],
            "subject_scopes": ["user"],
            "categories": ["preference", "project_work"],
            "importance": 0.7,
            "confidence": 0.9,
        },
    )
    mem.record_assistant_turn("记住了，我会优先保持提示词和工具链路清楚。", timestamp=_ts(2026, 7, 2, 9, 1))
    current = mem.record_user_turn("帮我验证这些抽出来的包能不能一起工作。", timestamp=_ts(2026, 7, 2, 9, 2))
    context = mem.build_prompt_context(current=current)
    rendered = mem.render_prompt_context(context)
    assert rendered.strip()
    return rendered, mem, store


def _build_prompt_assembly(character_context: Mapping[str, Any], memory_context: str) -> Any:
    persona = character_context["persona"]
    assembler = PromptAssembler()
    assembler.add_section(
        "rules.product",
        "You are an AI product host smoke test. Keep output deterministic.",
        placement=PromptPlacement.SYSTEM,
        cache_role=CacheRole.STABLE,
    )
    assembler.add_section(
        "rules.tooling",
        "Use native tools only through capcore provider envelopes.",
        placement=PromptPlacement.SYSTEM,
        cache_role=CacheRole.STABLE,
    )
    assembler.add_section(
        "character.system",
        str(persona["system_context"]),
        placement=PromptPlacement.SYSTEM,
        cache_role=CacheRole.SEMI_STABLE,
    )
    assembler.add_section(
        "character.resources",
        str(character_context["resource_prompt"]),
        placement=PromptPlacement.SYSTEM,
        cache_role=CacheRole.SEMI_STABLE,
    )
    assembler.add_section(
        "memory.visible",
        memory_context,
        placement=PromptPlacement.USER,
        cache_role=CacheRole.DYNAMIC,
    )
    assembler.add_section(
        "user.current",
        "Please run the ecosystem smoke.",
        placement=PromptPlacement.USER,
        cache_role=CacheRole.DYNAMIC,
    )
    assembly = assembler.assemble()
    assert assembly.stable_prefix_hash
    assert len(assembly.cumulative_prefix_hashes) == len(assembly.sections)
    assert assembly.messages[0]["role"] == "system"
    assert assembly.messages[-1]["role"] == "user"
    return assembly


async def _build_adapters() -> tuple[PythonCapabilityAdapter, McpStdioCapabilityAdapter]:
    python_adapter = PythonCapabilityAdapter(
        provider_id="provider.python.ecosystem",
        capabilities=[
            PythonCapabilitySpec.from_callable(
                python_echo,
                capability_id="python.ecosystem.echo",
                display_name="Python Echo",
                short_hint="Echo text through a local Python callable.",
                visible_in=("desktop",),
                prompt_exposed=True,
                risk="low",
                confirm="never",
                inputs=(CapabilityIOSlot(name="text", kind="string", required=True),),
            )
        ],
    )
    mcp_adapter = McpStdioCapabilityAdapter(
        provider_id="provider.mcp.ecosystem",
        server=McpStdioServerConfig(server_id="demo", command="python", args=("-m", "demo_server")),
        tool_overrides={
            "echo": McpToolOverride(prompt_exposed=True, risk="low", confirm="never", visible_in=("desktop",))
        },
        client=FakeMcpClient(),
    )
    return python_adapter, mcp_adapter


async def _openai_provider_smoke(
    python_adapter: PythonCapabilityAdapter,
    mcp_adapter: McpStdioCapabilityAdapter,
) -> dict[str, Any]:
    capabilities = [*(await python_adapter.list_capabilities()), *(await mcp_adapter.list_capabilities())]
    tool_set = build_openai_chat_tool_set(capabilities, surface="desktop")
    assistant_message: Mapping[str, Any] = {
        "tool_calls": [
            {
                "id": "call_python_1",
                "type": "function",
                "function": {
                    "name": tool_set.to_model_name("python.ecosystem.echo"),
                    "arguments": '{"text":"hello"}',
                },
            },
            {
                "id": "call_mcp_1",
                "type": "function",
                "function": {
                    "name": tool_set.to_model_name("mcp.demo.echo"),
                    "arguments": '{"text":"hello"}',
                },
            },
        ]
    }
    invocations = parse_openai_chat_tool_calls(assistant_message, tool_set)
    assert len(invocations) == 2

    routes = {}
    routes.update(await route_openai_capabilities_from_adapter(python_adapter))
    routes.update(await route_openai_capabilities_from_adapter(mcp_adapter))
    ctx = InvocationContext(profile_user_id="ecosystem-user", session_id="s1", client_mode="desktop")
    results = [
        await run_openai_chat_tool_invocation(invocation, routes=routes, ctx=ctx, approval_policy=ApprovalPolicy())
        for invocation in invocations
    ]
    messages = build_openai_chat_tool_messages(results)
    assert len(messages) == 2
    assert all(message["role"] == "tool" for message in messages)
    assert "python:hello" in messages[0]["content"]
    assert "mcp:hello" in messages[1]["content"]
    return {
        "tool_count": len(tool_set.tools),
        "invocation_count": len(invocations),
        "message_count": len(messages),
    }


async def _anthropic_provider_smoke(
    python_adapter: PythonCapabilityAdapter,
    mcp_adapter: McpStdioCapabilityAdapter,
) -> dict[str, Any]:
    capabilities = [*(await python_adapter.list_capabilities()), *(await mcp_adapter.list_capabilities())]
    tool_set = build_anthropic_messages_tool_set(capabilities, surface="desktop")
    assistant_message = {
        "content": [
            {
                "type": "tool_use",
                "id": "toolu_python_1",
                "name": tool_set.to_model_name("python.ecosystem.echo"),
                "input": {"text": "hello"},
            },
            {
                "type": "tool_use",
                "id": "toolu_mcp_1",
                "name": tool_set.to_model_name("mcp.demo.echo"),
                "input": {"text": "hello"},
            },
        ]
    }
    invocations = parse_anthropic_messages_tool_uses(assistant_message, tool_set)
    assert len(invocations) == 2

    routes = {}
    routes.update(await route_anthropic_capabilities_from_adapter(python_adapter))
    routes.update(await route_anthropic_capabilities_from_adapter(mcp_adapter))
    ctx = InvocationContext(profile_user_id="ecosystem-user", session_id="s1", client_mode="desktop")
    results = [
        await run_anthropic_messages_tool_invocation(
            invocation,
            routes=routes,
            ctx=ctx,
            approval_policy=ApprovalPolicy(),
        )
        for invocation in invocations
    ]
    message = build_anthropic_messages_tool_result_message(results)
    assert message["role"] == "user"
    blocks = message["content"]
    assert len(blocks) == 2
    assert blocks[0]["type"] == "tool_result"
    assert "python:hello" in blocks[0]["content"]
    assert "mcp:hello" in blocks[1]["content"]
    return {
        "tool_count": len(tool_set.tools),
        "invocation_count": len(invocations),
        "tool_result_count": len(blocks),
    }


async def _run_smoke() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_root = Path(temp_dir)
        memory_system: MemorySystem | None = None
        memory_store: SQLiteMemoryStore | None = None
        try:
            character_context = _build_character_context(temp_root)
            memory_context, memory_system, memory_store = _build_memory_context(temp_root)
            assembly = _build_prompt_assembly(character_context, memory_context)
            python_adapter, mcp_adapter = await _build_adapters()
            openai = await _openai_provider_smoke(python_adapter, mcp_adapter)
            anthropic = await _anthropic_provider_smoke(python_adapter, mcp_adapter)
            return {
                "status": "ok",
                "prompt_sections": len(assembly.sections),
                "stable_prefix_hash": assembly.stable_prefix_hash[:12],
                "openai": openai,
                "anthropic": anthropic,
                "normalized_emotion": character_context["normalized_visual"]["emotion"],
            }
        finally:
            if memory_system is not None:
                memory_system.close()
            if memory_store is not None:
                memory_store.close()


def main() -> None:
    result = asyncio.run(_run_smoke())
    print("AKANE_EXTRACTED_ECOSYSTEM_SMOKE_OK")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
