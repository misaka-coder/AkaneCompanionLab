"""Prepare, activate, drain and terminate the shared child process."""

from __future__ import annotations

import shutil
import subprocess
import threading
import time
from types import MappingProxyType
from typing import Any, Mapping
from akane_plugin.events import EventSubscription
from akane_plugin.connections import ConnectionSpec, validate_connection_specs
from ..plugin_events import validate_event_subscription
from ..plugin_generation_codec import json_snapshot
from ..plugin_generation_skills import PluginGenerationSkillError, decode_generation_skill_roots
from .contracts import PluginGenerationError, _decode_capability_snapshot, _decode_event_types, _decode_hook_types, _decode_background_service_ids, _decode_qq_commands, _decode_stable_prompt_blocks


class GenerationLifecycleMixin:
    """Prepare, activate, drain and terminate the shared child process."""

    def prepare(self) -> dict[str, Any]:
        return self.start(prepare_only=True)

    def activate(self) -> dict[str, Any]:
        if not self._prepared:
            return {"ok": self.running, **self.public_status_snapshot()}
        request_id, responses = self._send_request("activate", {})
        try:
            response = self._next_response(responses, self.start_timeout_seconds)
            self._validate_response(response, expected_type="response")
            if response.get("request_id") != request_id:
                raise PluginGenerationError("plugin_generation_protocol_invalid")
            if not response.get("ok"):
                raise PluginGenerationError(str(response.get("reason") or "plugin_generation_activation_failed"))
            if response.get("status") != "active":
                raise PluginGenerationError("plugin_generation_protocol_invalid")
            self._prepared = False
            return dict(response)
        finally:
            self._discard_pending(request_id, responses)

    def start(self, *, prepare_only: bool = False) -> dict[str, Any]:
        if self._process is not None:
            raise PluginGenerationError("plugin_generation_already_started")
        self._prepared = prepare_only
        self.work_dir.mkdir(parents=True, exist_ok=True)
        started_at = time.perf_counter()
        try:
            command = [
                self.python_executable,
                "-u",
                "-m",
                "companion_v01.plugin_generation_worker",
                "--worker",
                "--site",
                str(self.site_dir),
                "--plugin-id",
                self.plugin_id,
                "--work-dir",
                str(self.work_dir),
                "--generation-id",
                self.generation_id,
                "--managed-artifact-timeout",
                str(self.managed_artifact_timeout_seconds),
                "--service-scope-id",
                self.service_scope_id,
            ]
            if prepare_only:
                command.append("--prepare-only")
            if self.plugin_storage_data_root is not None:
                command.extend(
                    [
                        "--storage-data-root",
                        str(self.plugin_storage_data_root),
                        "--storage-instance-id",
                        self.plugin_storage_instance_id,
                    ]
                )
            self._process = subprocess.Popen(
                command,
                cwd=str(self.project_root),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                bufsize=1,
            )
        except OSError as exc:
            raise PluginGenerationError("plugin_generation_unavailable") from exc
        try:
            assert self._process.stdout is not None
            self._reader = threading.Thread(
                target=self._read_responses,
                args=(self._process.stdout,),
                name=f"plugin-generation-reader:{self.generation_id[:8]}",
                daemon=True,
            )
            self._reader.start()
        except Exception as exc:
            self._terminate()
            raise PluginGenerationError("plugin_generation_unavailable") from exc
        try:
            ready = self._next_response(self._ready, self.start_timeout_seconds)
            self._validate_response(ready, expected_type="ready")
            if not ready.get("ok"):
                raise PluginGenerationError(
                    str(ready.get("reason") or "plugin_generation_start_failed"),
                    schema_errors=ready.get("schema_errors") or (),
                )
            if str(ready.get("plugin_id") or "") != self.plugin_id:
                raise PluginGenerationError("plugin_generation_protocol_invalid")
            if ready.get("status") != ("prepared" if prepare_only else "active"):
                raise PluginGenerationError("plugin_generation_protocol_invalid")
            raw_permissions = ready.get("permissions")
            contribution_snapshot = ready.get("contribution_snapshot")
            if (
                not isinstance(raw_permissions, list)
                or any(not isinstance(item, str) for item in raw_permissions)
                or not isinstance(contribution_snapshot, Mapping)
            ):
                raise PluginGenerationError("plugin_generation_protocol_invalid")
            self._plugin_version = str(ready.get("plugin_version") or "")
            self._prepared = prepare_only
            self._permissions = tuple(raw_permissions)
            raw_connections = contribution_snapshot.get("connections", [])
            try:
                if not isinstance(raw_connections, list):
                    raise ValueError("connection_declarations_invalid")
                self._connection_specs = tuple(ConnectionSpec.from_dict(item) for item in raw_connections)
                validate_connection_specs(self._connection_specs, self._permissions)
            except (TypeError, ValueError):
                raise PluginGenerationError("plugin_connection_declarations_invalid") from None
            if self._approved_permissions is not None and tuple(sorted(set(self._permissions))) != self._approved_permissions:
                raise PluginGenerationError("plugin_permissions_changed_after_approval")
            self._contribution_snapshot = MappingProxyType(
                dict(json_snapshot(contribution_snapshot))
            )
            self._capability_descriptors = _decode_capability_snapshot(
                ready.get("capabilities")
            )
            self._event_types = _decode_event_types(
                ready.get("contribution_snapshot")
            )
            subscriptions = []
            raw_subscriptions = contribution_snapshot.get("event_subscriptions", [])
            if not isinstance(raw_subscriptions, list):
                raise PluginGenerationError("plugin_generation_protocol_invalid")
            for raw in raw_subscriptions:
                if not isinstance(raw, dict) or not isinstance(raw.get("sources", []), list):
                    raise PluginGenerationError("plugin_generation_protocol_invalid")
                try:
                    subscription = EventSubscription(**{**raw, "sources": tuple(raw.get("sources", ()))})
                except (TypeError, AttributeError):
                    raise PluginGenerationError("plugin_generation_protocol_invalid") from None
                if validate_event_subscription(subscription) or not subscription.subscription_id.startswith(self.plugin_id + "."):
                    raise PluginGenerationError("plugin_generation_protocol_invalid")
                subscriptions.append(subscription)
            if len({item.subscription_id for item in subscriptions}) != len(subscriptions):
                raise PluginGenerationError("plugin_generation_protocol_invalid")
            self._event_subscriptions = tuple(subscriptions)
            self._hook_types = _decode_hook_types(
                ready.get("contribution_snapshot")
            )
            self._qq_commands = _decode_qq_commands(
                ready.get("contribution_snapshot")
            )
            self._background_service_ids = _decode_background_service_ids(
                ready.get("contribution_snapshot")
            )
            self._stable_prompt_blocks = _decode_stable_prompt_blocks(
                ready.get("stable_system_prompt_blocks")
            )
            self._skill_roots = decode_generation_skill_roots(
                ready.get("skill_mounts"),
                export_dir=self._skill_export_dir,
                plugin_id=self.plugin_id,
                declared_names=(
                    contribution_snapshot.get("skills", ())
                    if isinstance(contribution_snapshot, Mapping)
                    else ()
                ),
            )
            ready["startup_ms"] = round((time.perf_counter() - started_at) * 1000.0, 3)
            return ready
        except PluginGenerationSkillError as exc:
            self._terminate()
            raise PluginGenerationError("plugin_generation_protocol_invalid") from exc
        except Exception:
            self._terminate()
            raise

    def health(self) -> dict[str, Any]:
        return self._request("health", timeout_seconds=self.start_timeout_seconds)

    def stop(self) -> dict[str, Any]:
        with self._stop_lock:
            if self._closed:
                self._callback_router.close()
                return {"ok": True, "status": "stopped", "reason": "already_stopped"}
            process = self._process
            if process is None:
                self._closed = True
                return {"ok": True, "status": "stopped", "reason": "not_started"}
            deadline = (
                None
                if self.stop_timeout_seconds is None
                else time.monotonic() + self.stop_timeout_seconds
            )
            try:
                if process.poll() is None:
                    response = self._request("stop", timeout_seconds=self.stop_timeout_seconds)
                    if response.get("ok") and not self._wait_for_invocations(deadline):
                        response = {
                            "ok": False,
                            "status": "failed",
                            "reason": "plugin_generation_timeout",
                        }
                else:
                    response = {
                        "ok": False,
                        "status": "failed",
                        "reason": "plugin_generation_exited",
                    }
            except PluginGenerationError as exc:
                response = {"ok": False, "status": "failed", "reason": exc.reason}
            finally:
                self._terminate()
            return response

    def _terminate(self) -> None:
        with self._write_lock:
            if self._closed:
                return
            self._closed = True
            process = self._process
            if process is None:
                return
            try:
                if process.stdin is not None:
                    process.stdin.close()
            except OSError:
                pass
        if process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=2.0)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    process.kill()
                    process.wait(timeout=2.0)
                except (OSError, subprocess.TimeoutExpired):
                    pass
        else:
            try:
                process.wait(timeout=0.1)
            except (OSError, subprocess.TimeoutExpired):
                pass
        try:
            if process.stdout is not None:
                process.stdout.close()
        except OSError:
            pass
        reader = self._reader
        if reader is not None and reader is not threading.current_thread() and reader.ident is not None:
            reader.join(timeout=1.0)
        self._fail_pending(None)
        self._callback_router.close()
        shutil.rmtree(self._artifact_outbox_dir, ignore_errors=True)
        shutil.rmtree(self._skill_export_dir, ignore_errors=True)
