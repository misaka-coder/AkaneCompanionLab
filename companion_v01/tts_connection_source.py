"""Thin projection of existing voice configuration into a scoped connection.

No synthesis, provider probing, credential store or persistent resource registry.
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from pathlib import Path

from .plugin_api import PluginConnectionResult
from .local_capability_config import (
    CONFIGURABLE_PROVIDER_BY_ID, build_provider_config_entry,
    get_voice_profile_runtime_config, load_capability_config,
)
from .runtime_settings import runtime_setting
from .tts_provider_selection import EDGE_TTS_PROVIDER_ID, GPT_SOVITS_PROVIDER_ID


def unavailable(reason):
    return PluginConnectionResult(False, "unavailable", reason)


def qq_voice_config_profile(config, context, settings=None):
    value = str(runtime_setting(settings, config, "qq_tts_profile_user_id", "QQ_TTS_PROFILE_USER_ID", "") or "").strip()
    if not value:
        value = str(getattr(config, "WEB_OWNER_PROFILE_USER_ID", "") or "master").strip()
    if value.lower() in {"conversation", "context", "current"}:
        value = str(getattr(context, "profile_user_id", "") or "master").strip()
    return value if value and re.fullmatch(r"[A-Za-z0-9_.-]+", value) else "master"


def resolve_tts_connection(engine, config, invocation):
    voice = invocation.arguments.get("voice", {})
    if not isinstance(voice, Mapping):
        return unavailable("tts_voice_request_invalid")
    provider = voice.get("provider", "")
    settings = getattr(engine, "settings", None)
    if provider == EDGE_TTS_PROVIDER_ID:
        options = {key: runtime_setting(settings, config, "tts_" + key, "TTS_" + key.upper(), default)
                   for key, default in (("voice", "zh-CN-XiaoxiaoNeural"), ("rate", "+6%"),
                                        ("volume", "+0%"), ("pitch", "+4Hz"))}
        return PluginConnectionResult(True, "configured", model=provider, options={"client": options})
    if provider != GPT_SOVITS_PROVIDER_ID:
        return unavailable("requested_provider_unknown")
    profile_id = voice.get("profile_id", "")
    preview = invocation.host_parameters.get("tts_preview")
    if preview is not None and (not isinstance(preview, dict) or preview.get("voice") != dict(voice)):
        return unavailable("tts_preview_request_mismatch")
    if not profile_id and preview is None:
        return unavailable("requested_voice_profile_missing")
    root = getattr(engine, "capability_config_base_dir", None)
    user = invocation.context.profile_user_id
    if str(invocation.context.client_mode).startswith("qq"):
        user = qq_voice_config_profile(config, invocation.context, settings)
    saved = load_capability_config(base_dir=root, profile_user_id=user)
    entry = build_provider_config_entry(CONFIGURABLE_PROVIDER_BY_ID[GPT_SOVITS_PROVIDER_ID],
        saved.get("providers", {}).get(GPT_SOVITS_PROVIDER_ID))
    if preview is not None:
        entry = {"status": "configured", "endpoint": preview.get("endpoint", "")}
    status = entry.get("status")
    if status not in {"configured", "ready"} or not entry.get("endpoint"):
        return unavailable({"missing_config": "requested_provider_missing_config",
            "disabled": "requested_provider_disabled", "invalid_config": "requested_provider_invalid_config",
            "unreachable": "requested_provider_unreachable"}.get(status, "requested_provider_not_ready"))
    profile = get_voice_profile_runtime_config(base_dir=root, profile_user_id=user, voice_profile_id=profile_id,
                                             config_snapshot=saved)
    if preview is not None:
        profile = dict(preview.get("profile") or {})
    if not profile:
        return unavailable("requested_voice_profile_missing")

    def project(value):
        if isinstance(value, dict):
            result = {}
            for key, item in value.items():
                if key == "refAudioPath" and item:
                    source = Path(item)
                    # Paths originate in the existing private configuration,
                    # never in plugin arguments. Only an opaque handle leaves.
                    handle = "voice-reference:" + hashlib.sha256(str(source).encode()).hexdigest()
                    invocation.issued_resources[handle] = {
                        "absolute_path": str(source), "handle": handle, "name": "reference" + source.suffix,
                    }
                    result["refAudioHandle"] = handle
                else:
                    result[key] = project(item)
            return result
        if isinstance(value, list):
            return [project(item) for item in value]
        return value

    options = {key: runtime_setting(settings, config, "gpt_sovits_" + key,
                                   "GPT_SOVITS_" + key.upper(), default)
               for key, default in (("text_lang", "zh"), ("media_type", "wav"), ("streaming_mode", False),
                    ("parallel_infer", None), ("split_bucket", None), ("batch_size", None),
                    ("speed_factor", None), ("fragment_interval", None), ("text_split_method", ""))}
    options["timeout_seconds"] = runtime_setting(settings, config, "gpt_sovits_tts_timeout_seconds",
                                                "GPT_SOVITS_TTS_TIMEOUT_SECONDS", 45.0)
    return PluginConnectionResult(True, "configured", base_url=entry["endpoint"], model=provider,
        options={"client": options, "profile": project(profile)})
