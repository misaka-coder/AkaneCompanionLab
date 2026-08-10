"""Tool round helpers extracted from engine.py.

Group A — pure helpers (no engine coupling).
Group B — module-level functions that take engine as first param.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import replace
from types import MappingProxyType
from typing import Any, Mapping

from ..capability_registry import (
    CapabilityRegistry,
    CapabilitySelection,
    CapabilitySnapshot,
    ServerLocalOfferIndex,
    is_document_attachment,
    is_document_generated_file,
    is_image_attachment,
    is_image_generated_file,
    is_media_attachment,
    is_media_generated_file,
)
from ..client_protocol import ClientMode, ClientProtocolContext
from ..domain_profiles import (
    DEFAULT_DOMAIN_PROFILE_ID,
    DomainProfile,
    DomainProfileRegistry,
    filter_tool_names,
)
from .. import tool_orchestration_engine
from ..local_capability_config import (
    APPROVAL_MODE_ASK_EACH_TIME,
    APPROVAL_MODE_TRUSTED_AUTO_ALLOW,
    approval_mode_override_for_capability,
    load_capability_config,
)
# M66-E: ToolReadinessGate deleted; readiness is now gated via
# ServerLocalOfferIndex inside CapabilityRegistry.select().
import config as _host_config


logger = logging.getLogger("akane.tool_rounds")


# ── Group A: Pure helpers ────────────────────────────────────────


def max_tool_rounds(*, domain_profile_id: str = "") -> int:
    del domain_profile_id
    return tool_orchestration_engine.max_tool_rounds()


def max_tool_emergency_rounds(*, domain_profile_id: str = "", current_budget: int = 0) -> int:
    del domain_profile_id
    return tool_orchestration_engine.max_tool_emergency_rounds(current_budget=current_budget)


def extend_tool_round_budget_for_progress(
    *,
    current_budget: int,
    emergency_limit: int,
    tool_round_index: int,
    tool_calls: list[dict[str, Any]],
    seen_signatures: set[str],
) -> tuple[int, bool]:
    return tool_orchestration_engine.extend_tool_round_budget_for_progress(
        current_budget=current_budget,
        emergency_limit=emergency_limit,
        tool_round_index=tool_round_index,
        tool_calls=tool_calls,
        seen_signatures=seen_signatures,
    )


def tool_call_signature(tool_call: dict[str, Any]) -> str:
    return tool_orchestration_engine.tool_call_signature(tool_call)


def describe_tool_call_for_prompt(tool_call: dict[str, Any]) -> str:
    return tool_orchestration_engine.describe_tool_call_for_prompt(tool_call)


def build_tool_working_stream_event(tool_call: dict[str, Any]) -> dict[str, Any]:
    tool_type = str((tool_call or {}).get("type") or "unknown").strip() or "unknown"
    message = {
        "generate_image": "我开始生成图片，可能需要一会儿。",
        "load_material": "我重新看一下原图。",
    }.get(tool_type, "我查一下。")
    return {
        "type": "assistant_working",
        "status": "running",
        "phase": "tool_call",
        "tool_type": tool_type,
        "message": message,
    }


def should_stop_after_tool_events(
    events: list[dict[str, Any]],
    *,
    domain_profile_id: str = "",
) -> bool:
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
        reason = str(event.get("reason") or "").strip().lower()
        if (
            str(event.get("type") or "").strip() == "web_search_completed"
            and status in {"unavailable", "error", "failed", "failure"}
            and reason in {"mcp_tool_call_timeout", "mcp_call_failed", "timeout"}
        ):
            continue
        if status in blocking_statuses:
            return True
    return False


def build_native_tool_round_instruction(native_tools: list[dict[str, Any]] | None) -> str:
    del native_tools
    return (
        "【本轮直接工具入口】\n"
        "本轮实际附带的工具名称、参数和说明以请求中的工具定义为准；"
        "需要时直接发出真实工具调用，最终 JSON 的 tool_call 保持 null。"
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
    capability_selection = tool_call.get("_tool_capability_selection") if isinstance(tool_call, dict) else None
    budget = tool_orchestration_engine.resolve_tool_round_budget(
        resolve_tool_handlers(
            engine,
            client_context=client_context,
            profile_user_id=profile_user_id,
            session_id=session_id,
            domain_profile_id=domain_profile_id,
            capability_selection=capability_selection,
        ),
        tool_call,
        current_budget=current_budget,
    )
    return budget


def resolve_tool_handlers(
    engine: Any,
    *,
    client_context: ClientProtocolContext | None = None,
    profile_user_id: str = "",
    session_id: str = "",
    domain_profile_id: str = "",
    capability_selection: CapabilitySelection | None = None,
) -> dict[str, Any]:
    frozen_handlers = getattr(capability_selection, "resolved_handlers", None)
    if capability_selection is not None and isinstance(frozen_handlers, Mapping):
        return {
            name: frozen_handlers[name]
            for name in capability_selection.tool_names
            if name in frozen_handlers
        }
    if client_context is None:
        selection = resolve_capability_selection(
            engine,
            client_context=None,
            profile_user_id=profile_user_id,
            session_id=session_id,
            domain_profile_id=domain_profile_id,
        )
        return {
            name: selection.resolved_handlers[name]
            for name in selection.tool_names
            if name in selection.resolved_handlers
        }
    handlers = getattr(engine, "tool_handlers", {}) or {}
    dynamic_handlers = build_adapter_tool_handlers(
        engine,
        profile_user_id=profile_user_id,
        client_context=client_context,
    )
    all_handlers = {**dict(handlers), **dynamic_handlers}
    domain_profile = DomainProfileRegistry().get(domain_profile_id)
    selection = capability_selection or resolve_capability_selection(
        engine,
        client_context=client_context,
        profile_user_id=profile_user_id,
        session_id=session_id,
        domain_profile_id=domain_profile_id,
    )
    selected_names = list(selection.tool_names)
    selected_handlers = {
        tool_name: all_handlers[tool_name] for tool_name in selected_names if tool_name in all_handlers
    }

    # M66-E: ToolReadinessGate removed. Readiness is now gated by
    # ServerLocalOfferIndex inside CapabilityRegistry.select() before this
    # function is called. selected_handlers are already offer-filtered.
    return selected_handlers


def resolve_capability_selection(
    engine: Any,
    *,
    client_context: ClientProtocolContext | None = None,
    profile_user_id: str = "",
    session_id: str = "",
    domain_profile_id: str = "",
    intent_text: str = "",
) -> CapabilitySelection:
    from ..capability_registry import CapabilityRegistry

    handlers = getattr(engine, "tool_handlers", {}) or {}
    domain_profile = DomainProfileRegistry().get(domain_profile_id)
    if client_context is None:
        registry = getattr(engine, "capability_registry", None) or CapabilityRegistry()
        server_offer_index = getattr(registry, "server_offer_index", None) or ServerLocalOfferIndex()
        registry.server_offer_index = server_offer_index
        try:
            setattr(engine, "capability_registry", registry)
        except Exception:
            pass
        server_offer_index.replace_handlers(handlers)
        # No-client-context resolution is a compatibility/admin path rather
        # than a model-facing turn.  Keep the host's in-process handlers here
        # so a missing/failed optional plugin cannot remove ordinary Akane
        # tools.  Frontstage and worker selections carry an explicit context
        # and continue to use the strict ServerLocalOfferIndex below.
        static_handlers = dict(handlers)
        dynamic_handlers = _filter_live_dynamic_handlers(
            build_adapter_tool_handlers(
                engine,
                profile_user_id=profile_user_id,
                client_context=None,
            )
        )
        all_handlers = {**dict(static_handlers), **dynamic_handlers}
        tool_names = _filter_tool_names_with_policy_extensions(
            tuple(all_handlers.keys()),
            domain_profile,
            handlers=all_handlers,
        )
        return _freeze_capability_selection(
            CapabilitySelection(
                light_hints=(),
                tool_names=tool_names,
                module_names=("all_tools",),
            ),
            all_handlers,
        )
    if not str(profile_user_id or "").strip() or not str(session_id or "").strip():
        registry = getattr(engine, "capability_registry", None) or CapabilityRegistry()
        server_offer_index = getattr(registry, "server_offer_index", None) or ServerLocalOfferIndex()
        registry.server_offer_index = server_offer_index
        try:
            setattr(engine, "capability_registry", registry)
        except Exception:
            pass
        server_offer_index.replace_handlers(handlers)
        client_mode_value = str(
            getattr(client_context.effective_mode, "value", client_context.effective_mode) or ""
        )
        selection = CapabilitySelection(
            light_hints=(),
            tool_names=tuple(
                name
                for name in legacy_mode_tool_names(
                    engine, client_context, domain_profile_id=domain_profile_id
                )
                if server_offer_index.is_offered(
                    name,
                    profile_user_id=profile_user_id,
                    session_id=session_id,
                    client_mode=client_mode_value,
                )
            ),
            module_names=("legacy_mode_pack",),
            schema_tool_names=tuple(
                name
                for name in legacy_mode_tool_names(
                    engine, client_context, domain_profile_id=domain_profile_id
                )
                if name in handlers
            ),
        )
        return _freeze_capability_selection(selection, handlers)
    snapshot = build_capability_snapshot(
        engine,
        client_context=client_context,
        profile_user_id=profile_user_id,
        session_id=session_id,
    )
    registry = getattr(engine, "capability_registry", None) or CapabilityRegistry()
    server_offer_index = getattr(registry, "server_offer_index", None)
    if server_offer_index is None:
        server_offer_index = ServerLocalOfferIndex()
        registry.server_offer_index = server_offer_index
        try:
            setattr(engine, "capability_registry", registry)
        except Exception:
            pass
    replace_handlers = getattr(server_offer_index, "replace_handlers", None)
    if callable(replace_handlers):
        replace_handlers(handlers)
    selection = registry.select(
        snapshot,
        allowed_tool_names=(
            domain_profile.allowed_tool_names
            if domain_profile.id != DEFAULT_DOMAIN_PROFILE_ID
            else tuple(handlers.keys())
        ),
        hidden_tool_names=(
            *domain_profile.hidden_tool_names,
            *(() if "generate_image" in handlers else ("generate_image",)),
        ),
        intent_text=intent_text,
        profile_user_id=profile_user_id,
        session_id=session_id,
    )
    if domain_profile.id != DEFAULT_DOMAIN_PROFILE_ID:
        client_mode_value = str(
            getattr(client_context.effective_mode, "value", client_context.effective_mode) or ""
        )

        def domain_handler_is_offered(name: str) -> bool:
            if server_offer_index is None:
                return True
            return server_offer_index.is_offered(
                name,
                profile_user_id=profile_user_id,
                session_id=session_id,
                client_mode=client_mode_value,
            )

        domain_handler_names = tuple(
            name
            for name in filter_tool_names(tuple(handlers.keys()), domain_profile)
            if name in handlers
            and name not in selection.tool_names
            and domain_handler_is_offered(name)
        )
        selection = CapabilitySelection(
            light_hints=domain_profile.capability_hints,
            tool_names=(*selection.tool_names, *domain_handler_names),
            module_names=selection.module_names,
            schema_tool_names=(*selection.schema_tool_names, *domain_handler_names),
            layer_names=selection.layer_names,
            disclosures=selection.disclosures,
            tool_specs=selection.tool_specs,
            execution_receipts=selection.execution_receipts,
        )
    dynamic_handlers = build_adapter_tool_handlers(
        engine,
        profile_user_id=profile_user_id,
        client_context=client_context,
    )
    # Dynamic providers must publish an explicit, ready status. Missing or
    # malformed liveness data fails closed and never enters the model schema.
    dynamic_handlers = _filter_live_dynamic_handlers(dynamic_handlers)
    dynamic_tool_names = tuple(
        name
        for name in _filter_tool_names_with_policy_extensions(
            tuple(dynamic_handlers.keys()),
            domain_profile,
            handlers=dynamic_handlers,
        )
        if name not in selection.tool_names
    )
    if dynamic_tool_names:
        selection = CapabilitySelection(
            light_hints=(
                *selection.light_hints,
                "当前 profile 有已显式暴露给 prompt 的扩展能力；调用失败时不要假装完成，涉及高风险动作会先请求确认。",
            ),
            tool_names=(*selection.tool_names, *dynamic_tool_names),
            module_names=(*selection.module_names, "extension_tools"),
            schema_tool_names=(*selection.schema_tool_names, *dynamic_tool_names),
            layer_names=(*selection.layer_names, "extension"),
            disclosures=selection.disclosures,
            tool_specs=selection.tool_specs,
            execution_receipts=selection.execution_receipts,
        )
    return _freeze_capability_selection(selection, {**dict(handlers), **dynamic_handlers})


def _filter_live_dynamic_handlers(dynamic_handlers: Mapping[str, Any]) -> dict[str, Any]:
    live_dynamic_handlers: dict[str, Any] = {}
    for _name, _handler in dynamic_handlers.items():
        _status_fn = getattr(_handler, "capability_status", None)
        if not callable(_status_fn):
            continue
        try:
            _status = _status_fn()
        except Exception:
            continue  # probe threw → treat as unavailable
        if isinstance(_status, bool):
            if _status:
                live_dynamic_handlers[_name] = _handler
        elif isinstance(_status, Mapping):
            _state = str(_status.get("status") or "").strip().lower()
            if bool(_status.get("enabled", False)) and _state in {"available", "degraded", "ok", "ready"}:
                live_dynamic_handlers[_name] = _handler
    return live_dynamic_handlers


def _freeze_capability_selection(
    selection: CapabilitySelection,
    handlers: dict[str, Any] | Mapping[str, Any],
) -> CapabilitySelection:
    schema_tool_names = selection.schema_tool_names or selection.tool_names
    resolved_names = dict.fromkeys((*selection.tool_names, *schema_tool_names))
    resolved = {
        name: handlers[name]
        for name in resolved_names
        if name in handlers
    }
    return replace(
        selection,
        schema_tool_names=tuple(schema_tool_names),
        resolved_handlers=MappingProxyType(resolved),
    )


def _filter_tool_names_with_policy_extensions(
    tool_names: tuple[str, ...] | list[str],
    profile: DomainProfile | None,
    *,
    handlers: dict[str, Any],
) -> tuple[str, ...]:
    normally_allowed = set(filter_tool_names(tool_names, profile))
    return tuple(
        name
        for name in tool_names
        if name in normally_allowed
        or bool(getattr(handlers.get(name), "policy_accepted_plugin_capability", False))
    )


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
    workspace_inventory: dict[str, Any] = {}
    if client_context.effective_mode == ClientMode.DESKTOP_PET:
        workspace_service = getattr(engine, "workspace_file_service", None)
        inventory_fn = getattr(workspace_service, "capability_inventory", None)
        if callable(inventory_fn):
            try:
                workspace_inventory = dict(inventory_fn() or {})
            except Exception:
                workspace_inventory = {}
    has_cover_song_cache = False
    cover_song_handler = (getattr(engine, "tool_handlers", {}) or {}).get("cover_song")
    cover_song_service = getattr(cover_song_handler, "cover_song_service", None)
    cache_status_fn = getattr(cover_song_service, "has_cached_cover", None)
    if callable(cache_status_fn):
        try:
            has_cover_song_cache = bool(cache_status_fn(profile_user_id=profile_user_id))
        except Exception:
            has_cover_song_cache = False
    execution_provider_present = bool(getattr(engine, "execution_provider", None))
    execution_approval_override = ""
    if client_context.effective_mode == ClientMode.QQ_TEXT:
        execution_profile_config = load_capability_config(
            base_dir=getattr(engine, "capability_config_base_dir", None),
            profile_user_id=profile_user_id,
        )
        execution_approval_override = approval_mode_override_for_capability(
            execution_profile_config.get("approvalPolicy"),
            "exec_run",
        )
    execution_qq_enabled = (
        execution_provider_present
        and bool(getattr(_host_config, "EXECUTION_QQ_ENABLED", False))
        and client_context.effective_mode == ClientMode.QQ_TEXT
        and execution_approval_override
        in {APPROVAL_MODE_ASK_EACH_TIME, APPROVAL_MODE_TRUSTED_AUTO_ALLOW}
    )
    return CapabilitySnapshot(
        client_mode=client_context.effective_mode,
        has_any_attachment=bool(attachments),
        has_document_attachment=any(is_document_attachment(item) for item in attachments),
        has_media_attachment=any(is_media_attachment(item) for item in attachments),
        has_image_attachment=any(is_image_attachment(item) for item in attachments),
        has_generated_file=bool(generated_files),
        has_document_generated_file=any(is_document_generated_file(item) for item in generated_files),
        has_media_generated_file=any(is_media_generated_file(item) for item in generated_files),
        has_image_generated_file=any(is_image_generated_file(item) for item in generated_files),
        has_workspace_file=bool(workspace_inventory.get("has_any_file")),
        has_document_workspace_file=bool(workspace_inventory.get("has_document_file")),
        has_media_workspace_file=bool(workspace_inventory.get("has_media_file")),
        has_image_workspace_file=bool(workspace_inventory.get("has_image_file")),
        has_cover_song_cache=has_cover_song_cache,
        has_pending_gift=False,
        execution_enabled=execution_provider_present,
        execution_qq_enabled=execution_qq_enabled,
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
    handlers.update(build_plugin_capability_tool_handlers(engine, client_context=client_context))
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


def build_plugin_capability_tool_handlers(
    engine: Any,
    *,
    client_context: ClientProtocolContext | None = None,
) -> dict[str, Any]:
    source = getattr(engine, "plugin_capability_source", None)
    builder = getattr(source, "build_tool_handlers", None)
    if not callable(builder):
        return {}
    try:
        raw_handlers = builder(client_context=client_context)
    except Exception as exc:
        logger.warning("plugin capability bridge unavailable: reason=%s", type(exc).__name__)
        return {}
    if not isinstance(raw_handlers, dict):
        try:
            raw_handlers = dict(raw_handlers or {})
        except Exception:
            logger.warning("plugin capability bridge returned invalid handler mapping")
            return {}
    handlers: dict[str, Any] = {}
    for raw_name, handler in raw_handlers.items():
        name = str(raw_name or "").strip()
        if not name or handler is None or str(getattr(handler, "tool_type", "") or "").strip() != name:
            continue
        handlers[name] = handler
    return handlers


def build_mcp_adapter_tool_handlers(
    engine: Any,
    *,
    profile_user_id: str = "",
    client_context: ClientProtocolContext | None = None,
) -> dict[str, Any]:
    from ..capability_adapters import McpStdioCapabilityAdapter
    from ..tool_runtime import AdapterCapabilityToolHandler

    if not str(profile_user_id or "").strip():
        return {}
    config_base_dir = getattr(engine, "capability_config_base_dir", None)
    if config_base_dir is None:
        return {}
    try:
        config_payload = load_capability_config(
            base_dir=config_base_dir,
            profile_user_id=profile_user_id,
        )
    except Exception:
        return {}
    servers = config_payload.get("mcpServers") if isinstance(config_payload.get("mcpServers"), dict) else {}
    raw_cache = getattr(engine, "_mcp_capability_adapter_cache", None)
    adapter_cache: dict[tuple[str, str, str], Any] = raw_cache if isinstance(raw_cache, dict) else {}
    handlers: dict[str, Any] = {}
    for server_id, server_config in sorted(servers.items(), key=lambda item: str(item[0])):
        if not isinstance(server_config, dict) or not bool(server_config.get("enabled")):
            continue
        transport = str(server_config.get("transport") or "stdio").strip().lower().replace("-", "_")
        configured = (
            bool(str(server_config.get("url") or "").strip())
            if transport in {"http", "streamablehttp", "streamable_http"}
            else bool(str(server_config.get("command") or "").strip())
        )
        if not configured:
            continue
        tools = [tool for tool in server_config.get("tools") or [] if isinstance(tool, dict)]
        prompt_tools = [tool for tool in tools if bool(tool.get("promptExposed") or tool.get("prompt_exposed"))]
        if not prompt_tools:
            continue
        adapter_config = {**server_config, "serverId": str(server_id)}
        fingerprint = hashlib.sha256(
            json.dumps(adapter_config, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        cache_key = (str(profile_user_id), str(server_id), fingerprint)
        adapter = adapter_cache.get(cache_key)
        if adapter is None:
            adapter = McpStdioCapabilityAdapter(
                provider_id=f"provider.mcp.{server_id}",
                server_id=str(server_id),
                server_config=adapter_config,
                tool_configs=tuple(prompt_tools),
                liveness_probe=getattr(engine, "mcp_liveness_probe", None),
            )
            if len(adapter_cache) >= 64:
                adapter_cache.clear()
            adapter_cache[cache_key] = adapter
        for tool in prompt_tools:
            descriptor = adapter.descriptor_for_tool(tool)
            if descriptor.id and descriptor.prompt_exposed:
                handlers[descriptor.id] = AdapterCapabilityToolHandler(
                    capability_id=descriptor.id,
                    adapter=adapter,
                    descriptor=descriptor,
                    config_base_dir=config_base_dir,
                )
    try:
        setattr(engine, "_mcp_capability_adapter_cache", adapter_cache)
    except Exception:
        pass
    return handlers


def build_python_adapter_tool_handlers(
    engine: Any,
    *,
    profile_user_id: str = "",
    client_context: ClientProtocolContext | None = None,
) -> dict[str, Any]:
    del client_context

    from ..capability_adapters import AkanePythonCapabilityAdapter
    from ..tool_runtime import AdapterCapabilityToolHandler

    if not str(profile_user_id or "").strip():
        return {}
    config_base_dir = getattr(engine, "capability_config_base_dir", None)
    if config_base_dir is None:
        return {}
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
