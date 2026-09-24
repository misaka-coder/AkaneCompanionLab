"""Tool round helpers extracted from engine.py.

Group A — pure helpers (no engine coupling).
Group B — module-level functions that take engine as first param.
"""

from __future__ import annotations

import hashlib
import json
import logging
from concurrent.futures import ThreadPoolExecutor
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
    APPROVAL_MODE_DISABLED,
    APPROVAL_MODE_ASK_EACH_TIME,
    APPROVAL_MODE_TRUSTED_AUTO_ALLOW,
    approval_mode_for_capability,
    approval_mode_override_for_capability,
    get_effective_mcp_server_configs,
    load_capability_config,
)
from ..native_tool_schema import NATIVE_TOOL_CAPABILITY_ID_FIELD, build_openai_native_tool_specs
from ..capability_discovery import build_capability_discovery_handlers
# M66-E: ToolReadinessGate deleted; readiness is now gated via
# ServerLocalOfferIndex inside CapabilityRegistry.select().
import config as _host_config


logger = logging.getLogger("akane.tool_rounds")


# ── Group A: Pure helpers ────────────────────────────────────────


def max_tool_rounds(*, domain_profile_id: str = "") -> int:
    del domain_profile_id
    return tool_orchestration_engine.max_tool_rounds()


def tool_round_warning_remaining(*, hard_limit: int) -> int:
    return tool_orchestration_engine.tool_round_warning_remaining(hard_limit=hard_limit)


def build_tool_round_warning(
    *,
    used_rounds: int,
    hard_limit: int,
    memcore_enabled: bool,
) -> str:
    return tool_orchestration_engine.build_tool_round_warning(
        used_rounds=used_rounds,
        hard_limit=hard_limit,
        memcore_enabled=memcore_enabled,
    )


def tool_call_signature(tool_call: dict[str, Any]) -> str:
    return tool_orchestration_engine.tool_call_signature(tool_call)


def describe_tool_call_for_prompt(tool_call: dict[str, Any]) -> str:
    return tool_orchestration_engine.describe_tool_call_for_prompt(tool_call)


def build_tool_working_stream_event(tool_call: dict[str, Any]) -> dict[str, Any]:
    tool_type = str((tool_call or {}).get("type") or "unknown").strip() or "unknown"
    message = {
        "load_material": "我重新看一下原图。",
    }.get(tool_type, "我查一下。")
    return {
        "type": "assistant_working",
        "status": "running",
        "phase": "tool_call",
        "tool_type": tool_type,
        "message": message,
    }


def build_native_tool_round_instruction(native_tools: list[dict[str, Any]] | None) -> str:
    del native_tools
    return (
        "【本轮直接工具入口】\n"
        "本轮实际附带的工具名称、参数和说明以请求中的工具定义为准；"
        "需要时直接发出真实工具调用；最终回复只遵循当前客户端的输出协议。"
    )


# ── Group B: Engine-coupled helpers ──────────────────────────────


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
    authorization_profile_user_id: str = "",
    character_pack_id: str = "",
    _raw_selection: bool = False,
    refresh_mcp_tools: bool = False,
    mcp_server_ids: frozenset[str] | None = None,
) -> CapabilitySelection:
    from ..capability_registry import CapabilityRegistry

    handlers = getattr(engine, "tool_handlers", {}) or {}
    domain_profile = DomainProfileRegistry().get(domain_profile_id)
    domain_profile_id = domain_profile.id
    authorization_profile_user_id = str(authorization_profile_user_id or profile_user_id)
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
                refresh_mcp_tools=refresh_mcp_tools,
                mcp_server_ids=mcp_server_ids,
            )
        )
        all_handlers = {**dict(static_handlers), **dynamic_handlers}
        tool_names = _filter_tool_names_with_policy_extensions(
            tuple(all_handlers.keys()),
            domain_profile,
            handlers=all_handlers,
        )
        return _freeze_or_project_capability_selection(
            engine,
            CapabilitySelection(
                light_hints=(),
                tool_names=tool_names,
                module_names=("all_tools",),
            ),
            all_handlers,
            profile_user_id=profile_user_id,
            session_id=session_id,
            client_mode="",
            domain_profile_id=domain_profile_id,
            character_pack_id=character_pack_id, client_context=client_context,
            authorization_profile_user_id=authorization_profile_user_id, raw_selection=_raw_selection,
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
        return _freeze_or_project_capability_selection(
            engine,
            selection,
            handlers,
            profile_user_id=profile_user_id,
            session_id=session_id,
            client_mode=str(getattr(client_context.effective_mode, "value", client_context.effective_mode) or ""),
            domain_profile_id=domain_profile_id,
            character_pack_id=character_pack_id, client_context=client_context,
            authorization_profile_user_id=authorization_profile_user_id, raw_selection=_raw_selection,
        )
    snapshot = build_capability_snapshot(
        engine,
        client_context=client_context,
        profile_user_id=profile_user_id,
        session_id=session_id,
        authorization_profile_user_id=authorization_profile_user_id,
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
        hidden_tool_names=domain_profile.hidden_tool_names,
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
        refresh_mcp_tools=refresh_mcp_tools,
        mcp_server_ids=mcp_server_ids,
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
    return _freeze_or_project_capability_selection(
        engine,
        selection,
        {**dict(handlers), **dynamic_handlers},
        profile_user_id=profile_user_id,
        session_id=session_id,
        client_mode=str(getattr(client_context.effective_mode, "value", client_context.effective_mode) or ""),
        domain_profile_id=domain_profile_id,
        character_pack_id=character_pack_id, client_context=client_context,
        authorization_profile_user_id=authorization_profile_user_id, raw_selection=_raw_selection,
    )


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


def _freeze_or_project_capability_selection(
    engine: Any,
    selection: CapabilitySelection,
    handlers: Mapping[str, Any],
    *,
    profile_user_id: str,
    session_id: str,
    client_mode: str,
    domain_profile_id: str,
    character_pack_id: str,
    client_context: ClientProtocolContext | None,
    authorization_profile_user_id: str,
    raw_selection: bool,
) -> CapabilitySelection:
    if raw_selection or (client_context is None and not profile_user_id and not session_id):
        return _freeze_capability_selection(selection, handlers)
    from ..capability_exposure import project_selection
    return project_selection(
        engine, selection, handlers, profile_user_id=profile_user_id, session_id=session_id,
        client_context=client_context, client_mode=client_mode, domain_profile_id=domain_profile_id,
        character_pack_id=character_pack_id, authorization_profile_user_id=authorization_profile_user_id,
    )


def restrict_capability_selection(
    selection: CapabilitySelection,
    *,
    allowed_tool_names: tuple[str, ...] | list[str],
) -> CapabilitySelection:
    """Intersect one already-resolved selection for a host-owned child turn.

    Filtering the frozen selection keeps schema visibility and execution
    dispatch on the same handler set.  It never resolves a hidden handler by
    name and therefore cannot widen the parent's effective capabilities.
    """

    allowed = {
        str(name or "").strip()
        for name in allowed_tool_names
        if str(name or "").strip()
    }
    if selection.execution_allowlist is not None:
        allowed.intersection_update(selection.execution_allowlist)
    tool_names = tuple(name for name in selection.tool_names if name in allowed)
    schema_tool_names = tuple(
        name for name in (selection.schema_tool_names or selection.tool_names) if name in allowed
    )
    handlers = getattr(selection, "resolved_handlers", {}) or {}
    catalog = getattr(selection, "capability_catalog", None)
    if catalog is not None:
        catalog = catalog.restricted(allowed)
        entries, _ = build_capability_discovery_handlers({}, refresh=catalog.current,
            profile_user_id=catalog.profile_user_id, session_id=catalog.session_id,
            character_pack_id=catalog.character_pack_id, client_mode=catalog.client_mode,
            domain_profile_id=catalog.domain_profile_id,
            authorization_profile_user_id=catalog.authorization_profile_user_id)
        handlers = {**handlers, **{name: handler for name, handler in entries.items() if name in allowed}}
    return replace(
        selection,
        tool_names=tool_names,
        schema_tool_names=schema_tool_names,
        native_tool_names=tuple(
            name for name in getattr(selection, "native_tool_names", ()) if name in allowed
        ),
        native_tool_aliases={
            name: alias
            for name, alias in dict(getattr(selection, "native_tool_aliases", {}) or {}).items()
            if alias in allowed
        },
        light_hints=(),
        module_names=(),
        layer_names=(),
        disclosures=tuple(
            disclosure
            for disclosure in selection.disclosures
            if disclosure.tool_names and all(name in allowed for name in disclosure.tool_names)
        ),
        tool_specs=tuple(spec for spec in selection.tool_specs if spec.capability_id in allowed),
        execution_receipts={
            name: receipt for name, receipt in selection.execution_receipts.items() if name in allowed
        },
        execution_allowlist=frozenset(allowed),
        capability_catalog=catalog,
        exposure_modes={name: mode for name, mode in selection.exposure_modes.items() if name in allowed},
        resolved_handlers=MappingProxyType(
            {name: handlers[name] for name in dict.fromkeys((*tool_names, *schema_tool_names)) if name in handlers}
        ),
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
    authorization_profile_user_id: str = "",
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
    execution_provider_present = bool(getattr(engine, "execution_provider", None))
    execution_approval_override = ""
    if client_context.effective_mode == ClientMode.QQ_TEXT:
        policy_profile_user_id = str(authorization_profile_user_id or "").strip()
        execution_profile_config = load_capability_config(
            base_dir=getattr(engine, "capability_config_base_dir", None),
            profile_user_id=policy_profile_user_id or profile_user_id,
        )
        if policy_profile_user_id:
            execution_approval_override = approval_mode_for_capability(
                execution_profile_config.get("approvalPolicy"),
                "exec_run",
                family_id="ops",
            )
        else:
            # Hidden/system QQ turns have no actor principal and therefore do
            # not inherit a person's broad access family.
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
    refresh_mcp_tools: bool = False,
    mcp_server_ids: frozenset[str] | None = None,
) -> dict[str, Any]:
    handlers: dict[str, Any] = {}
    handlers.update(build_plugin_capability_tool_handlers(engine, client_context=client_context))
    handlers.update(
        build_mcp_adapter_tool_handlers(
            engine,
            profile_user_id=profile_user_id,
            client_context=client_context,
            refresh_mcp_tools=refresh_mcp_tools,
            mcp_server_ids=mcp_server_ids,
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
    refresh_mcp_tools: bool = False,
    mcp_server_ids: frozenset[str] | None = None,
) -> dict[str, Any]:
    from ..capability_adapters import McpStdioCapabilityAdapter
    from ..tool_runtime import AdapterCapabilityToolHandler

    if mcp_server_ids == frozenset():
        return {}
    if not str(profile_user_id or "").strip():
        return {}
    config_base_dir = getattr(engine, "capability_config_base_dir", None)
    approval_store_getter = getattr(engine, "_get_approval_store", None)
    approval_store = (
        approval_store_getter()
        if callable(approval_store_getter)
        else getattr(engine, "approval_store", None)
    )
    if config_base_dir is None:
        return {}
    try:
        config_payload = load_capability_config(
            base_dir=config_base_dir,
            profile_user_id=profile_user_id,
        )
    except Exception:
        return {}
    if (
        approval_mode_for_capability(
            config_payload.get("approvalPolicy"),
            "mcp.family",
        )
        == APPROVAL_MODE_DISABLED
    ):
        return {}
    servers = get_effective_mcp_server_configs(
        base_dir=config_base_dir,
        profile_user_id=profile_user_id,
        profile_config=config_payload,
    )
    raw_cache = getattr(engine, "_mcp_capability_adapter_cache", None)
    adapter_cache: dict[tuple[str, str, str], Any] = raw_cache if isinstance(raw_cache, dict) else {}
    handlers: dict[str, Any] = {}
    adapters = []
    for server_id, server_config in sorted(servers.items(), key=lambda item: str(item[0])):
        if mcp_server_ids is not None and str(server_id) not in mcp_server_ids:
            continue
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
        prompt_tools = sorted(
            # Previously promptExposed described native-schema injection, and
            # load/invoke already allowed these enabled server tools. Present
            # the same eligible set to the shared directory; residency is now
            # selected separately. Explicit modelVisible=False is restrictive.
            ({**tool, "promptExposed": True} for tool in tools if tool.get("modelVisible") is not False),
            key=lambda tool: str(tool.get("name") or ""),
        )
        adapter_config = {**server_config, "serverId": str(server_id)}
        manager = getattr(engine, "mcp_host_manager", None)
        revision_reader = getattr(manager, "contract_revision", None)
        runtime_revision = revision_reader(profile_user_id=str(profile_user_id), server_id=str(server_id)) if callable(revision_reader) else ""
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "adapter": adapter_config,
                    "runtime_revision": runtime_revision,
                    "toolNames": [str(tool.get("name") or "") for tool in prompt_tools],
                },
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ).encode("utf-8")
        ).hexdigest()
        cache_key = (str(profile_user_id), str(server_id), fingerprint)
        adapter = adapter_cache.get(cache_key)
        if adapter is None:
            managed_client = None
            liveness_probe = getattr(engine, "mcp_liveness_probe", None)
            if manager is not None:
                managed_client = manager.client(
                    profile_user_id=str(profile_user_id),
                    server_id=str(server_id),
                    server_config=adapter_config,
                )

                def liveness_probe(*, server: Any, _manager=manager, _profile=str(profile_user_id), _server_id=str(server_id)):
                    return _manager.discover(
                        profile_user_id=_profile,
                        server_id=_server_id,
                        server_config=server,
                    )

            adapter = McpStdioCapabilityAdapter(
                provider_id=f"provider.mcp.{server_id}",
                server_id=str(server_id),
                server_config=adapter_config,
                tool_configs=tuple(prompt_tools),
                client=managed_client,
                liveness_probe=liveness_probe,
                contract_revision=runtime_revision,
                current_revision=(lambda _reader=revision_reader, _profile=str(profile_user_id), _server=str(server_id):
                    _reader(profile_user_id=_profile, server_id=_server)) if callable(revision_reader) else None,
            )
            if len(adapter_cache) >= 64:
                adapter_cache.clear()
            adapter_cache[cache_key] = adapter
        adapters.append(adapter)
    # Independent providers need not serialize initialize/tools-list waits.
    # Collect in configuration order so parallel discovery never changes the
    # published schema order (and therefore the provider's prompt cache).
    if len(adapters) > 1:
        with ThreadPoolExecutor(max_workers=min(4, len(adapters)), thread_name_prefix="mcp-discovery") as pool:
            futures = [pool.submit(adapter.current_tool_configs, refresh=refresh_mcp_tools) for adapter in adapters]
            definitions = [future.result() for future in futures]
    else:
        definitions = [adapter.current_tool_configs(refresh=refresh_mcp_tools) for adapter in adapters]
    for adapter, tools in zip(adapters, definitions):
        for tool in tools:
            descriptor = adapter.descriptor_for_tool(tool)
            if descriptor.id and descriptor.prompt_exposed:
                handler = AdapterCapabilityToolHandler(
                    capability_id=descriptor.id,
                    adapter=adapter,
                    descriptor=descriptor,
                    config_base_dir=config_base_dir,
                    approval_store=approval_store,
                )
                # Include declared metadata/semantic revisions even when the
                # input JSON Schema and accepted argument values are identical.
                from ..capability_contracts import digest
                handler.contract_revision = digest({"connection": adapter.contract_revision, "tool": tool})
                handlers[descriptor.id] = handler
    try:
        setattr(engine, "_mcp_capability_adapter_cache", adapter_cache)
    except Exception:
        pass
    return handlers


def resolve_unloaded_mcp_native_aliases(
    engine: Any, *, model_tool_names, profile_user_id, client_context,
    domain_profile_id, capability_selection,
):
    """Historical names identify targets; undisclosed contracts never execute."""
    del engine, profile_user_id, client_context, domain_profile_id
    if capability_selection is None:
        return {}, capability_selection
    from ..capability_contracts import ContractBoundHandler
    catalog = getattr(capability_selection, "capability_catalog", None)
    if catalog is None:
        return {}, capability_selection
    requested = set(model_tool_names)
    current = catalog.current()
    candidates = {name: current.handler(name) for name in current.capability_ids
                  if getattr(getattr(current.handler(name), "adapter", None), "server_id", None)}
    aliases = {}
    resolved = dict(capability_selection.resolved_handlers)
    for native in build_openai_native_tool_specs(candidates):
        model_name = native.get("function", {}).get("name")
        target = native.get(NATIVE_TOOL_CAPABILITY_ID_FIELD)
        if model_name not in requested or target not in candidates:
            continue
        aliases[model_name] = target
        if target not in capability_selection.schema_tool_names:
            snapshot = current.snapshot(target)
            snapshot["contract_ref"] = ""
            resolved[target] = ContractBoundHandler(candidates[target], snapshot,
                current_contract=catalog.current_snapshot)
    return aliases, replace(capability_selection, resolved_handlers=MappingProxyType(resolved),
        tool_names=tuple(dict.fromkeys((*capability_selection.tool_names, *aliases.values()))))


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
    approval_store_getter = getattr(engine, "_get_approval_store", None)
    approval_store = (
        approval_store_getter()
        if callable(approval_store_getter)
        else getattr(engine, "approval_store", None)
    )
    handlers: dict[str, Any] = {}
    for descriptor in adapter.list_capabilities_sync():
        if descriptor.id and descriptor.prompt_exposed:
            handlers[descriptor.id] = AdapterCapabilityToolHandler(
                capability_id=descriptor.id,
                adapter=adapter,
                descriptor=descriptor,
                config_base_dir=config_base_dir,
                approval_store=approval_store,
            )
    return handlers
