"""Validate plugin-owned semantics before Akane builds model-facing feedback."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .plugin_api import PluginResultExperience, PluginResultPayload


PLUGIN_RESULT_EXPERIENCE_KEY = "result_experience"
PLUGIN_RESULT_DATA_KEY = "result"
PLUGIN_RESULT_RESERVED_KEYS = frozenset({PLUGIN_RESULT_EXPERIENCE_KEY, "managed_artifacts"})

_MAX_SUMMARY_CHARS = 1200
_MAX_AS_OF_CHARS = 120
_MAX_FACTS = 16
_MAX_FACT_CHARS = 500
_MAX_WARNINGS = 8
_MAX_WARNING_CHARS = 500
_MAX_NOTES = 8
_MAX_NOTE_CHARS = 500
_MAX_ACTIONS = 8
_MAX_ACTION_CHARS = 240


class PluginResultExperienceError(RuntimeError):
    def __init__(self, reason: str = "plugin_result_experience_invalid") -> None:
        self.reason = str(reason or "plugin_result_experience_invalid")
        super().__init__(self.reason)


def project_plugin_result_payload(payload: PluginResultPayload) -> dict[str, Any]:
    if not isinstance(payload, PluginResultPayload):
        raise PluginResultExperienceError()
    experience = payload.experience
    if not isinstance(experience, PluginResultExperience):
        raise PluginResultExperienceError()

    summary = _required_text(experience.summary, max_chars=_MAX_SUMMARY_CHARS)
    as_of = _optional_text(experience.as_of, max_chars=_MAX_AS_OF_CHARS)
    facts = _bounded_text_tuple(
        experience.facts,
        max_items=_MAX_FACTS,
        max_chars=_MAX_FACT_CHARS,
    )
    warnings = _bounded_text_tuple(
        experience.warnings,
        max_items=_MAX_WARNINGS,
        max_chars=_MAX_WARNING_CHARS,
    )
    interpretation_notes = _bounded_text_tuple(
        experience.interpretation_notes,
        max_items=_MAX_NOTES,
        max_chars=_MAX_NOTE_CHARS,
    )
    suggested_next_actions = _bounded_text_tuple(
        experience.suggested_next_actions,
        max_items=_MAX_ACTIONS,
        max_chars=_MAX_ACTION_CHARS,
    )
    projected_experience: dict[str, Any] = {"summary": summary}
    if facts:
        projected_experience["facts"] = list(facts)
    if as_of:
        projected_experience["as_of"] = as_of
    if warnings:
        projected_experience["warnings"] = list(warnings)
    if interpretation_notes:
        projected_experience["interpretation_notes"] = list(interpretation_notes)
    if suggested_next_actions:
        projected_experience["suggested_next_actions"] = list(suggested_next_actions)
    return {
        PLUGIN_RESULT_DATA_KEY: payload.content,
        PLUGIN_RESULT_EXPERIENCE_KEY: projected_experience,
    }


def has_reserved_plugin_result_key(value: Any) -> bool:
    return isinstance(value, Mapping) and any(key in value for key in PLUGIN_RESULT_RESERVED_KEYS)


def _required_text(value: Any, *, max_chars: int) -> str:
    text = _optional_text(value, max_chars=max_chars)
    if not text:
        raise PluginResultExperienceError()
    return text


def _optional_text(value: Any, *, max_chars: int) -> str:
    if not isinstance(value, str):
        raise PluginResultExperienceError()
    text = value.strip()
    if len(text) > max_chars:
        raise PluginResultExperienceError()
    return text


def _bounded_text_tuple(value: Any, *, max_items: int, max_chars: int) -> tuple[str, ...]:
    if not isinstance(value, tuple) or len(value) > max_items:
        raise PluginResultExperienceError()
    normalized: list[str] = []
    for item in value:
        text = _required_text(item, max_chars=max_chars)
        normalized.append(text)
    return tuple(normalized)


__all__ = [
    "PLUGIN_RESULT_DATA_KEY",
    "PLUGIN_RESULT_EXPERIENCE_KEY",
    "PLUGIN_RESULT_RESERVED_KEYS",
    "PluginResultExperienceError",
    "has_reserved_plugin_result_key",
    "project_plugin_result_payload",
]
