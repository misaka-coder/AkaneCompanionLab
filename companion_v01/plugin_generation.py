"""Versioned process boundary for one managed PluginHost generation.

The protocol exposes lifecycle control and public CapCore invocation values.
Runtime calls remain on the in-process PluginHost until every port used by a
plugin has an explicit projection. No host objects are pickled across this
boundary.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import queue
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, TextIO

from capcore import CapabilityDescriptor, CapabilityResult, InvocationContext

from .plugin_api import (
    PluginEventEnvelope,
    PluginHookEnvelope,
    PluginQQCommandResult,
    is_valid_plugin_id,
    is_valid_permission_id,
)
from .plugin_events import PluginEventDispatchResult
from .plugin_hooks import PluginHookDispatchResult
from .plugin_generation_artifacts import (
    consume_generation_artifact,
    discard_generation_artifacts,
    is_generation_artifact_reference,
)
from .plugin_generation_callbacks import GenerationHostCallbackRouter
from .plugin_generation_codec import (
    PluginGenerationCodecError,
    capability_descriptor_from_wire,
    capability_result_from_wire,
    invocation_context_to_wire,
    json_snapshot,
    plugin_event_dispatch_result_from_wire,
    plugin_event_envelope_to_wire,
    plugin_hook_dispatch_result_from_wire,
    plugin_hook_envelope_to_wire,
    plugin_qq_command_result_from_wire,
    qq_command_dispatch_to_wire,
)
from .plugin_generation_protocol import (
    PLUGIN_GENERATION_PROTOCOL,
    PLUGIN_GENERATION_START_TIMEOUT_SECONDS,
    PLUGIN_GENERATION_STOP_TIMEOUT_SECONDS,
)
from .plugin_generation_skills import (
    PluginGenerationSkillError,
    decode_generation_skill_roots,
    generation_skill_export_dir,
)
from .plugin_managed_artifacts import (
    ManagedArtifactError,
    ManagedArtifactSink,
    normalize_managed_artifact_reference,
)
from .plugin_qq_commands import COMMAND_FAILURE_REPLY, PluginQQCommandBroker
from .plugin_subprocess import drain
from .skill_runtime import ContributedSkillRoot


class PluginGenerationError(RuntimeError):
    """A public, path-free generation lifecycle failure."""

    def __init__(self, reason: str) -> None:
        self.reason = str(reason or "plugin_generation_failed")
        super().__init__(self.reason)


class PluginGenerationQQCommandBroker:
    """Compose host commands with one isolated plugin generation."""

    def __init__(
        self,
        generation: "PluginGenerationProcess",
        *,
        host_registrations: tuple = (),
    ) -> None:
        self._generation = generation
        self._host_broker = PluginQQCommandBroker(
            (),
            host_registrations=tuple(host_registrations or ()),
        )

    @property
    def registered_commands(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                (
                    *self._host_broker.registered_commands,
                    *self._generation.registered_qq_commands,
                )
            )
        )

    def handles(self, command: str) -> bool:
        return self._host_broker.handles(command) or self._generation.handles_qq_command(
            command
        )

    async def dispatch(self, **command_args: Any) -> PluginQQCommandResult:
        command = str(command_args.get("command") or "")
        if self._host_broker.handles(command):
            return await self._host_broker.dispatch(**command_args)
        if not self._generation.handles_qq_command(command):
            return PluginQQCommandResult(
                handled=False,
                reason="no_matching_command",
            )
        return await self._generation.dispatch_qq_command(**command_args)


class PluginGenerationProcess:
    """Own one exact child process and its line-delimited JSON control lane."""

    def __init__(
        self,
        *,
        project_root: Path,
        site_dir: Path,
        plugin_id: str,
        work_dir: Path,
        python_executable: str = sys.executable,
        start_timeout_seconds: float = PLUGIN_GENERATION_START_TIMEOUT_SECONDS,
        stop_timeout_seconds: float | None = PLUGIN_GENERATION_STOP_TIMEOUT_SECONDS,
        managed_artifact_timeout_seconds: float = 0.0,
        plugin_storage_data_root: Path | None = None,
        plugin_storage_instance_id: str = "",
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.site_dir = Path(site_dir).resolve()
        self.plugin_id = str(plugin_id or "").strip()
        self.work_dir = Path(work_dir).resolve()
        self.python_executable = str(python_executable or sys.executable)
        self.start_timeout_seconds = max(0.1, float(start_timeout_seconds))
        self.stop_timeout_seconds = (
            None
            if stop_timeout_seconds is None
            else max(0.1, float(stop_timeout_seconds))
        )
        self.managed_artifact_timeout_seconds = max(
            0.0,
            float(managed_artifact_timeout_seconds),
        )
        storage_instance_id = str(plugin_storage_instance_id or "").strip()
        if (plugin_storage_data_root is None) != (not storage_instance_id):
            raise ValueError("plugin_generation_storage_scope_incomplete")
        if storage_instance_id and not is_valid_plugin_id(storage_instance_id):
            raise ValueError("plugin_generation_storage_scope_invalid")
        self.plugin_storage_data_root = (
            Path(plugin_storage_data_root).resolve()
            if plugin_storage_data_root is not None
            else None
        )
        self.plugin_storage_instance_id = storage_instance_id
        self.generation_id = uuid.uuid4().hex
        self._artifact_outbox_dir = self.work_dir / "outbox" / self.generation_id
        self._skill_export_dir = generation_skill_export_dir(
            self.work_dir,
            self.generation_id,
        )
        self._process: subprocess.Popen[str] | None = None
        self._ready: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._pending: dict[str, queue.Queue[dict[str, Any] | None]] = {}
        self._pending_lock = threading.Lock()
        self._active_invocation_condition = threading.Condition()
        self._active_invocations = 0
        self._reader: threading.Thread | None = None
        self._write_lock = threading.Lock()
        self._stop_lock = threading.Lock()
        self._capability_descriptors: Mapping[str, CapabilityDescriptor] = MappingProxyType({})
        self._event_types: tuple[str, ...] = ()
        self._hook_types: tuple[str, ...] = ()
        self._qq_commands: tuple[str, ...] = ()
        self._background_service_ids: tuple[str, ...] = ()
        self._stable_prompt_blocks: tuple[str, ...] = ()
        self._skill_roots: tuple[ContributedSkillRoot, ...] = ()
        self._plugin_version = ""
        self._permissions: tuple[str, ...] = ()
        self._approved_permissions: tuple[str, ...] | None = None
        self._contribution_snapshot: Mapping[str, Any] = MappingProxyType({})
        self._managed_artifact_sink: ManagedArtifactSink | None = None
        self._callback_router = GenerationHostCallbackRouter(
            generation_id=self.generation_id,
            start_timeout_seconds=self.start_timeout_seconds,
            write_response=self._write_callback_response,
        )
        self._stopping = False
        self._closed = False

    @property
    def running(self) -> bool:
        process = self._process
        return bool(process is not None and process.poll() is None and not self._closed)

    @property
    def capability_descriptors(self) -> Mapping[str, CapabilityDescriptor]:
        """Return the immutable public Capability snapshot published at ready."""

        return self._capability_descriptors

    @property
    def registered_event_types(self) -> tuple[str, ...]:
        """Return the exact event subscriptions published by this generation."""

        return self._event_types

    @property
    def registered_hook_types(self) -> tuple[str, ...]:
        """Return the exact lifecycle Hook subscriptions published at ready."""

        return self._hook_types

    @property
    def registered_background_service_ids(self) -> tuple[str, ...]:
        """Return the exact supervised services published at ready."""

        return self._background_service_ids

    @property
    def registered_qq_commands(self) -> tuple[str, ...]:
        """Return the exact QQ command tokens published at ready."""

        return self._qq_commands

    def public_status_snapshot(self) -> dict[str, Any]:
        """Return path-free ready metadata without a health request."""

        return {
            "plugin_id": self.plugin_id,
            "enabled": True,
            "status": "active" if self.running else "unavailable",
            "reason": "" if self.running else "plugin_generation_unavailable",
            **({"plugin_version": self._plugin_version} if self._plugin_version else {}),
            **({"permissions": list(self._permissions)} if self._permissions else {}),
            "contribution_snapshot": json_snapshot(dict(self._contribution_snapshot)),
        }

    def stable_system_prompt_blocks(self) -> tuple[str, ...]:
        """Return the generation's immutable restart-only prompt snapshot."""

        return self._stable_prompt_blocks

    def skill_roots(self) -> tuple[ContributedSkillRoot, ...]:
        """Return verified Skill mounts while this generation is available."""

        return self._skill_roots if self.running else ()

    def handles_qq_command(self, command: str) -> bool:
        return str(command or "").strip().lower() in self._qq_commands

    def build_qq_command_broker(
        self,
        host_registrations: tuple = (),
    ) -> PluginGenerationQQCommandBroker:
        return PluginGenerationQQCommandBroker(
            self,
            host_registrations=host_registrations,
        )

    def observes(self, event_type: str) -> bool:
        normalized = str(event_type or "").strip().lower()
        return normalized in self._event_types or normalized in self._hook_types

    def bind_managed_artifact_sink(self, sink: ManagedArtifactSink) -> None:
        if self._process is not None:
            raise RuntimeError("plugin_generation_already_started")
        if not callable(getattr(sink, "materialize", None)):
            raise TypeError("invalid_managed_artifact_sink")
        self._managed_artifact_sink = sink

    def bind_notification_port(self, port: Any) -> None:
        if self._process is not None:
            raise RuntimeError("plugin_generation_already_started")
        self._callback_router.bind_notification_port(port)

    def bind_resource_provider(self, provider: Any) -> None:
        if self._process is not None:
            raise RuntimeError("plugin_generation_already_started")
        self._callback_router.bind_resource_provider(provider)

    def bind_capability_provider(self, provider: Any) -> None:
        if self._process is not None:
            raise RuntimeError("plugin_generation_already_started")
        self._callback_router.bind_capability_provider(provider)

    def bind_connection_provider(self, provider: Any) -> None:
        if self._process is not None:
            raise RuntimeError("plugin_generation_already_started")
        self._callback_router.bind_connection_provider(provider)

    def bind_approved_permissions(self, permissions: tuple[str, ...]) -> None:
        if self._process is not None:
            raise RuntimeError("plugin_generation_already_started")
        if not isinstance(permissions, tuple) or any(not is_valid_permission_id(p) for p in permissions):
            raise ValueError("plugin_approved_permissions_invalid")
        self._approved_permissions = tuple(sorted(set(permissions)))

    def bind_agent_event_port(self, port: Any) -> None:
        if self._process is not None:
            raise RuntimeError("plugin_generation_already_started")
        self._callback_router.bind_agent_event_port(port)

    def start(self) -> dict[str, Any]:
        if self._process is not None:
            raise PluginGenerationError("plugin_generation_already_started")
        self.work_dir.mkdir(parents=True, exist_ok=True)
        started_at = time.perf_counter()
        try:
            command = [
                self.python_executable,
                "-u",
                "-m",
                "companion_v01.plugin_generation",
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
            ]
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
        assert self._process.stdout is not None
        self._reader = threading.Thread(
            target=self._read_responses,
            args=(self._process.stdout,),
            name=f"plugin-generation-reader:{self.generation_id[:8]}",
            daemon=True,
        )
        self._reader.start()
        try:
            ready = self._next_response(self._ready, self.start_timeout_seconds)
            self._validate_response(ready, expected_type="ready")
            if not ready.get("ok"):
                raise PluginGenerationError(
                    str(ready.get("reason") or "plugin_generation_start_failed")
                )
            if str(ready.get("plugin_id") or "") != self.plugin_id:
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
            self._permissions = tuple(raw_permissions)
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

    async def dispatch(
        self,
        event: PluginEventEnvelope | PluginHookEnvelope,
    ) -> PluginEventDispatchResult | PluginHookDispatchResult:
        """Dispatch one public event or Hook through the active generation."""

        if isinstance(event, PluginHookEnvelope):
            return await self._dispatch_hook(event)

        try:
            wire_event = plugin_event_envelope_to_wire(event)
        except PluginGenerationCodecError as exc:
            return _generation_event_failure("invalid_event", exc.args[0])
        try:
            request_id, response_queue = self._send_request(
                "event.dispatch",
                {"event": wire_event},
            )
        except PluginGenerationError as exc:
            return _generation_event_failure("host_unavailable", exc.reason)
        try:
            try:
                response = await asyncio.to_thread(
                    self._next_response,
                    response_queue,
                    None,
                )
            except asyncio.CancelledError:
                self._discard_pending(request_id, response_queue)
                try:
                    response_queue.put_nowait(None)
                except queue.Full:
                    pass
                self._send_cancel(request_id)
                raise
            finally:
                self._discard_pending(request_id, response_queue)
            try:
                self._validate_response(response, expected_type="response")
            except PluginGenerationError as exc:
                return _generation_event_failure("host_unavailable", exc.reason)
            if response.get("request_id") != request_id:
                return _generation_event_failure(
                    "host_unavailable",
                    "plugin_generation_protocol_invalid",
                )
            if not response.get("ok"):
                return _generation_event_failure(
                    "host_unavailable",
                    str(response.get("reason") or "plugin_event_dispatch_failed"),
                )
            try:
                return plugin_event_dispatch_result_from_wire(response.get("result"))
            except PluginGenerationCodecError:
                return _generation_event_failure(
                    "host_unavailable",
                    "plugin_generation_protocol_invalid",
                )
        except PluginGenerationError as exc:
            return _generation_event_failure("host_unavailable", exc.reason)
        finally:
            self._finish_invocation()

    async def _dispatch_hook(self, hook: PluginHookEnvelope) -> PluginHookDispatchResult:
        try:
            wire_hook = plugin_hook_envelope_to_wire(hook)
        except PluginGenerationCodecError as exc:
            return _generation_hook_failure("invalid_hook", exc.args[0])
        try:
            request_id, response_queue = self._send_request(
                "hook.dispatch",
                {"hook": wire_hook},
            )
        except PluginGenerationError as exc:
            return _generation_hook_failure("host_unavailable", exc.reason)
        try:
            try:
                response = await asyncio.to_thread(
                    self._next_response,
                    response_queue,
                    None,
                )
            except asyncio.CancelledError:
                self._discard_pending(request_id, response_queue)
                try:
                    response_queue.put_nowait(None)
                except queue.Full:
                    pass
                self._send_cancel(request_id)
                raise
            finally:
                self._discard_pending(request_id, response_queue)
            try:
                self._validate_response(response, expected_type="response")
            except PluginGenerationError as exc:
                return _generation_hook_failure("host_unavailable", exc.reason)
            if response.get("request_id") != request_id:
                return _generation_hook_failure(
                    "host_unavailable",
                    "plugin_generation_protocol_invalid",
                )
            if not response.get("ok"):
                return _generation_hook_failure(
                    "host_unavailable",
                    str(response.get("reason") or "plugin_hook_dispatch_failed"),
                )
            try:
                return plugin_hook_dispatch_result_from_wire(response.get("result"))
            except PluginGenerationCodecError:
                return _generation_hook_failure(
                    "host_unavailable",
                    "plugin_generation_protocol_invalid",
                )
        except PluginGenerationError as exc:
            return _generation_hook_failure("host_unavailable", exc.reason)
        finally:
            self._finish_invocation()

    def dispatch_from_consumer(self, hook: PluginHookEnvelope) -> PluginHookDispatchResult:
        """Synchronous Hook boundary used by Engine and outbound workers."""

        if not isinstance(hook, PluginHookEnvelope):
            return _generation_hook_failure("invalid_hook", "invalid_hook")
        try:
            wire_hook = plugin_hook_envelope_to_wire(hook)
            request_id, response_queue = self._send_request(
                "hook.dispatch",
                {"hook": wire_hook},
            )
        except PluginGenerationCodecError as exc:
            return _generation_hook_failure("invalid_hook", exc.args[0])
        except PluginGenerationError as exc:
            return _generation_hook_failure("host_unavailable", exc.reason)
        try:
            try:
                response = self._next_response(response_queue, None)
            finally:
                self._discard_pending(request_id, response_queue)
            self._validate_response(response, expected_type="response")
            if response.get("request_id") != request_id or not response.get("ok"):
                return _generation_hook_failure(
                    "host_unavailable",
                    str(response.get("reason") or "plugin_generation_protocol_invalid"),
                )
            try:
                return plugin_hook_dispatch_result_from_wire(response.get("result"))
            except PluginGenerationCodecError:
                return _generation_hook_failure(
                    "host_unavailable",
                    "plugin_generation_protocol_invalid",
                )
        except PluginGenerationError as exc:
            return _generation_hook_failure("host_unavailable", exc.reason)
        finally:
            self._finish_invocation()

    async def dispatch_qq_command(
        self,
        *,
        command: str,
        args: str,
        qq_number: int,
        group_id: int,
        is_group: bool,
        idempotency_key: str = "",
        sender_role: str = "",
        profile_user_id: str = "",
        session_id: str = "",
        character_pack_id: str = "",
        conversation_ref: str = "",
    ) -> PluginQQCommandResult:
        try:
            wire_args = qq_command_dispatch_to_wire(
                command=command,
                args=args,
                qq_number=qq_number,
                group_id=group_id,
                is_group=is_group,
                idempotency_key=idempotency_key,
                sender_role=sender_role,
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=character_pack_id,
                conversation_ref=conversation_ref,
            )
        except PluginGenerationCodecError:
            return _generation_qq_command_failure("invalid_command_context")
        try:
            request_id, response_queue = self._send_request(
                "qq_command.dispatch",
                {"command_args": wire_args},
            )
        except PluginGenerationError as exc:
            return _generation_qq_command_failure(exc.reason)
        try:
            try:
                response = await asyncio.to_thread(
                    self._next_response,
                    response_queue,
                    None,
                )
            except asyncio.CancelledError:
                self._discard_pending(request_id, response_queue)
                try:
                    response_queue.put_nowait(None)
                except queue.Full:
                    pass
                self._send_cancel(request_id)
                raise
            finally:
                self._discard_pending(request_id, response_queue)
            self._validate_response(response, expected_type="response")
            if response.get("request_id") != request_id or not response.get("ok"):
                return _generation_qq_command_failure(
                    str(response.get("reason") or "plugin_generation_protocol_invalid")
                )
            try:
                return plugin_qq_command_result_from_wire(response.get("result"))
            except PluginGenerationCodecError:
                return _generation_qq_command_failure(
                    "plugin_generation_protocol_invalid"
                )
        except PluginGenerationError as exc:
            return _generation_qq_command_failure(exc.reason)
        finally:
            self._finish_invocation()

    async def invoke(
        self,
        capability_id: str,
        args: Mapping[str, Any],
        *,
        context: InvocationContext,
    ) -> CapabilityResult:
        """Invoke one public Capability without imposing a generation timeout."""

        try:
            wire_args = json_snapshot(dict(args))
            wire_context = invocation_context_to_wire(context)
        except (TypeError, ValueError, PluginGenerationCodecError) as exc:
            raise PluginGenerationError("plugin_generation_invocation_invalid") from exc
        request_id = uuid.uuid4().hex
        self._callback_router.begin_invocation(
            request_id, plugin_id=self.plugin_id, context=context, capability_id=capability_id,
            # Process-installed legacy plugins have no installation approval
            # receipt. They cannot obtain newly privileged connection access.
            permissions=(self._permissions if self._approved_permissions is not None else
                         tuple(p for p in self._permissions if not p.startswith("connection."))),
        )
        try:
            request_id, response_queue = self._send_request(
                "invoke",
                {
                    "capability_id": str(capability_id or ""),
                    "args": wire_args,
                    "context": wire_context,
                },
                request_id=request_id,
            )
        except BaseException:
            await self._callback_router.finish_invocation(request_id)
            raise
        response_task = asyncio.create_task(asyncio.to_thread(self._next_response, response_queue, None))
        cancellation_observed = False
        try:
            try:
                response = await asyncio.shield(response_task)
            except asyncio.CancelledError:
                cancellation_observed = True
                self._send_cancel(request_id)
                # Wait for the worker's actual terminal response before
                # deleting input copies that a converter may still have open.
                while not response_task.done():
                    try:
                        await asyncio.shield(response_task)
                    except asyncio.CancelledError:
                        continue
                    except Exception:
                        break
                # The worker may suppress cancellation and return an actual
                # success, or an error such as unconfirmed remote completion.
                # A cancellation request is not proof that either became a
                # cancelled operation. Validate its real terminal below.
                response = response_task.result()
            finally:
                self._discard_pending(request_id, response_queue)
            self._validate_response(response, expected_type="response")
            if response.get("request_id") != request_id:
                raise PluginGenerationError("plugin_generation_protocol_invalid")
            if not response.get("ok"):
                raise PluginGenerationError(
                    str(response.get("reason") or "plugin_generation_invoke_failed")
                )
            try:
                result = capability_result_from_wire(response.get("result"))
            except PluginGenerationCodecError as exc:
                raise PluginGenerationError("plugin_generation_protocol_invalid") from exc
            if cancellation_observed and result.is_error and result.status == "cancelled":
                if isinstance(result.content, Mapping):
                    discard_generation_artifacts(self._artifact_outbox_dir, result.content.get("managed_artifacts"))
                raise asyncio.CancelledError()
            materialize = self._materialize_generation_artifact(
                result, capability_id=str(capability_id or ""), context=context,
            )
            if cancellation_observed:
                # Complete the confirmed outcome once, including its artifact
                # handoff, even if the caller repeats the cancellation signal.
                finalized, _ = await drain(asyncio.create_task(materialize))
                return finalized
            return await materialize
        finally:
            await self._callback_router.finish_invocation(request_id)
            self._finish_invocation()

    async def _materialize_generation_artifact(
        self,
        result: CapabilityResult,
        *,
        capability_id: str,
        context: InvocationContext,
    ) -> CapabilityResult:
        content = result.content
        if not isinstance(content, Mapping) or "managed_artifacts" not in content:
            return result
        references = content.get("managed_artifacts")
        if (
            not isinstance(references, list) or not references
            or not all(is_generation_artifact_reference(ref) for ref in references)
            or len({ref["generated_id"] for ref in references}) != len(references)
        ):
            discard_generation_artifacts(self._artifact_outbox_dir, references)
            return _generation_artifact_failure("managed_artifact_handoff_invalid")
        descriptor = self._capability_descriptors.get(capability_id)
        outputs = tuple(output for output in descriptor.outputs if output.delivery == "generated_file") if descriptor else ()
        if len(outputs) != 1:
            discard_generation_artifacts(self._artifact_outbox_dir, references)
            return _generation_artifact_failure("managed_artifact_not_declared")
        staged_artifacts = []
        materialized_refs = []
        try:
            for reference in references:
                staged_artifacts.append(consume_generation_artifact(
                    self._artifact_outbox_dir,
                    reference, capability_id=capability_id,
                ))
            if sum(item.data_path.stat().st_size for item in staged_artifacts) > outputs[0].max_bytes:
                return _generation_artifact_failure("managed_artifact_too_large")
            if any(item.draft.source_handles or item.draft.revision_of for item in staged_artifacts) and "resource.read" not in self._permissions:
                return _generation_artifact_failure("managed_artifact_lineage_resource_permission_required")
            if self._managed_artifact_sink is None:
                return _generation_artifact_failure("managed_artifact_sink_unavailable")
            for staged in staged_artifacts:
                materialized = await asyncio.wait_for(
                    self._managed_artifact_sink.materialize(
                        staged.draft, context=context, capability_id=capability_id,
                    ),
                    timeout=self.managed_artifact_timeout_seconds or None,
                )
                normalized = normalize_managed_artifact_reference(
                    materialized, draft=staged.draft, capability_id=capability_id,
                )
                if normalized is None or is_generation_artifact_reference(normalized):
                    raise ManagedArtifactError("managed_artifact_invalid_reference")
                materialized_refs.append(normalized)
            projected_content = dict(content)
            projected_content["managed_artifacts"] = materialized_refs
            return CapabilityResult(
                is_error=result.is_error, status=result.status, reason=result.reason,
                content=projected_content,
            )
        except asyncio.CancelledError:
            raise
        except (TimeoutError, asyncio.TimeoutError):
            return _generation_artifact_failure("managed_artifact_write_timeout", artifacts=materialized_refs)
        except ManagedArtifactError as exc:
            return _generation_artifact_failure(exc.reason, artifacts=materialized_refs)
        except Exception:
            return _generation_artifact_failure("managed_artifact_write_failed", artifacts=materialized_refs)
        finally:
            discard_generation_artifacts(self._artifact_outbox_dir, references)

    def stop(self) -> dict[str, Any]:
        with self._stop_lock:
            if self._closed:
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

    def _request(
        self,
        command: str,
        *,
        timeout_seconds: float | None,
    ) -> dict[str, Any]:
        request_id, response_queue = self._send_request(command)
        try:
            response = self._next_response(response_queue, timeout_seconds)
        finally:
            self._discard_pending(request_id, response_queue)
        self._validate_response(response, expected_type="response")
        if response.get("request_id") != request_id:
            raise PluginGenerationError("plugin_generation_protocol_invalid")
        return response

    def _send_request(
        self,
        command: str,
        extra: Mapping[str, Any] | None = None,
        *,
        request_id: str | None = None,
    ) -> tuple[str, queue.Queue[dict[str, Any] | None]]:
        response_queue: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)
        request_id = request_id or uuid.uuid4().hex
        with self._write_lock:
            process = self._process
            if (
                self._closed
                or (self._stopping and command != "cancel")
                or process is None
                or process.poll() is not None
            ):
                raise PluginGenerationError("plugin_generation_unavailable")
            if process.stdin is None:
                raise PluginGenerationError("plugin_generation_unavailable")
            if command == "stop":
                self._stopping = True
            payload = {
                "protocol": PLUGIN_GENERATION_PROTOCOL,
                "type": "request",
                "request_id": request_id,
                "command": command,
                **dict(extra or {}),
            }
            with self._pending_lock:
                self._pending[request_id] = response_queue
            try:
                process.stdin.write(
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
                )
                process.stdin.flush()
                if command in {
                    "invoke",
                    "event.dispatch",
                    "hook.dispatch",
                    "qq_command.dispatch",
                }:
                    with self._active_invocation_condition:
                        self._active_invocations += 1
            except (BrokenPipeError, OSError) as exc:
                self._discard_pending(request_id, response_queue)
                raise PluginGenerationError("plugin_generation_unavailable") from exc
        return request_id, response_queue

    def _finish_invocation(self) -> None:
        with self._active_invocation_condition:
            self._active_invocations -= 1
            self._active_invocation_condition.notify_all()

    def _wait_for_invocations(self, deadline: float | None) -> bool:
        with self._active_invocation_condition:
            while self._active_invocations:
                if deadline is None:
                    self._active_invocation_condition.wait()
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._active_invocation_condition.wait(timeout=remaining)
            return True

    def _next_response(
        self,
        response_queue: queue.Queue[dict[str, Any] | None],
        timeout_seconds: float | None,
    ) -> dict[str, Any]:
        try:
            if timeout_seconds is None:
                response = response_queue.get()
            else:
                response = response_queue.get(timeout=max(0.1, float(timeout_seconds)))
        except queue.Empty as exc:
            raise PluginGenerationError("plugin_generation_timeout") from exc
        if response is None:
            raise PluginGenerationError("plugin_generation_exited")
        return response

    def _read_responses(self, stream: TextIO) -> None:
        try:
            for line in stream:
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    self._fail_pending({"protocol": "", "type": "invalid"})
                    continue
                if not isinstance(payload, Mapping):
                    self._fail_pending({"protocol": "", "type": "invalid"})
                    continue
                response = dict(payload)
                if response.get("type") == "callback_request":
                    self._callback_router.dispatch(response)
                    continue
                if response.get("type") == "callback_cancel":
                    self._callback_router.cancel(response)
                    continue
                if response.get("type") == "ready":
                    self._ready.put(response)
                    continue
                request_id = str(response.get("request_id") or "")
                with self._pending_lock:
                    response_queue = self._pending.get(request_id)
                if response_queue is not None:
                    try:
                        response_queue.put_nowait(response)
                    except queue.Full:
                        pass
        finally:
            self._ready.put(None)
            self._fail_pending(None)

    def _discard_pending(
        self,
        request_id: str,
        response_queue: queue.Queue[dict[str, Any] | None],
    ) -> None:
        with self._pending_lock:
            if self._pending.get(request_id) is response_queue:
                self._pending.pop(request_id, None)

    def _fail_pending(self, response: dict[str, Any] | None) -> None:
        with self._pending_lock:
            pending = tuple(self._pending.values())
        for response_queue in pending:
            try:
                response_queue.put_nowait(response)
            except queue.Full:
                pass

    def _send_cancel(self, target_request_id: str) -> None:
        try:
            cancel_id, cancel_queue = self._send_request(
                "cancel",
                {"target_request_id": target_request_id},
            )
        except PluginGenerationError:
            return
        self._discard_pending(cancel_id, cancel_queue)

    def _validate_response(
        self,
        response: Mapping[str, Any],
        *,
        expected_type: str,
    ) -> None:
        if (
            response.get("protocol") != PLUGIN_GENERATION_PROTOCOL
            or response.get("type") != expected_type
            or response.get("generation_id") != self.generation_id
        ):
            raise PluginGenerationError("plugin_generation_protocol_invalid")

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
        if reader is not None and reader is not threading.current_thread():
            reader.join(timeout=1.0)
        self._fail_pending(None)
        self._callback_router.close()
        shutil.rmtree(self._artifact_outbox_dir, ignore_errors=True)
        shutil.rmtree(self._skill_export_dir, ignore_errors=True)

    def _write_callback_response(self, payload: Mapping[str, Any]) -> None:
        with self._write_lock:
            process = self._process
            if (
                self._closed
                or process is None
                or process.poll() is not None
                or process.stdin is None
            ):
                return
            try:
                process.stdin.write(
                    json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":"))
                    + "\n"
                )
                process.stdin.flush()
            except (BrokenPipeError, OSError):
                return


def _decode_capability_snapshot(value: object) -> Mapping[str, CapabilityDescriptor]:
    if not isinstance(value, list):
        raise PluginGenerationError("plugin_generation_protocol_invalid")
    descriptors: dict[str, CapabilityDescriptor] = {}
    try:
        for item in value:
            descriptor = capability_descriptor_from_wire(item)
            if descriptor.id in descriptors:
                raise PluginGenerationCodecError("duplicate_capability")
            descriptors[descriptor.id] = descriptor
    except PluginGenerationCodecError as exc:
        raise PluginGenerationError("plugin_generation_protocol_invalid") from exc
    return MappingProxyType(descriptors)


def _decode_event_types(value: object) -> tuple[str, ...]:
    if not isinstance(value, Mapping):
        raise PluginGenerationError("plugin_generation_protocol_invalid")
    raw_event_types = value.get("event_handlers", [])
    if not isinstance(raw_event_types, list) or any(
        not isinstance(item, str) for item in raw_event_types
    ):
        raise PluginGenerationError("plugin_generation_protocol_invalid")
    return tuple(sorted(set(raw_event_types)))


def _decode_hook_types(value: object) -> tuple[str, ...]:
    if not isinstance(value, Mapping):
        raise PluginGenerationError("plugin_generation_protocol_invalid")
    raw_hook_types = value.get("hooks", [])
    if not isinstance(raw_hook_types, list) or any(
        not isinstance(item, str) for item in raw_hook_types
    ):
        raise PluginGenerationError("plugin_generation_protocol_invalid")
    return tuple(sorted(set(raw_hook_types)))


def _decode_background_service_ids(value: object) -> tuple[str, ...]:
    if not isinstance(value, Mapping):
        raise PluginGenerationError("plugin_generation_protocol_invalid")
    raw_service_ids = value.get("background_services", [])
    if not isinstance(raw_service_ids, list) or any(
        not isinstance(item, str) for item in raw_service_ids
    ):
        raise PluginGenerationError("plugin_generation_protocol_invalid")
    return tuple(sorted(set(raw_service_ids)))


def _decode_qq_commands(value: object) -> tuple[str, ...]:
    if not isinstance(value, Mapping):
        raise PluginGenerationError("plugin_generation_protocol_invalid")
    raw_commands = value.get("commands", [])
    if not isinstance(raw_commands, list) or any(
        not isinstance(item, str) for item in raw_commands
    ):
        raise PluginGenerationError("plugin_generation_protocol_invalid")
    return tuple(sorted(set(raw_commands)))


def _decode_stable_prompt_blocks(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) for item in value
    ):
        raise PluginGenerationError("plugin_generation_protocol_invalid")
    return tuple(value)


def _generation_event_failure(status: str, reason: object) -> PluginEventDispatchResult:
    safe_reason = str(reason or "plugin_event_dispatch_failed")
    return PluginEventDispatchResult(
        ok=False,
        status=str(status or "host_unavailable"),
        failures=(("host", safe_reason),),
    )


def _generation_hook_failure(status: str, reason: object) -> PluginHookDispatchResult:
    safe_reason = str(reason or "plugin_hook_dispatch_failed")
    return PluginHookDispatchResult(
        ok=False,
        status=str(status or "host_unavailable"),
        failures=(("host", safe_reason),),
    )


def _generation_qq_command_failure(reason: object) -> PluginQQCommandResult:
    return PluginQQCommandResult(
        handled=True,
        reply_text=COMMAND_FAILURE_REPLY,
        reason=str(reason or "plugin_generation_unavailable"),
    )


def _generation_artifact_failure(reason: str, *, artifacts: list | tuple = ()) -> CapabilityResult:
    return CapabilityResult(
        is_error=True,
        status="partial" if artifacts else "error",
        reason=str(reason or "managed_artifact_write_failed"),
        content={"managed_artifacts": list(artifacts)} if artifacts else None,
    )


def main(argv: list[str] | None = None) -> int:
    from .plugin_generation_worker import run_generation_worker

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--site")
    parser.add_argument("--plugin-id")
    parser.add_argument("--work-dir")
    parser.add_argument("--generation-id")
    parser.add_argument("--managed-artifact-timeout")
    parser.add_argument("--storage-data-root")
    parser.add_argument("--storage-instance-id")
    args = parser.parse_args(argv)
    if not args.worker or not all(
        (
            args.site,
            args.plugin_id,
            args.work_dir,
            args.generation_id,
            args.managed_artifact_timeout,
        )
    ):
        return 2
    protocol_stream = sys.stdout
    # Plugin prints must not share the versioned control lane.
    sys.stdout = sys.stderr
    return asyncio.run(run_generation_worker(args, protocol_stream))


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "PLUGIN_GENERATION_PROTOCOL",
    "PLUGIN_GENERATION_START_TIMEOUT_SECONDS",
    "PLUGIN_GENERATION_STOP_TIMEOUT_SECONDS",
    "PluginGenerationError",
    "PluginGenerationProcess",
    "PluginGenerationQQCommandBroker",
]
