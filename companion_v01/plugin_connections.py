"""Private, invocation-bound snapshots of declared or legacy connections."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
import json
from akane_plugin.connections import connection_name_from_permission

from .plugin_api import PluginConnectionResult
from .plugin_resources import current_resource_invocation


def permitted_connections(permissions):
    return tuple(dict.fromkeys(name for permission in permissions
                               if (name := connection_name_from_permission(permission)) is not None))


def connection_result_to_wire(result):
    if not isinstance(result, PluginConnectionResult):
        raise ValueError("connection_result_invalid")
    raw = asdict(result)
    if (
        not isinstance(result.ok, bool)
        or not all(isinstance(raw[name], str) for name in ("status", "reason", "base_url", "model", "api_key"))
        or not isinstance(result.options, dict)
        or not isinstance(result.private_option_fields, tuple)
        or any(not isinstance(key, str) for key in result.private_option_fields)
    ):
        raise ValueError("connection_result_invalid")
    if not result.ok and (result.base_url or result.model or result.api_key or result.options):
        raise ValueError("connection_failure_contains_configuration")
    if len(json.dumps(raw, ensure_ascii=False, allow_nan=False).encode("utf-8")) > 16 * 1024:
        raise ValueError("connection_result_too_large")
    raw["private_option_fields"] = list(result.private_option_fields)
    return raw


def connection_result_from_wire(raw):
    required = {"ok", "status", "reason", "base_url", "model", "api_key", "options"}
    if not isinstance(raw, dict) or not required <= set(raw) or set(raw) - required - {"private_option_fields"}:
        raise ValueError("connection_result_invalid")
    fields = raw.get("private_option_fields", [])
    if not isinstance(fields, list):
        raise ValueError("connection_result_invalid")
    result = PluginConnectionResult(**{**json.loads(json.dumps(raw, allow_nan=False)),
                                       "private_option_fields": tuple(fields)})
    connection_result_to_wire(result)
    return result


def rejected(reason):
    return PluginConnectionResult(False, "unavailable", reason)


def connection_private_values(result):
    def leaves(value):
        if isinstance(value, str):
            if value:
                yield value
        elif isinstance(value, dict):
            yield from (key for key in value if key)
            for child in value.values():
                yield from leaves(child)
        elif isinstance(value, list):
            for child in value:
                yield from leaves(child)
        elif value is not None:
            yield json.dumps(value, allow_nan=False)
    values = {result.api_key} if result.api_key else set()
    for key in result.private_option_fields:
        values.update(leaves(result.options.get(key)))
    return values


async def resolve_connection_snapshot(provider, name, invocation):
    """Freeze the first resolution; return a detached copy to every consumer."""
    if invocation.connection_snapshot_lock is None:
        invocation.connection_snapshot_lock = asyncio.Lock()
    async with invocation.connection_snapshot_lock:
        if not invocation.active:
            return rejected("connection_invocation_expired")
        if name not in invocation.connection_snapshots:
            result = await provider.resolve(name, invocation=invocation)
            if not invocation.active:
                return rejected("connection_invocation_expired")
            wire = connection_result_to_wire(result)
            snapshot = connection_result_from_wire(wire)
            invocation.private_values.update(connection_private_values(snapshot))
            invocation.connection_snapshots[name] = wire
        return connection_result_from_wire(invocation.connection_snapshots[name])


class ScopedPluginConnectionPort:
    def __init__(self, plugin_id, provider, *, private_values=None):
        self._plugin_id, self._provider = plugin_id, provider
        self._private_values = private_values if private_values is not None else set()

    async def resolve(self, name):
        scope = current_resource_invocation.get()
        if scope is None or not scope.active or scope.plugin_id != self._plugin_id:
            return rejected("connection_invocation_required")
        if getattr(scope.context, "global_scope", False):
            return rejected("connection_context_required")
        if not isinstance(name, str) or name not in scope.connection_names:
            return rejected("connection_permission_required")
        task = asyncio.current_task()
        if task.cancelling():
            scope.revoke()
            raise asyncio.CancelledError()
        scope.pending.add(task)
        try:
            result = await resolve_connection_snapshot(self._provider, name, scope)
            if not scope.active or task.cancelling():
                raise asyncio.CancelledError()
            snapshot = connection_result_from_wire(connection_result_to_wire(result))
            if snapshot.ok:
                self._private_values.update(connection_private_values(snapshot))
                scope.private_values.update(self._private_values)
            return snapshot
        except asyncio.CancelledError:
            raise
        except Exception:
            return rejected("connection_resolution_failed")
        finally:
            scope.pending.discard(task)


class ModelServicePluginConnectionProvider:
    """Resolve declarations, with thin source adapters for existing Bot settings."""

    def __init__(self, engine, config, *, connection_settings=None):
        self._engine, self._config = engine, config
        self._connection_settings = connection_settings

    async def resolve(self, name, *, invocation):
        if not invocation.active or name not in invocation.connection_names:
            return rejected("connection_permission_required")
        if not invocation.context.profile_user_id or not invocation.context.session_id:
            return rejected("connection_context_required")
        spec = next((spec for spec in invocation.connection_specs if spec.name == name), None)
        if spec is not None:
            if self._connection_settings is None:
                return rejected("connection_settings_unavailable")
            return self._connection_settings.resolve(invocation.context.profile_user_id, invocation.plugin_id, spec)
        if name == "rvc":
            return self._rvc()
        if name == "tts":
            from .tts_connection_source import resolve_tts_connection

            return resolve_tts_connection(self._engine, self._config, invocation)
        if name != "image_generation":
            return rejected("connection_not_found")
        settings = self._engine.settings
        if not settings.image_generation_enabled:
            return rejected("image_provider_disabled")
        # Preserve the existing explicit image-service credential fallback;
        # never export unrelated chat/vision settings or all environment vars.
        key = settings.image_generation_api_key or settings.chat_api_key
        if not key or not settings.image_generation_base_url or not settings.image_generation_model:
            return rejected("image_provider_not_configured")
        options = {
            "timeout_seconds": getattr(self._config, "IMAGE_GENERATION_TIMEOUT_SECONDS", 300.0),
            "max_input_images": getattr(self._config, "IMAGE_GENERATION_MAX_INPUT_IMAGES", 5),
            "max_output_images": getattr(self._config, "IMAGE_GENERATION_MAX_OUTPUT_IMAGES", 4),
            "max_image_bytes": getattr(self._config, "IMAGE_GENERATION_MAX_IMAGE_BYTES", 8 * 1024 * 1024),
            "max_total_input_bytes": getattr(self._config, "IMAGE_GENERATION_MAX_TOTAL_INPUT_BYTES", 20 * 1024 * 1024),
            "max_output_bytes": getattr(self._config, "IMAGE_GENERATION_MAX_OUTPUT_BYTES", 25 * 1024 * 1024),
        }
        result = PluginConnectionResult(
            True,
            "configured",
            base_url=settings.image_generation_base_url,
            model=settings.image_generation_model,
            api_key=key,
            options=options,
        )
        connection_result_to_wire(result)
        return result

    def _rvc(self):
        # Product config only; no RVC probing, pipeline, resource, or filesystem
        # operations belong in this private settings projection.
        config = self._config
        if not getattr(config, "COVER_SONG_ENABLED", False):
            return rejected("rvc_provider_disabled")
        remote = str(getattr(config, "LOCAL_MEDIA_EXECUTOR_BASE_URL", "") or "").strip()
        result = PluginConnectionResult(
            True,
            "configured",
            base_url=remote or str(getattr(config, "RVC_WEBUI_BASE_URL", "http://127.0.0.1:7899") or ""),
            model=str(getattr(config, "RVC_DEFAULT_MODEL", "") or ""),
            options={
                "backend": "remote" if remote else "webui",
                "root_dir": "" if remote else str(getattr(config, "RVC_ROOT_DIR", "") or ""),
                "separation_model": getattr(config, "COVER_SONG_SEPARATION_MODEL", "HP5_only_main_vocal"),
                "timeout_seconds": getattr(
                    config, "LOCAL_MEDIA_EXECUTOR_TIMEOUT_SECONDS" if remote else "COVER_SONG_TIMEOUT_SECONDS", 1800.0
                ),
                "max_duration_seconds": getattr(config, "COVER_SONG_MAX_DURATION_SECONDS", 900.0),
                "max_input_bytes": getattr(config, "COVER_SONG_MAX_INPUT_BYTES", 256 * 1024 * 1024),
                "default_output_format": getattr(config, "COVER_SONG_DEFAULT_OUTPUT_FORMAT", "mp3"),
                "default_delivery": getattr(config, "COVER_SONG_DEFAULT_DELIVERY", "auto"),
            },
            private_option_fields=("root_dir",),
        )
        connection_result_to_wire(result)
        return result
