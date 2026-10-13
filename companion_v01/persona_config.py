from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import tomllib

import config


DEFAULT_PERSONA_PROFILE_PATH = Path(__file__).with_name("persona_profiles.toml")


@dataclass(frozen=True)
class PersonaConfig:
    assistant_name: str
    user_label: str
    trace_prefix: str
    surprise_memory_reason: str
    final_fast_mode_prompt: str
    final_debug_mode_prompt: str
    final_fallback_thought: str
    final_fallback_speech: str
    router_system_prompt: str = ""
    router_fast_mode_prompt: str = ""
    router_debug_mode_prompt: str = ""
    verifier_system_prompt: str = ""
    verifier_fast_mode_prompt: str = ""
    verifier_debug_mode_prompt: str = ""
    summary_fallback_diary_template: str = ""
    summary_system_prompt: str = ""
    summary_user_prompt_template: str = ""
    semantic_summary_fallback_template: str = ""
    semantic_summary_system_prompt: str = ""
    semantic_summary_user_prompt_template: str = ""
    semantic_reinforcement_system_prompt: str = ""
    semantic_reinforcement_user_prompt_template: str = ""

    def build_summary_fallback_diary(self, tags: str) -> str:
        return self.summary_fallback_diary_template.format(tags=tags) if self.summary_fallback_diary_template else ""

    def build_semantic_fallback_summary(self, tags: str) -> str:
        return self.semantic_summary_fallback_template.format(tags=tags) if self.semantic_summary_fallback_template else ""


def _resolve_persona_profile_path(path: str | Path | None = None) -> Path:
    if path is not None:
        resolved = Path(path)
    else:
        configured = str(getattr(config, "PERSONA_CONFIG_PATH", "") or "").strip()
        if configured:
            resolved = Path(configured)
            if not resolved.is_absolute():
                resolved = Path(getattr(config, "BASE_DIR", Path.cwd())) / resolved
        else:
            resolved = DEFAULT_PERSONA_PROFILE_PATH
    return resolved.resolve()


def _load_toml_file(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        payload = tomllib.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"Persona profile at {path} is not a TOML table.")
    return payload


def _read_table(payload: dict[str, Any], key: str, *, context: str) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"Missing TOML table '{key}' in {context}.")
    return value


def _read_string(payload: dict[str, Any], key: str, *, context: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise ValueError(f"Missing string '{key}' in {context}.")
    return str(value).strip()


def _read_string_or_default(
    payload: dict[str, Any],
    key: str,
    *,
    context: str,
    default: str,
) -> str:
    value = payload.get(key)
    if isinstance(value, str):
        return str(value).strip()
    return str(default).strip()


def load_persona_config(
    *,
    path: str | Path | None = None,
    variant: str | None = None,
) -> PersonaConfig:
    resolved_path = _resolve_persona_profile_path(path)
    payload = _load_toml_file(resolved_path)
    variants = _read_table(payload, "variants", context=str(resolved_path))
    selected_variant = str(variant or getattr(config, "PERSONA_VARIANT", "default") or "default").strip() or "default"
    variant_payload = _read_table(variants, selected_variant, context=f"{resolved_path} variants")

    meta = _read_table(variant_payload, "meta", context=f"{resolved_path}:{selected_variant}")
    final = _read_table(variant_payload, "final", context=f"{resolved_path}:{selected_variant}")
    router = _read_table(variant_payload, "router", context=f"{resolved_path}:{selected_variant}") if "router" in variant_payload else {}
    verifier = _read_table(variant_payload, "verifier", context=f"{resolved_path}:{selected_variant}") if "verifier" in variant_payload else {}
    summary = _read_table(variant_payload, "summary", context=f"{resolved_path}:{selected_variant}") if "summary" in variant_payload else {}
    semantic_summary = _read_table(variant_payload, "semantic_summary", context=f"{resolved_path}:{selected_variant}") if "semantic_summary" in variant_payload else {}
    semantic_reinforcement = _read_table(variant_payload, "semantic_reinforcement", context=f"{resolved_path}:{selected_variant}") if "semantic_reinforcement" in variant_payload else {}

    return PersonaConfig(
        assistant_name=_read_string(meta, "assistant_name", context="meta"),
        user_label=_read_string(meta, "user_label", context="meta"),
        trace_prefix=_read_string(meta, "trace_prefix", context="meta"),
        surprise_memory_reason=_read_string(meta, "surprise_memory_reason", context="meta"),
        final_fast_mode_prompt=_read_string(final, "fast_mode", context="final"),
        final_debug_mode_prompt=_read_string(final, "debug_mode", context="final"),
        final_fallback_thought=_read_string(final, "fallback_thought", context="final"),
        final_fallback_speech=_read_string(final, "fallback_speech", context="final"),
        router_system_prompt=_read_string_or_default(router, "system", context="router", default=""),
        router_fast_mode_prompt=_read_string_or_default(router, "fast_mode", context="router", default=""),
        router_debug_mode_prompt=_read_string_or_default(router, "debug_mode", context="router", default=""),
        verifier_system_prompt=_read_string_or_default(verifier, "system", context="verifier", default=""),
        verifier_fast_mode_prompt=_read_string_or_default(verifier, "fast_mode", context="verifier", default=""),
        verifier_debug_mode_prompt=_read_string_or_default(verifier, "debug_mode", context="verifier", default=""),
        summary_fallback_diary_template=_read_string_or_default(summary, "fallback_diary_template", context="summary", default=""),
        summary_system_prompt=_read_string_or_default(summary, "system", context="summary", default=""),
        summary_user_prompt_template=_read_string_or_default(summary, "user_prompt_template", context="summary", default=""),
        semantic_summary_fallback_template=_read_string_or_default(semantic_summary, "fallback_template", context="semantic_summary", default=""),
        semantic_summary_system_prompt=_read_string_or_default(semantic_summary, "system", context="semantic_summary", default=""),
        semantic_summary_user_prompt_template=_read_string_or_default(semantic_summary, "user_prompt_template", context="semantic_summary", default=""),
        semantic_reinforcement_system_prompt=_read_string_or_default(semantic_reinforcement, "system", context="semantic_reinforcement", default=""),
        semantic_reinforcement_user_prompt_template=_read_string_or_default(semantic_reinforcement, "user_prompt_template", context="semantic_reinforcement", default=""),
    )


PERSONA = load_persona_config()
