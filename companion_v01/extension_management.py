"""One authoritative lifecycle service for installed Akane extensions.

The persisted selection overlay and managed artifact catalog feed the same
isolated generation runtime; management never imports plugin code itself.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol

from capcore import CapabilityResult, InvocationContext

from .instance_profile import PluginSelection
from .plugin_api import is_valid_plugin_id
from .plugin_installation import ManagedPluginArtifactStore, PluginInstallationError
from .plugin_service_dependencies import provider_bindings


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


class PluginSelectionStore:
    """Persist instance plugin enablement as a small atomic JSON overlay."""

    def __init__(
        self,
        path: Path,
        *,
        defaults: tuple[PluginSelection, ...],
        instance_id: str,
    ) -> None:
        self.path = Path(path)
        self.defaults = _normalize_selections(defaults)
        self.instance_id = str(instance_id or "").strip()
        self._lock = threading.RLock()
        self.load_reason = ""

    def load(self) -> tuple[PluginSelection, ...]:
        with self._lock:
            if not self.path.exists():
                self.load_reason = ""
                return self.defaults
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
                overrides = self._parse_payload(payload)
            except Exception:
                self.load_reason = "plugin_selection_state_invalid"
                return self.defaults
            self.load_reason = ""
            return _merge_selection_overrides(self.defaults, overrides)

    def load_service_bindings(self):
        with self._lock:
            if not self.path.exists():
                return ()
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            self._parse_payload(payload)
            bindings = provider_bindings(payload.get("service_bindings", ()))
            return tuple({"service_id": key[0], "version": key[1], "plugin_id": value}
                         for key, value in sorted(bindings.items()))

    def save(self, selections: tuple[PluginSelection, ...], *, service_bindings=None) -> None:
        normalized = _normalize_selections(selections)
        default_by_id = {item.plugin_id: item.enabled for item in self.defaults}
        overrides = {
            item.plugin_id: item.enabled
            for item in normalized
            if default_by_id.get(item.plugin_id) is None
            or default_by_id[item.plugin_id] != item.enabled
        }
        payload = {
            "schema_version": PLUGIN_SELECTION_STATE_SCHEMA_VERSION,
            "instance_id": self.instance_id,
            "overrides": {key: overrides[key] for key in sorted(overrides)},
        }
        with self._lock:
            if service_bindings is None:
                service_bindings = self.load_service_bindings()
            bindings = provider_bindings(service_bindings)
            if bindings:
                payload["service_bindings"] = [{"service_id": key[0], "version": key[1], "plugin_id": value}
                                              for key, value in sorted(bindings.items())]
            data = (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
            try:
                with temp_path.open("xb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_path, self.path)
                self.load_reason = ""
            finally:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def _parse_payload(self, payload: Any) -> dict[str, bool]:
        if not isinstance(payload, Mapping):
            raise ValueError("plugin_selection_state_root_invalid")
        if payload.get("schema_version") != PLUGIN_SELECTION_STATE_SCHEMA_VERSION:
            raise ValueError("plugin_selection_state_schema_unsupported")
        stored_instance_id = str(payload.get("instance_id") or "").strip()
        if stored_instance_id != self.instance_id:
            raise ValueError("plugin_selection_state_instance_mismatch")
        raw_overrides = payload.get("overrides")
        if not isinstance(raw_overrides, Mapping):
            raise ValueError("plugin_selection_state_overrides_invalid")
        overrides: dict[str, bool] = {}
        for raw_plugin_id, raw_enabled in raw_overrides.items():
            plugin_id = str(raw_plugin_id or "").strip()
            if not is_valid_plugin_id(plugin_id) or not isinstance(raw_enabled, bool):
                raise ValueError("plugin_selection_state_entry_invalid")
            overrides[plugin_id] = raw_enabled
        return overrides

    def save_service_binding(self, *, service_id: str, version: int, plugin_id: str, expected_sha256: str):
        """Update one binding in a reviewed file; activation remains explicit.

        Preserve unknown fields and all other selections. Check the exact bytes
        again immediately before replacement to reject intervening edits.
        """
        import hashlib

        binding = {"service_id": service_id, "version": version, "plugin_id": plugin_id}
        provider_bindings((binding,))
        with self._lock:
            original = self.path.read_bytes()
            if hashlib.sha256(original).hexdigest() != expected_sha256:
                raise ValueError("plugin_selection_conflict")
            payload = json.loads(original)
            self._parse_payload(payload)
            bindings = dict(provider_bindings(payload.get("service_bindings", ())))
            bindings[(service_id, version)] = plugin_id
            payload["service_bindings"] = [{"service_id": key[0], "version": key[1], "plugin_id": value}
                                           for key, value in sorted(bindings.items())]
            data = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
            try:
                with temporary.open("xb") as output:
                    output.write(data)
                    output.flush()
                    os.fsync(output.fileno())
                if self.path.read_bytes() != original:
                    raise ValueError("plugin_selection_conflict")
                os.replace(temporary, self.path)
            finally:
                temporary.unlink(missing_ok=True)
            return {"ok": True, "status": "saved", "activated": False,
                    "sha256": hashlib.sha256(data).hexdigest(), "binding": binding}


class ExtensionManagementService:
    """Coordinate persisted selection and the one live plugin runtime."""

    def __init__(
        self,
        *,
        plugin_runtime: PluginManagementRuntime,
        selection_store: PluginSelectionStore,
        artifact_store: ManagedPluginArtifactStore | None = None,
        market: Any = None,
        sync_timeout_seconds: float | None = None,
        connection_settings: Any = None,
    ) -> None:
        self.plugin_runtime = plugin_runtime
        self.selection_store = selection_store
        self.artifact_store = artifact_store
        self.market = market
        self.connection_settings = connection_settings
        self.sync_timeout_seconds = (
            None if sync_timeout_seconds is None else max(1.0, float(sync_timeout_seconds))
        )
        self._operation_lock = asyncio.Lock()

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
        artifact_entries = (
            artifacts.get("plugins", ())
            if isinstance(artifacts, Mapping)
            else ()
        )
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
            version = str(
                raw.get("plugin_version")
                or artifact.get("version")
                or ""
            ).strip()
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
                    **({"dependency_errors": deepcopy(raw["dependency_errors"])} if raw.get("dependency_errors") else {}),
                    "generation": int(
                        contribution.get("generation")
                        or snapshot.get("generation")
                        or 0
                    ),
                    "surfaces": _public_string_list(
                        contribution.get("surfaces"),
                        allowed={"desktop", "qq"},
                    ),
                    "contributions": _public_contributions(contribution),
                    "permissions": _public_string_list(
                        raw.get("permissions") or artifact.get("permissions")
                    ),
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
            **({"service_bindings": deepcopy(snapshot["service_bindings"])} if snapshot.get("service_bindings") else {}),
        }

    async def restart(self, *, requested_plugin_id: str = "") -> dict[str, Any]:
        plugin_id = str(requested_plugin_id or "").strip()
        desired = self.selection_store.load()
        if plugin_id and plugin_id not in {item.plugin_id for item in desired}:
            return _failure("not_found", "plugin_not_configured", plugin_id=plugin_id)
        async with self._operation_lock:
            result = dict(await self.plugin_runtime.reconfigure(desired))
            artifact_status = self.reconcile_runtime(result)
        if plugin_id and result.get("published") is not False:
            target = _plugin_status(result, plugin_id)
            expected = "active" if _selection_enabled(desired, plugin_id) else "disabled"
            if target.get("status") not in ({"active", "waiting_dependency"} if expected == "active" else {expected}):
                return {
                    "ok": False,
                    "status": "reload_failed",
                    "reason": "plugin_reload_target_missing",
                    "action": "restart",
                    "plugin_id": plugin_id,
                    "candidate": target,
                    "artifact_status": artifact_status,
                }
        result.update(
            {
                "action": "restart",
                "scope": "plugin_runtime",
                "requested_plugin_id": plugin_id,
                "artifact_status": artifact_status,
            }
        )
        return result

    async def set_enabled(self, *, plugin_id: str, enabled: bool) -> dict[str, Any]:
        normalized_id = str(plugin_id or "").strip()
        if not is_valid_plugin_id(normalized_id):
            return _failure("invalid_request", "invalid_plugin_id", plugin_id=normalized_id)
        async with self._operation_lock:
            desired = self.selection_store.load()
            if normalized_id not in {item.plugin_id for item in desired}:
                return _failure("not_found", "plugin_not_configured", plugin_id=normalized_id)
            previous_live = self.plugin_runtime.selections
            previous_enabled = _selection_enabled(desired, normalized_id)
            live_enabled = _selection_enabled(previous_live, normalized_id, missing=None)
            if previous_enabled == bool(enabled) and live_enabled == bool(enabled):
                payload = self.snapshot()
                payload["runtime_status"] = str(payload.get("status") or "unknown")
                payload.update(
                    {
                        "ok": True,
                        "status": "enabled" if enabled else "disabled",
                        "action": "enable" if enabled else "disable",
                        "plugin_id": normalized_id,
                        "unchanged": True,
                    }
                )
                return payload

            candidate = tuple(
                PluginSelection(item.plugin_id, bool(enabled) if item.plugin_id == normalized_id else item.enabled)
                for item in desired
            )
            candidate_status = dict(await self.plugin_runtime.reconfigure(candidate))
            target = _plugin_status(candidate_status, normalized_id)
            if candidate_status.get("published") is False or target.get("status") not in ({"active", "waiting_dependency"} if enabled else {"disabled"}):
                rollback_status = "not_required"
                if candidate_status.get("published") is not False:
                    rollback = dict(await self.plugin_runtime.reconfigure(previous_live))
                    rollback_status = str(rollback.get("status") or "unknown")
                artifact_status = self.reconcile_runtime(candidate_status)
                return {
                    "ok": False,
                    "status": "activation_failed",
                    "reason": str(
                        candidate_status.get("reason")
                        or target.get("reason")
                        or "plugin_state_change_failed"
                    ),
                    "action": "enable" if enabled else "disable",
                    "plugin_id": normalized_id,
                    "candidate": target,
                    "rollback_status": rollback_status,
                    "artifact_status": artifact_status,
                }
            try:
                self.selection_store.save(candidate)
            except Exception:
                rollback = dict(await self.plugin_runtime.reconfigure(previous_live))
                return {
                    "ok": False,
                    "status": "persist_failed",
                    "reason": "plugin_selection_persist_failed",
                    "action": "enable" if enabled else "disable",
                    "plugin_id": normalized_id,
                    "rollback_status": str(rollback.get("status") or "unknown"),
                }
            artifact_status = self.reconcile_runtime(candidate_status)
            payload = self.snapshot()
            payload["artifact_status"] = artifact_status
            payload["runtime_status"] = str(payload.get("status") or "unknown")
            payload.update(
                {
                    "ok": True,
                    "status": "waiting_dependency" if target.get("status") == "waiting_dependency" else "enabled" if enabled else "disabled",
                    "action": "enable" if enabled else "disable",
                    "plugin_id": normalized_id,
                    "unchanged": False,
                }
            )
            return payload

    async def install_stage(
        self,
        *,
        stage_id: str,
        approved_permissions: Iterable[str],
    ) -> dict[str, Any]:
        """Publish one immutable stage and make it effective in one operation.

        Staging is the review boundary.  Once the exact permission set is
        approved, callers should not have to coordinate the artifact catalog,
        persisted selections and live generation themselves.
        """

        if self.artifact_store is None:
            return _failure("unavailable", "plugin_artifact_store_unavailable")
        async with self._operation_lock:
            desired_before = self.selection_store.load()
            live_before = self.plugin_runtime.selections
            try:
                published = dict(
                    await asyncio.to_thread(
                        self.artifact_store.publish_stage,
                        stage_id,
                        approved_permissions=tuple(approved_permissions),
                    )
                )
            except PluginInstallationError as exc:
                return _failure(exc.status, exc.reason, schema_errors=exc.schema_errors, diagnostics=exc.diagnostics)
            except Exception:
                return _failure("error", "plugin_install_failed")
            if not published.get("ok"):
                return published

            plugin_id = str(published.get("plugin_id") or "")
            existed = plugin_id in {item.plugin_id for item in desired_before}
            enabled = _selection_enabled(desired_before, plugin_id) if existed else True
            candidate = _upsert_selection(desired_before, plugin_id, enabled=enabled)
            candidate_status = dict(await self.plugin_runtime.reconfigure(candidate))
            target = _plugin_status(candidate_status, plugin_id)
            expected_status = "active" if enabled else "disabled"
            if candidate_status.get("published") is False or target.get("status") not in ({"active", "waiting_dependency"} if enabled else {"disabled"}):
                artifact_status = self.reconcile_runtime(candidate_status)
                rollback_ok = await self._restore_install_state(
                    plugin_id=plugin_id,
                    existed=existed,
                    desired_before=desired_before,
                    live_before=live_before,
                )
                return {
                    "ok": False,
                    "status": "activation_failed" if rollback_ok else "rollback_failed",
                    "reason": str(
                        (
                            candidate_status.get("reason")
                            or target.get("reason")
                            or "plugin_activation_failed"
                        )
                        if rollback_ok
                        else "plugin_install_rollback_failed"
                    ),
                    "action": "install",
                    "plugin_id": plugin_id,
                    "candidate": target,
                    "artifact_status": artifact_status,
                }

            try:
                self.selection_store.save(candidate)
            except Exception:
                rollback_ok = await self._restore_install_state(
                    plugin_id=plugin_id,
                    existed=existed,
                    desired_before=desired_before,
                    live_before=live_before,
                )
                return {
                    "ok": False,
                    "status": "persist_failed" if rollback_ok else "rollback_failed",
                    "reason": (
                        "plugin_selection_persist_failed"
                        if rollback_ok
                        else "plugin_install_rollback_failed"
                    ),
                    "action": "install",
                    "plugin_id": plugin_id,
                }

            artifact_status = self.reconcile_runtime(candidate_status)
            return {
                "ok": True,
                "status": target.get("status", expected_status),
                "action": "install",
                "plugin_id": plugin_id,
                "version": str(published.get("version") or ""),
                "digest": str(published.get("digest") or ""),
                "permissions": list(published.get("permissions") or ()),
                "installed_status": str(published.get("status") or "installed"),
                "runtime": target,
                "artifact_status": artifact_status,
                "unchanged": bool(published.get("unchanged")),
            }

    async def _restore_install_state(
        self,
        *,
        plugin_id: str,
        existed: bool,
        desired_before: tuple[PluginSelection, ...],
        live_before: tuple[PluginSelection, ...],
    ) -> bool:
        try:
            if existed:
                rollback = await asyncio.to_thread(
                    self.artifact_store.rollback_to_last_good,
                    plugin_id,
                )
                if not rollback.get("ok") and rollback.get("status") != "unchanged":
                    return False
            else:
                removed = await asyncio.to_thread(self.artifact_store.remove_plugin, plugin_id,
                                                  **self._artifact_retention())
                if not removed.get("ok"):
                    return False
            self.selection_store.save(desired_before)
            restored = dict(await self.plugin_runtime.reconfigure(live_before))
            if restored.get("published") is False:
                return False
            self.reconcile_runtime(restored)
            return True
        except Exception:
            return False

    async def browse_market(self) -> dict[str, Any]:
        if self.market is None:
            return {**_failure("unavailable", "plugin_market_not_configured"), "plugins": []}
        try:
            payload = await asyncio.to_thread(self.market.browse)
            installed = {item["plugin_id"]: item for item in self.public_snapshot()["plugins"]}
            for item in payload["plugins"]:
                current = installed.get(item["plugin_id"], {})
                item["installed_version"] = current.get("version", "")
                item["installed_status"] = current.get("runtime_status", "not_installed")
            return payload
        except PluginInstallationError as exc:
            return {**_failure(exc.status, exc.reason, schema_errors=exc.schema_errors, diagnostics=exc.diagnostics), "plugins": []}
        except Exception:
            return {**_failure("error", "plugin_market_read_failed"), "plugins": []}

    async def stage_market(self, *, plugin_id: str, digest: str) -> dict[str, Any]:
        if self.market is None or self.artifact_store is None:
            return _failure("unavailable", "plugin_market_not_configured")
        async with self._operation_lock:
            task = asyncio.create_task(asyncio.to_thread(
                self.market.stage, self.artifact_store, plugin_id=plugin_id, digest=digest,
            ))
            cancelled = False
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    cancelled = True
                except Exception:
                    break
            if cancelled:
                # A synchronous installer cannot be abandoned mid-probe. Wait for
                # its terminal state and discard any candidate before unlocking.
                if not task.cancelled() and task.exception() is None:
                    staged = task.result()
                    self.artifact_store.discard_stage(staged["stage_id"])
                raise asyncio.CancelledError
            try:
                return dict(task.result())
            except PluginInstallationError as exc:
                return _failure(exc.status, exc.reason, schema_errors=exc.schema_errors, diagnostics=exc.diagnostics)
            except Exception:
                return _failure("error", "plugin_market_stage_failed")

    async def _run_cancellable_artifact_operation(
        self,
        operation: Any,
        *,
        failure_reason: str,
        discard_staged_result: bool = False,
    ) -> dict[str, Any]:
        """Drain a synchronous installer before releasing the management lock."""

        task = asyncio.create_task(asyncio.to_thread(operation))
        cancelled = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                cancelled = True
            except Exception:
                break
        if cancelled:
            if not task.cancelled() and task.exception() is None:
                result = task.result()
                if discard_staged_result and isinstance(result, Mapping):
                    stage_id = str(result.get("stage_id") or "").strip()
                    if stage_id:
                        self.artifact_store.discard_stage(stage_id)
            raise asyncio.CancelledError
        try:
            return dict(task.result())
        except PluginInstallationError as exc:
            return _failure(exc.status, exc.reason, schema_errors=exc.schema_errors, diagnostics=exc.diagnostics)
        except Exception:
            return _failure("error", failure_reason)

    async def stage_wheel(self, *, wheel_path: str) -> dict[str, Any]:
        if self.artifact_store is None:
            return _failure("unavailable", "plugin_artifact_store_unavailable")
        normalized = str(wheel_path or "").strip()
        if not normalized:
            return _failure("invalid_request", "plugin_wheel_path_required")
        async with self._operation_lock:
            return await self._run_cancellable_artifact_operation(
                lambda: self.artifact_store.stage_wheel(Path(normalized)),
                failure_reason="plugin_stage_failed",
                discard_staged_result=True,
            )

    async def stage_source(self, *, source_path: str) -> dict[str, Any]:
        if self.artifact_store is None:
            return _failure("unavailable", "plugin_artifact_store_unavailable")
        normalized = str(source_path or "").strip()
        if not normalized:
            return _failure("invalid_request", "plugin_source_path_required")
        async with self._operation_lock:
            return await self._run_cancellable_artifact_operation(
                lambda: self.artifact_store.stage_source(Path(normalized)),
                failure_reason="plugin_source_stage_failed",
                discard_staged_result=True,
            )

    async def test_source(self, *, source_path: str) -> dict[str, Any]:
        if self.artifact_store is None:
            return _failure("unavailable", "plugin_artifact_store_unavailable")
        normalized = str(source_path or "").strip()
        if not normalized:
            return _failure("invalid_request", "plugin_source_path_required")
        async with self._operation_lock:
            return await self._run_cancellable_artifact_operation(
                lambda: self.artifact_store.test_source(Path(normalized)),
                failure_reason="plugin_source_test_failed",
            )

    async def discard_stage(self, *, stage_id: str) -> dict[str, Any]:
        if self.artifact_store is None:
            return _failure("unavailable", "plugin_artifact_store_unavailable")
        async with self._operation_lock:
            try:
                return dict(await asyncio.to_thread(self.artifact_store.discard_stage, stage_id))
            except PluginInstallationError as exc:
                return _failure(exc.status, exc.reason, schema_errors=exc.schema_errors, diagnostics=exc.diagnostics)
            except Exception:
                return _failure("error", "plugin_stage_discard_failed")

    async def rollback(self, *, plugin_id: str) -> dict[str, Any]:
        if self.artifact_store is None:
            return _failure("unavailable", "plugin_artifact_store_unavailable", plugin_id=plugin_id)
        async with self._operation_lock:
            try:
                result = dict(
                    await asyncio.to_thread(self.artifact_store.rollback_to_last_good, plugin_id)
                )
                if result.get("ok"):
                    result["reload_required"] = bool(result.pop("activation_pending", False))
                    result["reload_scope"] = "plugin_generation"
                return result
            except PluginInstallationError as exc:
                return _failure(exc.status, exc.reason, plugin_id=plugin_id, schema_errors=exc.schema_errors, diagnostics=exc.diagnostics)
            except Exception:
                return _failure("error", "plugin_rollback_failed", plugin_id=plugin_id)

    async def uninstall(self, *, plugin_id: str) -> dict[str, Any]:
        normalized_id = str(plugin_id or "").strip()
        if not is_valid_plugin_id(normalized_id):
            return _failure("invalid_request", "invalid_plugin_id", plugin_id=normalized_id)
        if self.artifact_store is None:
            return _failure("unavailable", "plugin_artifact_store_unavailable", plugin_id=normalized_id)
        try:
            artifact_snapshot = self.artifact_store.snapshot()
        except PluginInstallationError as exc:
            return _failure(exc.status, exc.reason, plugin_id=normalized_id)
        except Exception:
            return _failure(
                "unavailable",
                "plugin_artifact_catalog_unavailable",
                plugin_id=normalized_id,
            )
        installed_ids = {
            str(item.get("plugin_id") or "")
            for item in artifact_snapshot.get("plugins", ())
            if isinstance(item, Mapping)
        }
        if normalized_id not in installed_ids:
            return _failure("not_found", "plugin_not_installed", plugin_id=normalized_id)
        async with self._operation_lock:
            current = self.plugin_runtime.selections
            if normalized_id in {item.plugin_id for item in current}:
                candidate = tuple(
                    PluginSelection(item.plugin_id, False if item.plugin_id == normalized_id else item.enabled)
                    for item in current
                )
                status = dict(await self.plugin_runtime.reconfigure(candidate))
                target = _plugin_status(status, normalized_id)
                if status.get("published") is False or target.get("status") != "disabled":
                    rollback_status = "not_required"
                    if status.get("published") is not False:
                        rollback = dict(await self.plugin_runtime.reconfigure(current))
                        rollback_status = str(rollback.get("status") or "unknown")
                    return {
                        "ok": False,
                        "status": "deactivation_failed",
                        "reason": str(target.get("reason") or "plugin_disable_failed"),
                        "plugin_id": normalized_id,
                        "rollback_status": rollback_status,
                    }
            persisted = self.selection_store.load()
            defaults = {item.plugin_id for item in self.selection_store.defaults}
            next_selections = tuple(
                PluginSelection(item.plugin_id, False)
                if item.plugin_id == normalized_id and normalized_id in defaults
                else item
                for item in persisted
                if item.plugin_id != normalized_id or normalized_id in defaults
            )
            try:
                self.selection_store.save(next_selections)
            except Exception:
                if current:
                    await self.plugin_runtime.reconfigure(current)
                return _failure(
                    "persist_failed",
                    "plugin_selection_persist_failed",
                    plugin_id=normalized_id,
                )
            try:
                result = dict(
                    await asyncio.to_thread(self.artifact_store.remove_plugin, normalized_id,
                                            **self._artifact_retention())
                )
            except PluginInstallationError as exc:
                try:
                    self.selection_store.save(persisted)
                    await self.plugin_runtime.reconfigure(current)
                except Exception:
                    return _failure(
                        "rollback_failed",
                        "plugin_uninstall_rollback_failed",
                        plugin_id=normalized_id,
                    )
                return _failure(exc.status, exc.reason, plugin_id=normalized_id)
            except Exception:
                try:
                    self.selection_store.save(persisted)
                    await self.plugin_runtime.reconfigure(current)
                except Exception:
                    return _failure(
                        "rollback_failed",
                        "plugin_uninstall_rollback_failed",
                        plugin_id=normalized_id,
                    )
                return _failure("error", "plugin_uninstall_failed", plugin_id=normalized_id)
            runtime_status = dict(await self.plugin_runtime.reconfigure(next_selections))
            if runtime_status.get("published") is False or runtime_status.get("status") == "degraded":
                result.update(
                    {
                        "ok": False,
                        "status": "removed_runtime_degraded",
                        "reason": "plugin_runtime_reconfigure_failed",
                        "reload_required": True,
                        "reload_scope": "plugin_generation",
                    }
                )
            else:
                result["reload_required"] = False
                result["reload_scope"] = "plugin_generation"
            return result

    def _artifact_retention(self):
        retained = getattr(self.plugin_runtime, "retained_site_dirs", None)
        return {"retained_site_dirs": retained()} if callable(retained) else {}

    def reconcile_runtime(self, plugin_status: Mapping[str, Any]) -> dict[str, Any]:
        if self.artifact_store is None:
            return {"status": "not_configured", "reload_required": False}
        try:
            result = dict(
                self.artifact_store.reconcile_runtime(plugin_status.get("plugins", ()), **self._artifact_retention())
            )
            if (
                result.get("reload_required")
                and plugin_status.get("published") is False
                and str(plugin_status.get("active_status") or "") == "active"
            ):
                # The failed candidate never replaced the old process set. The
                # catalog now points back to that last-good artifact, so the
                # still-active snapshot is already the correct runtime proof.
                settled = dict(
                    self.artifact_store.reconcile_runtime(
                        self.plugin_runtime.status_snapshot().get("plugins", ()), **self._artifact_retention()
                    )
                )
                result["active_generation_preserved"] = True
                result["reload_required"] = bool(settled.get("reload_required"))
                result["settled_status"] = str(settled.get("status") or "")
            return result
        except PluginInstallationError as exc:
            return {"status": exc.status, "reason": exc.reason, "reload_required": False}
        except Exception:
            return {"status": "error", "reason": "plugin_artifact_reconcile_failed", "reload_required": False}

    async def invoke_capability(
        self,
        capability_id: str,
        args: Mapping[str, Any],
        *,
        context: InvocationContext,
    ) -> CapabilityResult:
        """Invoke diagnostics through the same runtime used by lifecycle work."""

        return await self.plugin_runtime.invoke(
            capability_id,
            args,
            context=context,
        )

    def execute_sync(
        self,
        *,
        action: str,
        plugin_id: str = "",
        path: str = "",
        stage_id: str = "",
        digest: str = "",
        approved_permissions: Iterable[str] = (),
    ) -> dict[str, Any]:
        normalized_action = str(action or "").strip().lower()
        if normalized_action == "list":
            payload = self.snapshot()
            payload["action"] = "list"
            return payload
        if normalized_action == "market":
            coroutine = self.browse_market()
        elif normalized_action == "stage_market":
            coroutine = self.stage_market(plugin_id=plugin_id, digest=digest)
        elif normalized_action == "test_source":
            coroutine = self.test_source(source_path=path)
        elif normalized_action == "stage_source":
            coroutine = self.stage_source(source_path=path)
        elif normalized_action == "stage_wheel":
            coroutine = self.stage_wheel(wheel_path=path)
        elif normalized_action == "install":
            coroutine = self.install_stage(
                stage_id=stage_id,
                approved_permissions=tuple(approved_permissions),
            )
        elif normalized_action == "discard_stage":
            coroutine = self.discard_stage(stage_id=stage_id)
        elif normalized_action == "enable":
            coroutine = self.set_enabled(plugin_id=plugin_id, enabled=True)
        elif normalized_action == "disable":
            coroutine = self.set_enabled(plugin_id=plugin_id, enabled=False)
        elif normalized_action == "rollback":
            coroutine = self.rollback(plugin_id=plugin_id)
        elif normalized_action == "uninstall":
            coroutine = self.uninstall(plugin_id=plugin_id)
        else:
            return _failure("invalid_request", "extension_action_invalid", plugin_id=plugin_id)
        runtime_loop = self.plugin_runtime.runtime_loop
        if runtime_loop is None or not runtime_loop.is_running():
            coroutine.close()
            return _failure("unavailable", "plugin_runtime_loop_unavailable", plugin_id=plugin_id)
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None
        if current_loop is runtime_loop:
            coroutine.close()
            return _failure("unavailable", "plugin_management_requires_worker_thread", plugin_id=plugin_id)
        future = asyncio.run_coroutine_threadsafe(coroutine, runtime_loop)
        try:
            return dict(future.result(timeout=self.sync_timeout_seconds))
        except TimeoutError:
            future.cancel()
            return _failure("timeout", "plugin_management_timeout", plugin_id=plugin_id)
        except Exception:
            return _failure("error", "plugin_management_failed", plugin_id=plugin_id)


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
    merged.extend(PluginSelection(plugin_id, overrides[plugin_id]) for plugin_id in sorted(overrides) if plugin_id not in known)
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


__all__ = [
    "ExtensionManagementService",
    "PLUGIN_SELECTION_STATE_FILENAME",
    "PLUGIN_SELECTION_STATE_SCHEMA_VERSION",
    "PluginManagementRuntime",
    "PluginSelectionStore",
]
