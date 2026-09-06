"""Private connection snapshots from existing instance settings, not a store.

No HTTP/business execution, plugin settings file or configurable credential
lookup. Connection names and permissions are explicit, identity is host-bound.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict
import json

from .plugin_api import IMAGE_CONNECTION_READ_PERMISSION, RVC_CONNECTION_READ_PERMISSION, PluginConnectionResult
from .plugin_resources import current_resource_invocation

CONNECTION_PERMISSIONS = {"image_generation": IMAGE_CONNECTION_READ_PERMISSION, "rvc": RVC_CONNECTION_READ_PERMISSION}


def permitted_connections(permissions):
    return tuple(name for name, permission in CONNECTION_PERMISSIONS.items() if permission in permissions)


def connection_result_to_wire(result):
    if not isinstance(result, PluginConnectionResult):
        raise ValueError("connection_result_invalid")
    raw = asdict(result)
    if (
        not isinstance(result.ok, bool)
        or not all(isinstance(raw[name], str) for name in ("status", "reason", "base_url", "model", "api_key"))
        or not isinstance(result.options, dict)
    ):
        raise ValueError("connection_result_invalid")
    if not result.ok and (result.base_url or result.model or result.api_key or result.options):
        raise ValueError("connection_failure_contains_configuration")
    if len(json.dumps(raw, ensure_ascii=False, allow_nan=False).encode("utf-8")) > 16 * 1024:
        raise ValueError("connection_result_too_large")
    return raw


def connection_result_from_wire(raw):
    if not isinstance(raw, dict) or set(raw) != {"ok", "status", "reason", "base_url", "model", "api_key", "options"}:
        raise ValueError("connection_result_invalid")
    result = PluginConnectionResult(**raw)
    connection_result_to_wire(result)
    return result


def rejected(reason):
    return PluginConnectionResult(False, "unavailable", reason)


class ScopedPluginConnectionPort:
    def __init__(self, plugin_id, provider):
        self._plugin_id, self._provider = plugin_id, provider

    async def resolve(self, name):
        scope = current_resource_invocation.get()
        if scope is None or not scope.active or scope.plugin_id != self._plugin_id:
            return rejected("connection_invocation_required")
        if not isinstance(name, str) or name not in scope.connection_names:
            return rejected("connection_permission_required")
        task = asyncio.current_task()
        scope.pending.add(task)
        try:
            result = await self._provider.resolve(name, invocation=scope)
            if not scope.active or task.cancelling():
                raise asyncio.CancelledError()
            return connection_result_from_wire(connection_result_to_wire(result))
        except asyncio.CancelledError:
            raise
        except Exception:
            return rejected("connection_resolution_failed")
        finally:
            scope.pending.discard(task)


class ModelServicePluginConnectionProvider:
    """Thin product mapping; captures the current settings object on each call."""

    def __init__(self, engine, config):
        self._engine, self._config = engine, config

    async def resolve(self, name, *, invocation):
        if not invocation.active or name not in invocation.connection_names:
            return rejected("connection_permission_required")
        if not invocation.context.profile_user_id or not invocation.context.session_id:
            return rejected("connection_context_required")
        if name == "rvc":
            return self._rvc()
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
        )
        connection_result_to_wire(result)
        return result
