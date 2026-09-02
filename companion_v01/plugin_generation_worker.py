"""Child-process runtime for one managed PluginHost generation."""

from __future__ import annotations

import asyncio
import json
import sys
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any, Mapping, TextIO

from capcore import CapabilityResult

from .instance_profile import PluginSelection
from .plugin_api import AKANE_PLUGIN_ENTRYPOINT_GROUP
from .plugin_contribution_policy import TrustedStatefulPluginContributionPolicy
from .plugin_generation_artifacts import GenerationArtifactOutboxSink
from .plugin_generation_callbacks import (
    GenerationNotificationPort,
    GenerationReasoningPort,
)
from .plugin_generation_codec import (
    PluginGenerationCodecError,
    capability_descriptor_to_wire,
    capability_result_to_wire,
    invocation_context_from_wire,
    plugin_event_dispatch_result_to_wire,
    plugin_event_envelope_from_wire,
    plugin_hook_dispatch_result_to_wire,
    plugin_hook_envelope_from_wire,
    plugin_qq_command_result_to_wire,
    qq_command_dispatch_from_wire,
)
from .plugin_generation_protocol import (
    PLUGIN_GENERATION_PROTOCOL,
    emit_protocol_message,
    response_base,
)
from .plugin_generation_skills import (
    PluginGenerationSkillError,
    export_generation_skills,
    generation_skill_export_dir,
)
from .plugin_host import PluginHost
from .plugin_storage import InstancePluginStorageService


def _entry_points(site_dir: Path, plugin_id: str) -> tuple[Any, ...]:
    entries: list[Any] = []
    for distribution in importlib_metadata.distributions(path=[str(site_dir)]):
        for entry_point in distribution.entry_points:
            if (
                entry_point.group == AKANE_PLUGIN_ENTRYPOINT_GROUP
                and entry_point.name == plugin_id
            ):
                entries.append(entry_point)
    return tuple(entries)


async def _handle_request(
    host: PluginHost,
    event_broker: Any,
    hook_broker: Any,
    qq_command_broker: Any,
    request: Mapping[str, Any],
    *,
    generation_id: str,
    protocol_stream: TextIO,
) -> None:
    request_id = str(request.get("request_id") or "")
    base = response_base(generation_id=generation_id, request_id=request_id)
    command = str(request.get("command") or "")
    try:
        if command == "health":
            snapshot = host.status_snapshot()
            emit_protocol_message(
                protocol_stream,
                {
                    **base,
                    "ok": snapshot.get("status") in {"active", "degraded"},
                    "status": str(snapshot.get("status") or "unknown"),
                    "reason": str(snapshot.get("reason") or ""),
                    "snapshot": snapshot,
                },
            )
            return
        if command == "event.dispatch":
            event = plugin_event_envelope_from_wire(request.get("event"))
            result = await event_broker.dispatch(event)
            emit_protocol_message(
                protocol_stream,
                {
                    **base,
                    "ok": True,
                    "status": str(result.status or ""),
                    "reason": "",
                    "result": plugin_event_dispatch_result_to_wire(result),
                },
            )
            return
        if command == "hook.dispatch":
            hook = plugin_hook_envelope_from_wire(request.get("hook"))
            result = await hook_broker.dispatch(hook)
            emit_protocol_message(
                protocol_stream,
                {
                    **base,
                    "ok": True,
                    "status": str(result.status or ""),
                    "reason": "",
                    "result": plugin_hook_dispatch_result_to_wire(result),
                },
            )
            return
        if command == "qq_command.dispatch":
            command_args = qq_command_dispatch_from_wire(request.get("command_args"))
            result = await qq_command_broker.dispatch(**command_args)
            emit_protocol_message(
                protocol_stream,
                {
                    **base,
                    "ok": True,
                    "status": "handled" if result.handled else "unhandled",
                    "reason": str(result.reason or ""),
                    "result": plugin_qq_command_result_to_wire(result),
                },
            )
            return
        if command == "invoke":
            raw_args = request.get("args")
            if not isinstance(raw_args, Mapping) or any(
                not isinstance(key, str) for key in raw_args
            ):
                raise PluginGenerationCodecError("invocation_args_invalid")
            context = invocation_context_from_wire(request.get("context"))
            result = await host.invoke(
                str(request.get("capability_id") or ""),
                dict(raw_args),
                context=context,
            )
            emit_protocol_message(
                protocol_stream,
                {
                    **base,
                    "ok": True,
                    "status": str(result.status or ""),
                    "reason": str(result.reason or ""),
                    "result": capability_result_to_wire(result),
                },
            )
            return
        emit_protocol_message(
            protocol_stream,
            {
                **base,
                "ok": False,
                "status": "failed",
                "reason": "command_invalid",
            },
        )
    except asyncio.CancelledError:
        if command in {"event.dispatch", "hook.dispatch", "qq_command.dispatch"}:
            emit_protocol_message(
                protocol_stream,
                {
                    **base,
                    "ok": True,
                    "status": "cancelled",
                    "reason": {
                        "event.dispatch": "plugin_event_dispatch_cancelled",
                        "hook.dispatch": "plugin_hook_dispatch_cancelled",
                        "qq_command.dispatch": "plugin_qq_command_dispatch_cancelled",
                    }[command],
                },
            )
            raise
        emit_protocol_message(
            protocol_stream,
            {
                **base,
                "ok": True,
                "status": "cancelled",
                "reason": "plugin_invoke_cancelled",
                "result": capability_result_to_wire(
                    CapabilityResult(
                        is_error=True,
                        status="cancelled",
                        reason="plugin_invoke_cancelled",
                    )
                ),
            },
        )
        raise
    except PluginGenerationCodecError:
        emit_protocol_message(
            protocol_stream,
            {
                **base,
                "ok": False,
                "status": "failed",
                "reason": (
                    "event_protocol_invalid"
                    if command == "event.dispatch"
                    else (
                        "hook_protocol_invalid"
                        if command == "hook.dispatch"
                        else (
                            "qq_command_protocol_invalid"
                            if command == "qq_command.dispatch"
                            else "invocation_protocol_invalid"
                        )
                    )
                ),
            },
        )
    except Exception:
        emit_protocol_message(
            protocol_stream,
            {
                **base,
                "ok": False,
                "status": "failed",
                "reason": "plugin_generation_exception",
            },
        )


async def _handle_stop(
    host: PluginHost,
    active_requests: tuple[asyncio.Task[None], ...],
    *,
    generation_id: str,
    request_id: str,
    protocol_stream: TextIO,
) -> None:
    await asyncio.sleep(0)
    try:
        if active_requests:
            await asyncio.gather(*active_requests, return_exceptions=True)
        snapshot = await host.stop()
    except asyncio.CancelledError:
        raise
    except Exception:
        emit_protocol_message(
            protocol_stream,
            {
                **response_base(
                    generation_id=generation_id,
                    request_id=request_id,
                ),
                "ok": False,
                "status": "failed",
                "reason": "plugin_generation_exception",
            },
        )
        return
    emit_protocol_message(
        protocol_stream,
        {
            **response_base(
                generation_id=generation_id,
                request_id=request_id,
            ),
            "ok": snapshot.get("status") == "stopped",
            "status": str(snapshot.get("status") or "unknown"),
            "reason": str(snapshot.get("reason") or ""),
            "snapshot": snapshot,
        },
    )


async def run_generation_worker(args: Any, protocol_stream: TextIO) -> int:
    site_dir = Path(args.site).resolve()
    work_dir = Path(args.work_dir).resolve()
    plugin_id = str(args.plugin_id or "").strip()
    generation_id = str(args.generation_id or "").strip()
    sys.path.insert(0, str(site_dir))
    host: PluginHost | None = None
    ready_sent = False
    callback_responses: dict[str, asyncio.Future[Mapping[str, Any]]] = {}
    try:
        entries = _entry_points(site_dir, plugin_id)
        host = PluginHost(
            (PluginSelection(plugin_id, True),),
            contribution_policy=TrustedStatefulPluginContributionPolicy(),
            entry_points_provider=lambda: entries,
            managed_artifact_timeout_seconds=float(args.managed_artifact_timeout),
        )
        storage_data_root = (
            Path(args.storage_data_root).resolve()
            if str(args.storage_data_root or "").strip()
            else work_dir / "storage"
        )
        storage_instance_id = str(args.storage_instance_id or "").strip() or (
            f"generation-{generation_id}"
        )
        host.bind_plugin_storage_service(
            InstancePluginStorageService(storage_data_root, storage_instance_id)
        )
        host.bind_notification_port(
            GenerationNotificationPort(
                generation_id=generation_id,
                emit=lambda payload: emit_protocol_message(protocol_stream, payload),
                pending=callback_responses,
            )
        )
        host.bind_reasoning_port(
            GenerationReasoningPort(
                generation_id=generation_id,
                emit=lambda payload: emit_protocol_message(protocol_stream, payload),
                pending=callback_responses,
            )
        )
        host.bind_managed_artifact_sink(
            GenerationArtifactOutboxSink(work_dir / "outbox" / generation_id)
        )
        status = await host.start()
        plugin_status = next(
            (
                item
                for item in status.get("plugins", ())
                if isinstance(item, Mapping) and item.get("plugin_id") == plugin_id
            ),
            {},
        )
        ok = (
            plugin_status.get("status") == "active"
            and status.get("status") == "active"
        )
        skill_mounts: list[dict[str, str]] = []
        if ok:
            try:
                skill_mounts = export_generation_skills(
                    host.skill_roots(),
                    export_dir=generation_skill_export_dir(work_dir, generation_id),
                    generation_id=generation_id,
                )
            except PluginGenerationSkillError:
                ok = False
                status = {**status, "reason": "plugin_skill_projection_failed"}
        emit_protocol_message(
            protocol_stream,
            {
                "protocol": PLUGIN_GENERATION_PROTOCOL,
                "type": "ready",
                "generation_id": generation_id,
                "ok": ok,
                "status": "active" if ok else "failed",
                "reason": ""
                if ok
                else str(
                    status.get("reason")
                    or plugin_status.get("reason")
                    or "plugin_probe_failed"
                ),
                "plugin_id": plugin_id,
                "plugin_version": str(plugin_status.get("plugin_version") or ""),
                "permissions": list(plugin_status.get("permissions") or ()),
                "contribution_snapshot": dict(
                    plugin_status.get("contribution_snapshot") or {}
                ),
                "capabilities": [
                    capability_descriptor_to_wire(descriptor)
                    for _capability_id, descriptor in sorted(
                        host.capability_descriptors.items()
                    )
                ],
                "stable_system_prompt_blocks": list(
                    host.stable_system_prompt_blocks()
                ),
                "skill_mounts": skill_mounts,
            },
        )
        ready_sent = True
        if not ok:
            await host.stop()
            return 1
        event_broker = host.build_event_broker()
        hook_broker = host.build_hook_broker()
        qq_command_broker = host.build_qq_command_broker()
        active_requests: dict[str, asyncio.Task[None]] = {}
        stop_task: asyncio.Task[None] | None = None
        while True:
            line = await asyncio.to_thread(sys.stdin.readline)
            if not line:
                if stop_task is None:
                    await host.stop()
                else:
                    await stop_task
                if active_requests:
                    await asyncio.gather(
                        *active_requests.values(),
                        return_exceptions=True,
                    )
                return 0
            try:
                request = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(request, Mapping):
                continue
            if (
                request.get("protocol") == PLUGIN_GENERATION_PROTOCOL
                and request.get("type") == "callback_response"
                and request.get("generation_id") == generation_id
            ):
                callback_id = str(request.get("callback_id") or "")
                callback_future = callback_responses.get(callback_id)
                if callback_future is not None and not callback_future.done():
                    callback_future.set_result(dict(request))
                continue
            request_id = str(request.get("request_id") or "")
            base = response_base(
                generation_id=generation_id,
                request_id=request_id,
            )
            if (
                request.get("protocol") != PLUGIN_GENERATION_PROTOCOL
                or request.get("type") != "request"
                or not request_id
            ):
                emit_protocol_message(
                    protocol_stream,
                    {
                        **base,
                        "ok": False,
                        "status": "failed",
                        "reason": "protocol_invalid",
                    },
                )
                continue
            command = str(request.get("command") or "")
            if command == "cancel":
                target_request_id = str(request.get("target_request_id") or "")
                target = active_requests.get(target_request_id)
                if target is not None and not target.done():
                    target.cancel()
                    cancel_status = "cancel_requested"
                else:
                    cancel_status = "not_inflight"
                emit_protocol_message(
                    protocol_stream,
                    {
                        **base,
                        "ok": True,
                        "status": cancel_status,
                        "reason": "",
                    },
                )
                continue
            if command == "stop":
                if stop_task is not None:
                    emit_protocol_message(
                        protocol_stream,
                        {
                            **base,
                            "ok": False,
                            "status": "failed",
                            "reason": "plugin_generation_stopping",
                        },
                    )
                    continue
                stop_task = asyncio.create_task(
                    _handle_stop(
                        host,
                        tuple(active_requests.values()),
                        generation_id=generation_id,
                        request_id=request_id,
                        protocol_stream=protocol_stream,
                    ),
                    name=f"plugin-generation-stop:{generation_id[:8]}",
                )
                continue
            if stop_task is not None:
                emit_protocol_message(
                    protocol_stream,
                    {
                        **base,
                        "ok": False,
                        "status": "failed",
                        "reason": "plugin_generation_stopping",
                    },
                )
                continue
            if request_id in active_requests:
                emit_protocol_message(
                    protocol_stream,
                    {
                        **base,
                        "ok": False,
                        "status": "failed",
                        "reason": "duplicate_request_id",
                    },
                )
                continue
            task = asyncio.create_task(
                _handle_request(
                    host,
                    event_broker,
                    hook_broker,
                    qq_command_broker,
                    request,
                    generation_id=generation_id,
                    protocol_stream=protocol_stream,
                ),
                name=f"plugin-generation-request:{request_id[:8]}",
            )
            active_requests[request_id] = task
            task.add_done_callback(
                lambda done, rid=request_id: active_requests.pop(rid, None)
            )
    except Exception:
        if not ready_sent:
            emit_protocol_message(
                protocol_stream,
                {
                    "protocol": PLUGIN_GENERATION_PROTOCOL,
                    "type": "ready",
                    "generation_id": generation_id,
                    "ok": False,
                    "status": "failed",
                    "reason": "plugin_generation_exception",
                },
            )
        if host is not None:
            try:
                await host.stop()
            except Exception:
                pass
        return 1
    finally:
        try:
            sys.path.remove(str(site_dir))
        except ValueError:
            pass


__all__ = ["run_generation_worker"]
