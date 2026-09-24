"""Child-process runtime for one managed PluginHost generation."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any, Mapping, TextIO

from capcore import CapabilityResult
from akane_plugin.events import Event

from .instance_profile import PluginSelection
from .plugin_api import AKANE_PLUGIN_ENTRYPOINT_GROUP
from .plugin_contribution_policy import TrustedStatefulPluginContributionPolicy
from .plugin_generation_artifacts import GenerationArtifactOutboxSink, artifact_handoff_scope
from .plugin_generation_callbacks import (
    GenerationCapabilityProvider,
    GenerationConnectionProvider,
    GenerationNotificationPort,
    GenerationResourceProvider,
    GenerationEventsProvider,
    GenerationTaskProvider,
)
from .plugin_resources import generation_request_id
from .plugin_generation_codec import (
    PluginGenerationCodecError,
    capability_descriptor_to_wire,
    capability_result_to_wire,
    invocation_context_from_wire,
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


def _background_service_scope_factory(service_scope_id: str, *, plugin_id: str, generation_id: str,
                                      events_provider: Any):
    """Open one callback-lane invocation per supervised background service."""

    from contextlib import asynccontextmanager
    from capcore import InvocationContext
    from .plugin_resources import ResourceInvocation, current_resource_invocation
    from .plugin_event_ports import ScopedPluginEventsPort

    def factory(_service_id: str):
        @asynccontextmanager
        async def scope():
            invocation = ResourceInvocation(
                plugin_id, InvocationContext(), generation_id=generation_id,
                can_invoke_capabilities=True, can_read_resources=True,
                can_emit_events=True, can_bind_events=True,
                can_observe_context=True, can_request_turn=True,
            )
            events_token = current_resource_invocation.set(invocation)
            request_token = generation_request_id.set(service_scope_id)
            try:
                yield ScopedPluginEventsPort(plugin_id, events_provider)
            finally:
                generation_request_id.reset(request_token)
                current_resource_invocation.reset(events_token)
                await invocation.aclose()

        return scope()

    return factory


async def _handle_request(
    host: PluginHost,
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
        if command == "activate":
            activated = await host.activate_prepared()
            failed = next((item for item in activated.get("plugins", ()) if item.get("enabled") and item.get("status") != "active"), {})
            emit_protocol_message(protocol_stream, {**base, "ok": activated.get("status") == "active",
                "status": activated.get("status"), "reason": failed.get("reason") or activated.get("reason", "")})
            return
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
        if command == "event.invoke":
            raw_event = request.get("event")
            if not isinstance(raw_event, dict) or not all(isinstance(raw_event.get(key), str) for key in ("event_id", "event_type", "source")):
                raise PluginGenerationCodecError("event_protocol_invalid")
            scope = raw_event.get("scope", "")
            version = raw_event.get("version", 0)
            occurred_at_ms = raw_event.get("occurred_at_ms", 0)
            received_at_ms = raw_event.get("received_at_ms", 0)
            if not isinstance(scope, str) or any(
                isinstance(value, bool) or not isinstance(value, int)
                for value in (version, occurred_at_ms, received_at_ms)
            ):
                raise PluginGenerationCodecError("event_protocol_invalid")
            event = Event(raw_event["event_id"], raw_event["event_type"], raw_event["source"], raw_event.get("data"),
                          scope=scope, version=version, occurred_at_ms=occurred_at_ms, received_at_ms=received_at_ms)
            context = invocation_context_from_wire(request.get("context"))
            token = generation_request_id.set(request_id)
            try:
                result = await host.invoke_event(request.get("subscription_id"), event, context=context,
                                                 scope_id=request.get("scope_id"), delivery_id=request.get("delivery_id"))
                emit_protocol_message(protocol_stream, {**base, "ok": True, "status": result.status,
                                                       "reason": result.reason, "result": capability_result_to_wire(result)})
            finally:
                generation_request_id.reset(token)
            return
        if command == "invoke":
            raw_args = request.get("args")
            if not isinstance(raw_args, Mapping) or any(
                not isinstance(key, str) for key in raw_args
            ):
                raise PluginGenerationCodecError("invocation_args_invalid")
            context = invocation_context_from_wire(request.get("context"))
            resource_token = generation_request_id.set(request_id)
            try:
                with artifact_handoff_scope() as pending_artifacts:
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
                    pending_artifacts.clear()
            finally:
                generation_request_id.reset(resource_token)
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
        if command in {"hook.dispatch", "qq_command.dispatch"}:
            emit_protocol_message(
                protocol_stream,
                {
                    **base,
                    "ok": True,
                    "status": "cancelled",
                    "reason": {
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
                    "hook_protocol_invalid"
                    if command == "hook.dispatch"
                    else (
                        "qq_command_protocol_invalid"
                        if command == "qq_command.dispatch"
                        else "invocation_protocol_invalid"
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
            # Managed plugins may verify prepared ML model weights at startup.
            # Stay below the parent's 45-second generation startup bound.
            activation_timeout_seconds=30.0,
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
        callback_emit = lambda payload: emit_protocol_message(protocol_stream, payload)
        host.bind_managed_artifact_sink(
            GenerationArtifactOutboxSink(work_dir / "outbox" / generation_id)
        )
        host.bind_resource_provider(GenerationResourceProvider(
            generation_id=generation_id,
            emit=callback_emit,
            pending=callback_responses,
        ))
        host.bind_capability_provider(GenerationCapabilityProvider(
            generation_id=generation_id,
            emit=callback_emit,
            pending=callback_responses,
        ))
        host.bind_connection_provider(GenerationConnectionProvider(
            generation_id=generation_id,
            emit=callback_emit,
            pending=callback_responses,
        ))
        events_provider = GenerationEventsProvider(
            generation_id=generation_id,
            emit=callback_emit,
            pending=callback_responses,
        )
        host.bind_events_provider(events_provider)
        tasks_provider = GenerationTaskProvider(
            generation_id=generation_id,
            emit=callback_emit,
            pending=callback_responses,
        )
        host.bind_tasks_provider(tasks_provider)
        service_scope_id = str(getattr(args, "service_scope_id", "") or "").strip()
        if service_scope_id:
            host.bind_background_service_invocation_factory(
                _background_service_scope_factory(
                    service_scope_id,
                    plugin_id=plugin_id,
                    generation_id=generation_id,
                    events_provider=events_provider,
                )
            )
        prepare_only = bool(getattr(args, "prepare_only", False))
        status = await host.prepare() if prepare_only else await host.start()
        plugin_status = next(
            (
                item
                for item in status.get("plugins", ())
                if isinstance(item, Mapping) and item.get("plugin_id") == plugin_id
            ),
            {},
        )
        ok = (
            plugin_status.get("status") == ("prepared" if prepare_only else "active")
            and status.get("status") == ("prepared" if prepare_only else "active")
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
                "status": ("prepared" if prepare_only else "active") if ok else "failed",
                "reason": ""
                if ok
                else str(
                    plugin_status.get("reason")
                    or status.get("reason")
                    or "plugin_probe_failed"
                ),
                "plugin_id": plugin_id,
                "plugin_version": str(plugin_status.get("plugin_version") or ""),
                "schema_errors": list(plugin_status.get("schema_errors") or ()),
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
            import traceback as _traceback
            print(f"plugin generation worker failed: {_traceback.format_exc()}", file=sys.stderr)
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--site")
    parser.add_argument("--plugin-id")
    parser.add_argument("--work-dir")
    parser.add_argument("--generation-id")
    parser.add_argument("--managed-artifact-timeout")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--service-scope-id")
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


__all__ = ["run_generation_worker", "main"]
