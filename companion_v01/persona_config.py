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
    router_system_prompt: str
    router_fast_mode_prompt: str
    router_debug_mode_prompt: str
    verifier_system_prompt: str
    verifier_fast_mode_prompt: str
    verifier_debug_mode_prompt: str
    final_system_prompt: str
    final_fast_mode_prompt: str
    final_debug_mode_prompt: str
    final_fallback_thought: str
    final_fallback_speech: str
    final_user_prompt_suffix: str
    summary_fallback_diary_template: str
    summary_system_prompt: str
    summary_user_prompt_template: str
    semantic_summary_fallback_template: str
    semantic_summary_system_prompt: str
    semantic_summary_user_prompt_template: str
    semantic_reinforcement_system_prompt: str
    semantic_reinforcement_user_prompt_template: str

    def build_summary_fallback_diary(self, tags: str) -> str:
        return self.summary_fallback_diary_template.format(tags=tags)

    def build_semantic_fallback_summary(self, tags: str) -> str:
        return self.semantic_summary_fallback_template.format(tags=tags)


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
    router = _read_table(variant_payload, "router", context=f"{resolved_path}:{selected_variant}")
    verifier = _read_table(variant_payload, "verifier", context=f"{resolved_path}:{selected_variant}")
    final = _read_table(variant_payload, "final", context=f"{resolved_path}:{selected_variant}")
    summary = _read_table(variant_payload, "summary", context=f"{resolved_path}:{selected_variant}")
    semantic_summary = _read_table(variant_payload, "semantic_summary", context=f"{resolved_path}:{selected_variant}")
    semantic_reinforcement = _read_table(variant_payload, "semantic_reinforcement", context=f"{resolved_path}:{selected_variant}")

    return PersonaConfig(
        assistant_name=_read_string(meta, "assistant_name", context="meta"),
        user_label=_read_string(meta, "user_label", context="meta"),
        trace_prefix=_read_string(meta, "trace_prefix", context="meta"),
        surprise_memory_reason=_read_string(meta, "surprise_memory_reason", context="meta"),
        router_system_prompt=_read_string(router, "system", context="router"),
        router_fast_mode_prompt=_read_string(router, "fast_mode", context="router"),
        router_debug_mode_prompt=_read_string(router, "debug_mode", context="router"),
        verifier_system_prompt=_read_string(verifier, "system", context="verifier"),
        verifier_fast_mode_prompt=_read_string(verifier, "fast_mode", context="verifier"),
        verifier_debug_mode_prompt=_read_string(verifier, "debug_mode", context="verifier"),
        final_system_prompt=_read_string(final, "system", context="final"),
        final_fast_mode_prompt=_read_string(final, "fast_mode", context="final"),
        final_debug_mode_prompt=_read_string(final, "debug_mode", context="final"),
        final_fallback_thought=_read_string(final, "fallback_thought", context="final"),
        final_fallback_speech=_read_string(final, "fallback_speech", context="final"),
        final_user_prompt_suffix=_read_string(final, "user_prompt_suffix", context="final"),
        summary_fallback_diary_template=_read_string(summary, "fallback_diary_template", context="summary"),
        summary_system_prompt=_read_string(summary, "system", context="summary"),
        summary_user_prompt_template=_read_string_or_default(
            summary,
            "user_prompt_template",
            context="summary",
            default=(
                "请总结下面这段较早的{batch_size}条对话：\n"
                "{transcript}\n\n"
                "要求：简洁、连贯、适合后续记忆检索。\n"
                "再次强调：importance 只能是数字，不要写成高、中、低。"
            ),
        ),
        semantic_summary_fallback_template=_read_string(semantic_summary, "fallback_template", context="semantic_summary"),
        semantic_summary_system_prompt=_read_string(semantic_summary, "system", context="semantic_summary"),
        semantic_summary_user_prompt_template=_read_string_or_default(
            semantic_summary,
            "user_prompt_template",
            context="semantic_summary",
            default=(
                "请把下面这组更早的阶段摘要，再压缩成一条长期语义记忆：\n"
                "{source_text}\n\n"
                "要求：保留长期稳定事实、反复话题、重要人物和未完成线索；不要写成流水账。\n"
                "再次强调：importance 只能是 0 到 1 之间的数字。"
            ),
        ),
        semantic_reinforcement_system_prompt=_read_string(semantic_reinforcement, "system", context="semantic_reinforcement"),
        semantic_reinforcement_user_prompt_template=_read_string_or_default(
            semantic_reinforcement,
            "user_prompt_template",
            context="semantic_reinforcement",
            default=(
                "已有长期语义记忆：\n"
                "{existing_text}\n\n"
                "新的阶段摘要压缩结果：\n"
                "{incoming_text}\n\n"
                "请输出融合后的长期记忆，尽量保留旧的稳定信息，同时吸收新的重复线索。"
            ),
        ),
    )


PERSONA = load_persona_config()
