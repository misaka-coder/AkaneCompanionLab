"""One presentation projection over the existing host execution selection."""
from __future__ import annotations
from collections.abc import Mapping

from copy import deepcopy
from dataclasses import replace
from types import MappingProxyType

from .capability_contracts import ContractBoundHandler
from .capability_discovery import CAPABILITY_DISCOVERY_TOOL_IDS, build_capability_discovery_handlers
from .capability_exposure_config import exposure_mode, read_preferences


def project_selection(engine, selection, handlers, *, profile_user_id, session_id, client_context,
                      client_mode, domain_profile_id, character_pack_id="", authorization_profile_user_id=""):
    from .engine_services.tool_rounds import resolve_capability_selection

    preferences = read_preferences(base_dir=getattr(engine, "capability_config_base_dir", None), profile_user_id=profile_user_id)
    scope_args = dict(profile_user_id=profile_user_id, session_id=session_id, client_mode=client_mode,
                      domain_profile_id=domain_profile_id, character_pack_id=character_pack_id,
                      authorization_profile_user_id=authorization_profile_user_id)

    def refresh(mcp_server_ids=None, capability_ids=None):
        from .plugin_invocation_scope import use_generation_scopes
        # Model discovery is always current. Already-admitted program calls
        # keep their existing plugin invocation scopes and cleanup semantics.
        with use_generation_scopes(()):
            current = resolve_capability_selection(
                engine, client_context=client_context, profile_user_id=profile_user_id,
                session_id=session_id, domain_profile_id=domain_profile_id,
                character_pack_id=character_pack_id,
                authorization_profile_user_id=authorization_profile_user_id, _raw_selection=True,
                refresh_mcp_tools=True,
                mcp_server_ids=mcp_server_ids,
            )
        _, catalog = build_capability_discovery_handlers(
            {name: current.resolved_handlers[name] for name in current.tool_names
             if name in current.resolved_handlers and (capability_ids is None or name in capability_ids)}, **scope_args)
        return catalog

    def refresh_targets(capability_ids):
        # Re-read host selection and authorization on every check, but contact
        # only the MCP providers whose contracts are actually being used.
        # Unknown MCP IDs still use full discovery so newly enabled tools load.
        servers = set()
        for name in capability_ids:
            handler = candidates.get(name)
            server_id = getattr(getattr(handler, "adapter", None), "server_id", None)
            if server_id:
                servers.add(str(server_id))
            elif str(name).startswith("mcp."):
                return refresh(capability_ids=capability_ids)
        return refresh(frozenset(servers), capability_ids=capability_ids)

    candidates = {name: handlers[name] for name in selection.tool_names if name in handlers and name not in CAPABILITY_DISCOVERY_TOOL_IDS}
    entries, catalog = build_capability_discovery_handlers(candidates, refresh=refresh, refresh_targets=refresh_targets,
        search_enabled=preferences["searchEnabled"], **scope_args)
    modes = {name: exposure_mode(name, handler, preferences) for name, handler in candidates.items()}
    resident = tuple(sorted(name for name, mode in modes.items() if mode == "resident"))
    resolved = dict(candidates)
    for name in catalog.capability_ids:
        binding = catalog.snapshot(name)
        if modes.get(name) != "resident":
            # A deferred target is eligible for routing, but no native contract
            # was published. Only invoke(ref) may bind its disclosed version.
            binding["contract_ref"] = ""
        resolved[name] = ContractBoundHandler(candidates[name], binding, current_contract=catalog.current_snapshot)
    resolved.update(entries)
    return replace(selection,
        tool_names=tuple(sorted(resolved)),
        schema_tool_names=tuple(sorted((*resident, *entries))),
        resolved_handlers=MappingProxyType(resolved),
        capability_catalog=catalog,
        exposure_modes=MappingProxyType(modes),
        exposure_preferences=MappingProxyType(preferences),
    )


def published_contracts(selection):
    """Serializable native declaration bindings; contains no handlers or grants."""
    from .capability_contracts import contract_snapshot
    catalog = selection.capability_catalog
    return {
        name: contract_snapshot(selection.resolved_handlers[name], scope=catalog.scope)
        for name in selection.schema_tool_names
        if name in selection.resolved_handlers
    }


def apply_published_contracts(selection, snapshots):
    catalog = selection.capability_catalog
    resolved = dict(selection.resolved_handlers)
    fixed, _ = build_capability_discovery_handlers({}, refresh=catalog.current,
        profile_user_id=catalog.profile_user_id, session_id=catalog.session_id)
    for name, snapshot in snapshots.items():
        if name in CAPABILITY_DISCOVERY_TOOL_IDS:
            # Reconstruct discovery entries from the published request snapshot.
            if name not in resolved and name in fixed:
                resolved[name] = fixed[name]
            continue
        current_handler = catalog.handler(name)
        resolved[name] = ContractBoundHandler(current_handler, snapshot, current_contract=catalog.current_snapshot)
    return replace(selection, schema_tool_names=tuple(sorted(snapshots)),
                   tool_names=tuple(sorted(resolved)), resolved_handlers=MappingProxyType(resolved),
                   published_contracts=deepcopy(snapshots))


def directory_text(selection):
    catalog = selection.capability_catalog
    lines = ["【可按需使用的工具】", "按精确 ID 调用 capability_load 获取完整契约，再用 capability_invoke 执行。搜索可选；capability_list 可分页浏览。"]
    for item in catalog.short_entries():
        if selection.exposure_modes.get(item["capability_id"]) == "on_demand":
            lines.append(f"- {item['capability_id']}：{item['description']}")
    if len(lines) == 2:
        lines.append("当前没有按需工具。常驻工具按本轮原生声明直接调用。")
    return "\n".join(lines)


def catalog_state(selection, plugin_blocks=()):
    from .capability_contracts import digest
    catalog = selection.capability_catalog
    return {"tools": {name: {"fingerprint": catalog.snapshot(name)["fingerprint"],
                             "mode": selection.exposure_modes.get(name, "resident")}
                       for name in catalog.capability_ids},
            "plugin_blocks": digest(list(plugin_blocks))}


def native_exposure_scope(catalog_scope, preferences):
    """A saved presentation change publishes on the next request, independently of plugin rules."""
    from .capability_contracts import digest
    return digest({"native_catalog": catalog_scope, "preferences_revision": preferences["revision"]})


def freeze_for_projection(engine, selection, projection, *, plugin_blocks, profile_user_id, session_id, character_pack_id,
                          client_mode="", domain_profile_id="", authorization_profile_user_id=""):
    """Resolve a durable baseline only on the actual MemCore authority path."""
    from .capability_contracts import digest, scope_key
    snapshot = {"contracts": {}, "plugin_blocks": list(plugin_blocks)}
    # Text-only clients also receive plugin system blocks. Their empty native
    # partition must not establish an empty baseline for a tool-enabled client.
    scope = selection.capability_catalog.scope if selection is not None else digest({
        "prompt_only": scope_key(profile_user_id=profile_user_id, session_id=session_id,
            character_pack_id=character_pack_id, client_mode=client_mode, domain_profile_id=domain_profile_id,
            authorization_profile_user_id=authorization_profile_user_id)})
    manager = getattr(engine, "memcore_manager", None)
    resolver = getattr(manager, "resolve_tool_exposure_snapshot", None)
    if not callable(resolver):
        return selection, tuple(plugin_blocks), {"ok": False, "reason": "tool_exposure_snapshot_unavailable"}
    context = dict(compaction_generation=int(projection["compaction_generation"]), profile_user_id=profile_user_id,
        session_id=session_id, character_pack_id=character_pack_id)
    result = resolver(snapshot=snapshot, scope=scope, **context)
    if not result.get("ok"):
        return selection, tuple(plugin_blocks), result
    baseline = result["snapshot"]
    # Removing/changing a higher-priority plugin rule is an explicit safety
    # exception. New rules wait for compaction; revoked rules stop immediately.
    safe_blocks = tuple(block for block in baseline["plugin_blocks"] if block in plugin_blocks)
    revoked = len(safe_blocks) != len(baseline["plugin_blocks"])
    projected = None
    pending = list(plugin_blocks) != baseline["plugin_blocks"]
    if selection is not None:
        native = {"contracts": published_contracts(selection), "plugin_blocks": []}
        result = resolver(snapshot=native,
            scope=native_exposure_scope(scope, selection.exposure_preferences), **context)
        if not result.get("ok"):
            return selection, safe_blocks, result
        projected = apply_published_contracts(selection, result["snapshot"]["contracts"])
        pending = pending or digest(native) != digest(result["snapshot"])
    return projected, safe_blocks, {"ok": True, "status": result["status"],
        "compaction_generation": result["compaction_generation"],
        "preferences_revision": selection.exposure_preferences["revision"] if selection is not None else None,
        "pending": pending, "system_revocation_exception": revoked}


def route_invocation(raw_call, selection):
    """Convert a generic call to its real target before normalization and hooks."""
    if not _entry_allowed(selection, "capability_invoke"):
        return raw_call, selection
    return _route_target(raw_call, selection)


def _entry_allowed(selection, name):
    ceiling = getattr(selection, "execution_allowlist", None)
    return name in getattr(selection, "tool_names", ()) and (ceiling is None or name in ceiling)


def _route_target(raw_call, selection):
    from .tool_invocation import TOOL_MODEL_NAME_FIELD, TOOL_MODEL_ARGUMENTS_FIELD
    catalog = getattr(selection, "capability_catalog", None)
    if catalog is None:
        return raw_call, selection
    arguments = raw_call.get(TOOL_MODEL_ARGUMENTS_FIELD)
    if not isinstance(arguments, dict):
        arguments = {key: value for key, value in raw_call.items() if key != "type" and not key.startswith("_tool_")}
    target = arguments.get("capability_id")
    values = arguments.get("arguments")
    reference = arguments.get("contract_ref")
    if not isinstance(target, str) or not target or not isinstance(values, dict):
        return raw_call, selection
    if target in CAPABILITY_DISCOVERY_TOOL_IDS:
        return raw_call, selection
    current = catalog.current_for((target,))
    snapshot = current.snapshot(target)
    if snapshot is None:
        # Preserve the exact attempted target so the normal executor produces
        # a paired unavailable result, without inventing a ready handler.
        routed_selection = selection
    else:
        snapshot["contract_ref"] = str(reference or "")
        resolved = dict(selection.resolved_handlers)
        resolved[target] = ContractBoundHandler(current.handler(target), snapshot, current_contract=catalog.current_snapshot)
        routed_selection = replace(selection, resolved_handlers=MappingProxyType(resolved),
                                   tool_names=tuple(dict.fromkeys((*selection.tool_names, target))))
    transport = {key: value for key, value in raw_call.items() if key.startswith("_tool_")}
    # The generic carrier has no device receipt of its own. Bind the routed
    # target to the host's frozen receipt, never to model-supplied metadata.
    from .tool_invocation import TOOL_EXECUTION_RECEIPT_FIELD
    transport.pop(TOOL_EXECUTION_RECEIPT_FIELD, None)
    receipt = getattr(selection, "execution_receipts", {}).get(target)
    if isinstance(receipt, Mapping):
        transport[TOOL_EXECUTION_RECEIPT_FIELD] = dict(receipt)
    # Native host tools historically use flat arguments; adapters retain their
    # opaque business object. The provider carrier always retains the outer call.
    handler = current.handler(target)
    from .tool_handlers.adapters import AdapterCapabilityToolHandler
    payload = {"arguments": deepcopy(values)} if isinstance(handler, AdapterCapabilityToolHandler) else deepcopy(values)
    return {**payload, "type": target, **transport,
            TOOL_MODEL_NAME_FIELD: "capability_invoke", TOOL_MODEL_ARGUMENTS_FIELD: deepcopy(arguments)}, routed_selection


def route_mcp_invocation(raw_call, selection):
    """Legacy server/tool coordinates translate into the shared contract route."""
    if not _entry_allowed(selection, "invoke_mcp"):
        return raw_call, selection
    from .tool_invocation import TOOL_MODEL_NAME_FIELD, TOOL_MODEL_ARGUMENTS_FIELD
    catalog = getattr(selection, "capability_catalog", None)
    if catalog is None:
        return raw_call, selection
    arguments = raw_call.get(TOOL_MODEL_ARGUMENTS_FIELD) or {
        key: value for key, value in raw_call.items() if key != "type" and not key.startswith("_tool_")}
    current = catalog.current()
    for name in current.capability_ids:
        adapter = getattr(current.handler(name), "adapter", None)
        if (getattr(adapter, "server_id", None) == arguments.get("server_id")
                and callable(getattr(adapter, "tool_name_for_capability", None))
                and adapter.tool_name_for_capability(name) == arguments.get("tool_name")):
            converted = {**raw_call, "type": "capability_invoke", TOOL_MODEL_ARGUMENTS_FIELD: {
                "capability_id": name, "arguments": arguments.get("arguments"),
                "contract_ref": arguments.get("contract_ref", "")}}
            routed, selected = _route_target(converted, selection)
            routed[TOOL_MODEL_NAME_FIELD] = "invoke_mcp"
            routed[TOOL_MODEL_ARGUMENTS_FIELD] = deepcopy(arguments)
            return routed, selected
    return raw_call, selection
