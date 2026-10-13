"""Private management status, public inventory and connection configuration."""

from __future__ import annotations


from copy import deepcopy
from typing import Any, Mapping
from ..plugin_installation import PluginInstallationError
from .contracts import _failure


class ManagementInventory:
    __slots__ = ()

    def connection_config(self, *, plugin_id, name, profile_user_id, payload=None):
        from akane_plugin import ConnectionSpec

        if self.connection_settings is None:
            return _failure("unavailable", "connection_settings_unavailable", plugin_id=plugin_id)
        plugin = next((item for item in self.public_snapshot()["plugins"] if item["plugin_id"] == plugin_id), None)
        if plugin is None:
            return _failure("not_found", "plugin_not_found", plugin_id=plugin_id)
        raw = next((item for item in plugin["contributions"].get("connections", []) if item.get("name") == name), None)
        if raw is None:
            return _failure("not_found", "connection_not_found", plugin_id=plugin_id)
        try:
            spec = ConnectionSpec.from_dict(raw)
        except (TypeError, ValueError):
            return _failure("invalid_config", "connection_declaration_invalid", plugin_id=plugin_id)
        if payload is None:
            return self.connection_settings.read(profile_user_id, plugin_id, spec)
        return self.connection_settings.save(profile_user_id, plugin_id, spec, payload)

    def snapshot(self) -> dict[str, Any]:
        payload = dict(self.plugin_runtime.status_snapshot())
        payload["kind"] = "plugin"
        artifact_snapshot: dict[str, Any] = {"status": "not_configured", "plugins": [], "stages": []}
        if self.artifact_store is not None:
            try:
                artifact_snapshot = self.artifact_store.snapshot()
            except PluginInstallationError as exc:
                artifact_snapshot = {"status": exc.status, "reason": exc.reason, "plugins": [], "stages": []}
        payload["artifacts"] = artifact_snapshot
        payload["management"] = {
            "status": "ready",
            "persistence": "instance_overlay",
            "load_reason": self.selection_store.load_reason,
            "supports": [
                "list",
                "enable",
                "disable",
                *(["market", "stage_market"] if self.market is not None and self.artifact_store is not None else []),
                *(
                    [
                        "test_source",
                        "stage_wheel",
                        "stage_source",
                        "install",
                        "discard_stage",
                        "rollback",
                        "uninstall",
                    ]
                    if self.artifact_store is not None
                    else []
                ),
            ],
            "code_reload": self.plugin_runtime.code_reload_mode,
        }
        return payload

    def public_snapshot(self) -> dict[str, Any]:
        """Return the path-free plugin inventory used by user-facing clients."""

        snapshot = self.snapshot()
        artifacts = snapshot.get("artifacts")
        artifact_entries = artifacts.get("plugins", ()) if isinstance(artifacts, Mapping) else ()
        artifacts_by_id = {
            str(item.get("plugin_id") or ""): item
            for item in artifact_entries
            if isinstance(item, Mapping) and str(item.get("plugin_id") or "")
        }
        plugins: list[dict[str, Any]] = []
        for raw in snapshot.get("plugins", ()):
            if not isinstance(raw, Mapping):
                continue
            plugin_id = str(raw.get("plugin_id") or "").strip()
            if not plugin_id:
                continue
            artifact = artifacts_by_id.get(plugin_id, {})
            live_contribution = raw.get("contribution_snapshot")
            artifact_contribution = artifact.get("contribution_snapshot")
            contribution = (
                live_contribution
                if isinstance(live_contribution, Mapping)
                else artifact_contribution
                if isinstance(artifact_contribution, Mapping)
                else {}
            )
            version = str(raw.get("plugin_version") or artifact.get("version") or "").strip()
            status = str(raw.get("status") or "unavailable").strip().lower()
            plugins.append(
                {
                    "plugin_id": plugin_id,
                    "version": version,
                    "source": "managed" if artifact else "bundled",
                    "manageable": True,
                    "enabled": bool(raw.get("enabled")),
                    "runtime_status": status,
                    "reason": str(raw.get("reason") or "").strip(),
                    **(
                        {"dependency_errors": deepcopy(raw["dependency_errors"])}
                        if raw.get("dependency_errors")
                        else {}
                    ),
                    "generation": int(contribution.get("generation") or snapshot.get("generation") or 0),
                    "surfaces": _public_string_list(
                        contribution.get("surfaces"),
                        allowed={"desktop", "qq"},
                    ),
                    "contributions": _public_contributions(contribution),
                    "permissions": _public_string_list(raw.get("permissions") or artifact.get("permissions")),
                    "declared_only": status == "waiting_dependency" or not isinstance(live_contribution, Mapping),
                    "pending_activation": bool(artifact.get("pending_activation")),
                    "rollback_available": bool(artifact.get("last_good_digest")),
                }
            )
        plugins.sort(key=lambda item: item["plugin_id"])
        return {
            "ok": True,
            "status": str(snapshot.get("status") or "unavailable"),
            "reason": str(snapshot.get("reason") or ""),
            "generation": int(snapshot.get("generation") or 0),
            "plugins": plugins,
            **(
                {"dependency_supply": deepcopy(artifacts.get("dependency_supply"))}
                if isinstance(artifacts, Mapping) and isinstance(artifacts.get("dependency_supply"), Mapping)
                else {}
            ),
            "plugin_count": len(plugins),
            **(
                {"service_bindings": deepcopy(snapshot["service_bindings"])} if snapshot.get("service_bindings") else {}
            ),
        }


def _public_string_list(
    value: Any,
    *,
    allowed: set[str] | None = None,
) -> list[str]:
    """Project a bounded, deterministic list of public identifiers."""

    if not isinstance(value, (list, tuple, set, frozenset)):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for raw in value:
        item = str(raw or "").strip()
        if not item or len(item) > 160 or item in seen:
            continue
        if allowed is not None and item not in allowed:
            continue
        seen.add(item)
        result.append(item)
        if len(result) >= 64:
            break
    return sorted(result)


def _public_contributions(value: Mapping[str, Any]) -> dict[str, Any]:
    """Project executable identifiers and admitted versioned service schemas."""

    result = {
        key: _public_string_list(value.get(key))
        for key in (
            "capabilities",
            "commands",
            "event_handlers",
            "hooks",
            "background_services",
            "prompt_blocks",
            "skills",
        )
    }
    services = value.get("services")
    if isinstance(services, list) and services:
        result["services"] = deepcopy(services)
    dependencies = value.get("requires_services")
    if isinstance(dependencies, list) and dependencies:
        result["requires_services"] = deepcopy(dependencies)
    connections = value.get("connections")
    if isinstance(connections, list) and connections:
        result["connections"] = deepcopy(connections)
    return result
