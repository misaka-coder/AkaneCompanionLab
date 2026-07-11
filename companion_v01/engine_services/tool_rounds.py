"""Tool round helpers extracted from engine.py.

Group A — pure helpers (no engine coupling).
Group B — module-level functions that take engine as first param.
"""

from __future__ import annotations

from typing import Any

from ..capability_registry import (
    CapabilityRegistry,
    CapabilitySelection,
    CapabilitySnapshot,
    is_document_attachment,
    is_document_generated_file,
    is_media_attachment,
    is_media_generated_file,
)
from ..client_protocol import ClientMode, ClientProtocolContext
from ..domain_profiles import (
    DEFAULT_DOMAIN_PROFILE_ID,
    FINANCE_DOMAIN_PROFILE_ID,
    DomainProfileRegistry,
    filter_tool_names,
)
from .. import tool_orchestration_engine
from ..local_capability_config import load_capability_config


# ── Group A: Pure helpers ────────────────────────────────────────


def max_tool_rounds(*, domain_profile_id: str = "") -> int:
    base_budget = tool_orchestration_engine.max_tool_rounds()
    profile = DomainProfileRegistry().get(domain_profile_id)
    if profile.id != FINANCE_DOMAIN_PROFILE_ID:
        return base_budget
    return min(
        int(profile.hard_tool_round_limit),
        max(base_budget, int(profile.default_tool_round_budget)),
    )


def tool_call_signature(tool_call: dict[str, Any]) -> str:
    return tool_orchestration_engine.tool_call_signature(tool_call)


def describe_tool_call_for_prompt(tool_call: dict[str, Any]) -> str:
    return tool_orchestration_engine.describe_tool_call_for_prompt(tool_call)


def build_tool_working_stream_event(tool_call: dict[str, Any]) -> dict[str, Any]:
    tool_type = str((tool_call or {}).get("type") or "unknown").strip() or "unknown"
    return {
        "type": "assistant_working",
        "status": "running",
        "phase": "tool_call",
        "tool_type": tool_type,
        "message": "我查一下。",
    }


def should_stop_after_tool_events(events: list[dict[str, Any]]) -> bool:
    blocking_statuses = {
        "unavailable",
        "permission_denied",
        "rate_limited",
        "error",
        "failed",
        "failure",
    }
    for event in events or []:
        if not isinstance(event, dict):
            continue
        status = str(event.get("status") or "").strip().lower()
        if status in blocking_statuses:
            return True
    return False


def should_stop_for_finance_no_progress(tool_results: list[Any]) -> bool:
    evidence: list[dict[str, Any]] = []
    for result in tool_results or []:
        state_updates = getattr(result, "state_updates", None)
        item = state_updates.get("finance_evidence") if isinstance(state_updates, dict) else None
        if isinstance(item, dict):
            evidence.append(item)
    if len(evidence) < 2:
        return False
    previous, current = evidence[-2:]
    previous_status = str(previous.get("status") or "").strip().lower()
    current_status = str(current.get("status") or "").strip().lower()
    if previous_status in {"empty", "unavailable", "permission_denied", "rate_limited"} and current_status in {
        "empty",
        "unavailable",
        "permission_denied",
        "rate_limited",
    }:
        return True
    previous_hash = str(previous.get("result_hash") or "").strip()
    current_hash = str(current.get("result_hash") or "").strip()
    return bool(previous_hash and previous_hash == current_hash)


def build_native_tool_round_instruction(native_tools: list[dict[str, Any]] | None) -> str:
    native_tool_names = sorted(
        {
            str(((tool.get("function") or {}).get("name") if isinstance(tool, dict) else "") or "").strip()
            for tool in native_tools or []
            if str(((tool.get("function") or {}).get("name") if isinstance(tool, dict) else "") or "").strip()
        }
    )
    name_text = "、".join(native_tool_names) if native_tool_names else "已提供的 native 工具"
    return (
        "【native 工具轮优先规则】\n"
        f"本轮已通过 provider native tools 提供：{name_text}。\n"
        f"若当前请求需要上述 native 工具，必须直接通过 provider tool_calls 调用；"
        "不要输出最终表现 JSON 正文，也不要在 JSON 的 tool_call 字段里手写这些 native 工具。\n"
        "只有仍在可用工具清单中、且没有通过 native schema 提供的 legacy 工具，才可以继续写入 JSON tool_call。\n"
        "如果不需要任何 legacy 工具，最终表现 JSON 的 tool_call 字段必须为 null。\n"
        "不要在 speech 里声称工具已调用、已完成或已失败；真实状态以系统工具结果为准。"
    )


# ── Group B: Engine-coupled helpers ──────────────────────────────


def resolve_tool_round_budget(
    engine: Any,
    *,
    current_budget: int,
    tool_call: dict[str, Any],
    client_context: ClientProtocolContext | None = None,
    profile_user_id: str = "",
    session_id: str = "",
    domain_profile_id: str = "",
) -> int:
    budget = tool_orchestration_engine.resolve_tool_round_budget(
        resolve_tool_handlers(
            engine,
            client_context=client_context,
            profile_user_id=profile_user_id,
            session_id=session_id,
            domain_profile_id=domain_profile_id,
        ),
        tool_call,
        current_budget=current_budget,
    )
    profile = DomainProfileRegistry().get(domain_profile_id)
    if profile.id == FINANCE_DOMAIN_PROFILE_ID:
        return min(int(profile.hard_tool_round_limit), max(int(profile.default_tool_round_budget), budget))
    return budget


def resolve_tool_handlers(
    engine: Any,
    *,
    client_context: ClientProtocolContext | None = None,
    profile_user_id: str = "",
    session_id: str = "",
    domain_profile_id: str = "",
) -> dict[str, Any]:
    handlers = getattr(engine, "tool_handlers", {}) or {}
    dynamic_handlers = build_adapter_tool_handlers(
        engine,
        profile_user_id=profile_user_id,
        client_context=client_context,
    )
    all_handlers = {**dict(handlers), **dynamic_handlers}
    domain_profile = DomainProfileRegistry().get(domain_profile_id)
    if domain_profile.id == DEFAULT_DOMAIN_PROFILE_ID:
        all_handlers = {
            name: handler
            for name, handler in all_handlers.items()
            if not _is_finance_only_handler(handler)
        }
    if client_context is None:
        allowed_names = filter_tool_names(tuple(all_handlers.keys()), domain_profile)
        return {name: all_handlers[name] for name in allowed_names if name in all_handlers}

    selected_names = list(
        resolve_capability_selection(
            engine,
            client_context=client_context,
            profile_user_id=profile_user_id,
            session_id=session_id,
            domain_profile_id=domain_profile_id,
        ).tool_names
    )
    return {
        tool_name: all_handlers[tool_name]
        for tool_name in selected_names
        if tool_name in all_handlers
    }


def resolve_capability_selection(
    engine: Any,
    *,
    client_context: ClientProtocolContext | None = None,
    profile_user_id: str = "",
    session_id: str = "",
    domain_profile_id: str = "",
) -> CapabilitySelection:
    from ..capability_registry import CapabilityRegistry

    handlers = getattr(engine, "tool_handlers", {}) or {}
    domain_profile = DomainProfileRegistry().get(domain_profile_id)
    if client_context is None:
        tool_names = filter_tool_names(tuple(handlers.keys()), domain_profile)
        return CapabilitySelection(
            light_hints=(),
            tool_names=tool_names,
            module_names=("all_tools",),
        )
    if not str(profile_user_id or "").strip() or not str(session_id or "").strip():
        return CapabilitySelection(
            light_hints=(),
            tool_names=tuple(
                legacy_mode_tool_names(
                    engine,
                    client_context,
                    domain_profile_id=domain_profile_id,
                )
            ),
            module_names=("legacy_mode_pack",),
        )
    snapshot = build_capability_snapshot(
        engine,
        client_context=client_context,
        profile_user_id=profile_user_id,
        session_id=session_id,
    )
    registry = getattr(engine, "capability_registry", None) or CapabilityRegistry()
    selection = registry.select(
        snapshot,
        allowed_tool_names=(
            domain_profile.allowed_tool_names
            if domain_profile.id != DEFAULT_DOMAIN_PROFILE_ID
            else None
        ),
        hidden_tool_names=domain_profile.hidden_tool_names,
    )
    if domain_profile.id != DEFAULT_DOMAIN_PROFILE_ID:
        domain_handler_names = tuple(
            name
            for name in filter_tool_names(tuple(handlers.keys()), domain_profile)
            if name in handlers and name not in selection.tool_names
        )
        selection = CapabilitySelection(
            light_hints=domain_profile.capability_hints,
            tool_names=(*selection.tool_names, *domain_handler_names),
            module_names=selection.module_names,
            layer_names=selection.layer_names,
        )
    dynamic_handlers = build_adapter_tool_handlers(
        engine,
        profile_user_id=profile_user_id,
        client_context=client_context,
    )
    if not dynamic_handlers:
        return selection
    dynamic_tool_names = tuple(
        name
        for name in filter_tool_names(tuple(dynamic_handlers.keys()), domain_profile)
        if name not in selection.tool_names
    )
    if not dynamic_tool_names:
        return selection
    return CapabilitySelection(
        light_hints=(
            *selection.light_hints,
            "当前 profile 有已显式暴露给 prompt 的本地 adapter 能力；调用失败时不要假装完成，涉及高风险动作会先请求确认。",
        ),
        tool_names=(*selection.tool_names, *dynamic_tool_names),
        module_names=(*selection.module_names, "adapter_tools"),
        layer_names=(*selection.layer_names, "adapter"),
    )


def _is_finance_only_handler(handler: Any) -> bool:
    tool_metadata = getattr(handler, "tool_metadata", None)
    if not callable(tool_metadata):
        return False
    try:
        metadata = tool_metadata()
    except Exception:
        return False
    return str(getattr(metadata, "family", "") or "").strip() in {"finance_read", "finance_artifact"}


def build_capability_snapshot(
    engine: Any,
    *,
    client_context: ClientProtocolContext,
    profile_user_id: str,
    session_id: str,
) -> CapabilitySnapshot:
    store = engine.store
    attachments = store.list_attachment_inbox_items(
        profile_user_id=profile_user_id,
        session_id=session_id,
        statuses=["ready", "pending_observation", "failed"],
        limit=80,
    )
    generated_files = store.list_generated_files(
        profile_user_id=profile_user_id,
        session_id=session_id,
        statuses=["ready", "failed"],
        limit=40,
    )
    return CapabilitySnapshot(
        client_mode=client_context.effective_mode,
        has_any_attachment=bool(attachments),
        has_document_attachment=any(is_document_attachment(item) for item in attachments),
        has_media_attachment=any(is_media_attachment(item) for item in attachments),
        has_generated_file=bool(generated_files),
        has_document_generated_file=any(is_document_generated_file(item) for item in generated_files),
        has_media_generated_file=any(is_media_generated_file(item) for item in generated_files),
        has_pending_gift=False,
    )


def legacy_mode_tool_names(
    engine: Any,
    client_context: ClientProtocolContext,
    *,
    domain_profile_id: str = "",
) -> list[str]:
    from ..capability_registry import CapabilityRegistry

    registry = getattr(engine, "capability_registry", None) or CapabilityRegistry()
    domain_profile = DomainProfileRegistry().get(domain_profile_id)
    return list(
        filter_tool_names(
            registry.tool_names_for_mode(client_context.effective_mode),
            domain_profile,
        )
    )


def build_adapter_tool_handlers(
    engine: Any,
    *,
    profile_user_id: str = "",
    client_context: ClientProtocolContext | None = None,
) -> dict[str, Any]:
    handlers: dict[str, Any] = {}
    handlers.update(
        build_mcp_adapter_tool_handlers(
            engine,
            profile_user_id=profile_user_id,
            client_context=client_context,
        )
    )
    handlers.update(
        build_python_adapter_tool_handlers(
            engine,
            profile_user_id=profile_user_id,
            client_context=client_context,
        )
    )
    return handlers


def build_mcp_adapter_tool_handlers(
    engine: Any,
    *,
    profile_user_id: str = "",
    client_context: ClientProtocolContext | None = None,
) -> dict[str, Any]:
    import config as _cfg
    from pathlib import Path

    from ..capability_adapters import McpStdioCapabilityAdapter
    from ..tool_runtime import AdapterCapabilityToolHandler

    if not str(profile_user_id or "").strip():
        return {}
    config_base_dir = Path(getattr(_cfg, "DATA_DIR", "users_data") or "users_data")
    try:
        config_payload = load_capability_config(
            base_dir=config_base_dir,
            profile_user_id=profile_user_id,
        )
    except Exception:
        return {}
    servers = config_payload.get("mcpServers") if isinstance(config_payload.get("mcpServers"), dict) else {}
    handlers: dict[str, Any] = {}
    for server_id, server_config in sorted(servers.items(), key=lambda item: str(item[0])):
        if not isinstance(server_config, dict) or not bool(server_config.get("enabled")):
            continue
        if not str(server_config.get("command") or "").strip():
            continue
        tools = [tool for tool in server_config.get("tools") or [] if isinstance(tool, dict)]
        prompt_tools = [tool for tool in tools if bool(tool.get("promptExposed") or tool.get("prompt_exposed"))]
        if not prompt_tools:
            continue
        adapter = McpStdioCapabilityAdapter(
            provider_id=f"provider.mcp.{server_id}",
            server_id=str(server_id),
            server_config={**server_config, "serverId": str(server_id)},
            tool_configs=tuple(prompt_tools),
        )
        for tool in prompt_tools:
            descriptor = adapter.descriptor_for_tool(tool)
            if descriptor.id and descriptor.prompt_exposed:
                handlers[descriptor.id] = AdapterCapabilityToolHandler(
                    capability_id=descriptor.id,
                    adapter=adapter,
                    descriptor=descriptor,
                    config_base_dir=config_base_dir,
                )
    return handlers


def build_python_adapter_tool_handlers(
    engine: Any,
    *,
    profile_user_id: str = "",
    client_context: ClientProtocolContext | None = None,
) -> dict[str, Any]:
    del engine, client_context
    import config as _cfg
    from pathlib import Path

    from ..capability_adapters import AkanePythonCapabilityAdapter
    from ..tool_runtime import AdapterCapabilityToolHandler

    if not str(profile_user_id or "").strip():
        return {}
    config_base_dir = Path(getattr(_cfg, "DATA_DIR", "users_data") or "users_data")
    adapter = AkanePythonCapabilityAdapter()
    handlers: dict[str, Any] = {}
    for descriptor in adapter.list_capabilities_sync():
        if descriptor.id and descriptor.prompt_exposed:
            handlers[descriptor.id] = AdapterCapabilityToolHandler(
                capability_id=descriptor.id,
                adapter=adapter,
                descriptor=descriptor,
                config_base_dir=config_base_dir,
            )
    return handlers
