"""Compact final reply history while retaining original output for delivery and recall."""
from __future__ import annotations

import json
import re
from dataclasses import replace


ARCHIVED_SAY = "[hidden]"
ARCHIVED_STYLE = ARCHIVED_SAY
# Frozen history upgrades keep a monotonically increasing generation.
ASSISTANT_HISTORY_PROJECTION_VERSION = 19
ASSISTANT_HISTORY_FORMAT = "text"
ASSISTANT_HISTORY_RECORD_HEADER = "过往发言"
LEGACY_ASSISTANT_HISTORY_RECORD_HEADER = "历史回复（显示记录）"


def _history_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _history_annotation(value) -> str:
    return value if isinstance(value, str) else _history_json(value)


def _render_utterance_record(reply: dict, units: list[dict]) -> str:
    overall = (
        "（整体表情 " + _history_annotation(reply["emotion"]) + "）"
        if "emotion" in reply else ""
    )
    lines = [ASSISTANT_HISTORY_RECORD_HEADER + overall + "："]
    if not units:
        lines.append("（沉默）")
    for unit in units:
        labels = []
        if "emotion" in unit and ("emotion" not in reply or unit["emotion"] != reply["emotion"]):
            labels.append("表情 " + _history_annotation(unit["emotion"]))
        style = unit.get("style")
        if isinstance(style, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,40}", style.strip()):
            labels.append("语气 " + style)
        say = unit.get("say")
        if not isinstance(say, str) or not say.strip() or say.strip() in {"[hidden]", "[archived]"}:
            labels.append("仅显示")
        annotation = "（" + "，".join(labels) + "）" if labels else ""
        lines.append("“" + unit["text"] + "”" + annotation)
        extra = {key: value for key, value in unit.items() if key not in {"emotion", "style", "text", "say"}}
        if extra:
            lines.append("   附加字段: " + _history_json(extra))
    extra = {key: value for key, value in reply.items()
             if key not in {"emotion", "utterances", "reply_medium"}
             and not (key == "memory_metadata" and isinstance(value, dict))}
    if extra:
        lines.append("附加字段: " + _history_json(extra))
    return "\n".join(lines)


def _project_utterance_history(unit: dict) -> dict:
    projected = dict(unit)
    if isinstance(unit.get("say"), str) and unit["say"].strip():
        projected["say"] = ARCHIVED_SAY
    style = unit.get("style")
    # Legacy reference-profile labels stay available in model history. Prose
    # intent remains in the original reply used for delivery and recall.
    if (isinstance(style, str) and style.strip()
            and not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", style.strip())):
        projected["style"] = ARCHIVED_STYLE
    return projected


def project_assistant_history_content(provider_output_raw: str, *, history_format: str | None = None) -> str:
    """Freeze a reading record; keep JSON available for controlled comparison.

    The caller still commits provider_output_raw and real memory annotations.
    This function runs only when a final reply is committed, not on history reads.
    """
    source = provider_output_raw.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*\n([\s\S]*?)\n?```", source, re.IGNORECASE)
    try:
        reply = json.loads(fenced.group(1) if fenced else source)
    except (TypeError, ValueError):
        return provider_output_raw
    if not isinstance(reply, dict):
        return provider_output_raw
    if any(reply.get(key) for key in ("tool_call", "tool_calls", "_native_tool_call", "_native_tool_calls")):
        return provider_output_raw
    units = reply.get("utterances")
    if isinstance(units, list):
        if any(not isinstance(unit, dict) or not isinstance(unit.get("text"), str) for unit in units):
            return provider_output_raw
        if (history_format or ASSISTANT_HISTORY_FORMAT) == "text":
            return _render_utterance_record(reply, units)
        reply["utterances"] = [_project_utterance_history(unit) for unit in units]
    elif not isinstance(reply.get("speech"), str) and not isinstance(reply.get("beats"), list):
        return provider_output_raw
    # Retired routing hints must not become examples for the current output format.
    reply.pop("reply_medium", None)
    if isinstance(reply.get("memory_metadata"), dict):
        reply.pop("memory_metadata")
        reply["memory_metadata"] = {}
    return json.dumps(reply, ensure_ascii=False, separators=(",", ":"))


def project_assistant_history_payload(payload: dict, *, history_format: str | None = None) -> dict:
    """Preserve provider envelopes and native blocks while compacting final text."""
    if payload.get("role") != "assistant" or payload.get("tool_calls"):
        return payload
    content = payload.get("content")
    if isinstance(content, str):
        return {**payload, "content": project_assistant_history_content(content, history_format=history_format)}
    if isinstance(content, list):
        return {**payload, "content": [
            {**block, "text": project_assistant_history_content(block["text"], history_format=history_format)}
            if isinstance(block, dict) and block.get("type") in {"text", "output_text"}
            and isinstance(block.get("text"), str) else block
            for block in content
        ]}
    return payload


def restore_final_history_content(payload: dict, provider_output_raw: str) -> dict:
    """Rehydrate one final text during explicit migration, preserving envelopes.

    Source output is required to recover speech presence from an older lossy
    projection. History reads continue to use the frozen ledger unchanged.
    """
    if payload.get("role") != "assistant" or payload.get("tool_calls"):
        return payload
    if not isinstance(provider_output_raw, str) or not provider_output_raw.strip():
        return payload
    content = payload.get("content")
    if isinstance(content, str):
        return {**payload, "content": provider_output_raw}
    if isinstance(content, list):
        text_indexes = [index for index, block in enumerate(content)
                        if isinstance(block, dict) and block.get("type") in {"text", "output_text"}
                        and isinstance(block.get("text"), str)]
        if len(text_indexes) == 1:
            index = text_indexes[0]
            return {**payload, "content": [
                {**block, "text": provider_output_raw} if i == index else block
                for i, block in enumerate(content)]}
    return payload


def build_assistant_history_projection_adapter(*, renderer_registry, timezone):
    """Host adapter for lazy provider profiles and terminal-turn settlement."""
    from memcore.projection import ProjectionAdapter

    class AssistantHistoryProjectionAdapter(ProjectionAdapter):
        def context_surface_payload(self, entry, **kwargs):
            # Final replies already own a frozen provider payload. Returning None
            # makes ContextSurface use that ledger row instead of rehydrating raw
            # speech/metadata. It also preserves pre-migration frozen versions.
            if entry.kind == "message.assistant" and str(entry.turn_role) == "final":
                return None
            return super().context_surface_payload(entry, **kwargs)

        def project_entries(self, entries, **kwargs):
            messages = super().project_entries(entries, **kwargs)
            finals = {entry.source_id for entry in entries
                      if entry.kind == "message.assistant" and str(entry.turn_role) == "final"}
            return tuple(
                replace(message, payload=project_assistant_history_payload(dict(message.payload)),
                        projection_version=ASSISTANT_HISTORY_PROJECTION_VERSION)
                if message.source_ids and all(source in finals for source in message.source_ids)
                else message for message in messages
            )

    return AssistantHistoryProjectionAdapter(renderer_registry=renderer_registry, timezone=timezone)


def configure_assistant_history_projection(system) -> None:
    # MemCore's request builder and compactor share an adapter. Keep that identity
    # when installing the host projection policy; raw/source rendering is unchanged.
    adapter = build_assistant_history_projection_adapter(
        renderer_registry=system.renderer_registry, timezone=system.timezone)
    system._projection = adapter
    system._projection_ledger.adapter = adapter
    system._compaction.projection_adapter = adapter
    system._compaction.projection_ledger.adapter = adapter
