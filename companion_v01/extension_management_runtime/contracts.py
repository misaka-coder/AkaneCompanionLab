"""Runtime contract, selection identities and structured management results."""

from __future__ import annotations


import asyncio
from typing import Any, Iterable, Mapping, Protocol
from capcore import CapabilityResult, InvocationContext
from ..instance_profile import PluginSelection
from ..plugin_api import is_valid_plugin_id


PLUGIN_SELECTION_STATE_SCHEMA_VERSION = 1


PLUGIN_SELECTION_STATE_FILENAME = "plugin-selections.json"


class PluginManagementRuntime(Protocol):
    """The live plugin surface consumed by management and diagnostics.

    The Bot-owned isolated generation facade satisfies this contract. HTTP
    routes and model tools never choose between lifecycle implementations.
    """

    @property
    def selections(self) -> tuple[PluginSelection, ...]: ...

    @property
    def runtime_loop(self) -> asyncio.AbstractEventLoop | None: ...

    @property
    def code_reload_mode(self) -> str: ...

    def status_snapshot(self) -> dict[str, Any]: ...

    async def restart(self) -> dict[str, Any]: ...

    async def reconfigure(
        self,
        selections: tuple[PluginSelection, ...],
    ) -> dict[str, Any]: ...

    async def invoke(
        self,
        capability_id: str,
        args: Mapping[str, Any],
        *,
        context: InvocationContext,
    ) -> CapabilityResult: ...


def _normalize_selections(selections: Iterable[PluginSelection]) -> tuple[PluginSelection, ...]:
    normalized: list[PluginSelection] = []
    seen: set[str] = set()
    for item in tuple(selections):
        if (
            not isinstance(item, PluginSelection)
            or not is_valid_plugin_id(item.plugin_id)
            or not isinstance(item.enabled, bool)
        ):
            raise ValueError("plugin_selection_invalid")
        if item.plugin_id in seen:
            raise ValueError("duplicate_plugin_selection")
        seen.add(item.plugin_id)
        normalized.append(PluginSelection(item.plugin_id, item.enabled))
    return tuple(normalized)


def _merge_selection_overrides(
    defaults: tuple[PluginSelection, ...],
    overrides: Mapping[str, bool],
) -> tuple[PluginSelection, ...]:
    merged = [PluginSelection(item.plugin_id, overrides.get(item.plugin_id, item.enabled)) for item in defaults]
    known = {item.plugin_id for item in defaults}
    merged.extend(
        PluginSelection(plugin_id, overrides[plugin_id]) for plugin_id in sorted(overrides) if plugin_id not in known
    )
    return tuple(merged)


def _plugin_status(snapshot: Mapping[str, Any], plugin_id: str) -> dict[str, Any]:
    for item in list(snapshot.get("plugins") or []):
        if isinstance(item, Mapping) and str(item.get("plugin_id") or "") == plugin_id:
            return dict(item)
    return {}


def _selection_enabled(
    selections: Iterable[PluginSelection],
    plugin_id: str,
    *,
    missing: bool | None = False,
) -> bool | None:
    for item in selections:
        if item.plugin_id == plugin_id:
            return item.enabled
    return missing


def _runtime_matches(snapshot: Mapping[str, Any], selections: Iterable[PluginSelection]) -> bool:
    """Require proof of the whole restored selection, including removed plugins."""
    if snapshot.get("published") is False or snapshot.get("status") in {"degraded", "candidate_rejected", "error"}:
        return False
    expected = {item.plugin_id: item.enabled for item in selections}
    if expected and snapshot.get("ok") is False:
        return False
    actual = {
        str(item.get("plugin_id") or ""): item for item in snapshot.get("plugins", ()) if isinstance(item, Mapping)
    }
    return actual.keys() == expected.keys() and all(
        actual[plugin_id].get("status") in ({"active", "waiting_dependency"} if enabled else {"disabled"})
        and actual[plugin_id].get("enabled", enabled) == enabled
        for plugin_id, enabled in expected.items()
    )


def _upsert_selection(
    selections: Iterable[PluginSelection],
    plugin_id: str,
    *,
    enabled: bool,
) -> tuple[PluginSelection, ...]:
    current = tuple(selections)
    if plugin_id in {item.plugin_id for item in current}:
        return tuple(
            PluginSelection(item.plugin_id, enabled if item.plugin_id == plugin_id else item.enabled)
            for item in current
        )
    return (*current, PluginSelection(plugin_id, enabled))


def _failure(status: str, reason: str, *, plugin_id: str = "", schema_errors=(), diagnostics=()) -> dict[str, Any]:
    payload = {
        "ok": False,
        "status": status,
        "reason": reason,
        "plugin_id": str(plugin_id or ""),
    }
    if schema_errors:
        payload["schema_errors"] = [dict(error) for error in schema_errors]
    if diagnostics:
        payload["diagnostics"] = [dict(item) for item in diagnostics]
    return payload
