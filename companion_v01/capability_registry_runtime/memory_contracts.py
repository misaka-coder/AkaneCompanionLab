"""Package-owned memory projection and character-context tool contracts."""
from __future__ import annotations

from capcore import CapabilityToolSpec
from typing import Any
from memcore import build_native_memory_tool_specs


def _memcore_tool_contract(package_name: str, product_name: str) -> tuple[str, dict[str, Any]]:
    """Project the package-owned memory schema into an Akane capability name."""

    specs = build_native_memory_tool_specs(tool_format="plain", include_material_tool=False)
    spec = next(
        (item for item in specs if str(item.get("name") or "") == package_name),
        None,
    )
    if spec is None:
        raise RuntimeError(f"memcore_native_tool_contract_missing:{package_name}")
    name_projection = {
        "retrieve_for_turn": "retrieve_memory",
        "read_timeline": "read_memory_timeline",
        package_name: product_name,
    }

    def _rename(value: Any) -> Any:
        if isinstance(value, dict):
            return {str(key): _rename(item) for key, item in value.items()}
        if isinstance(value, list):
            return [_rename(item) for item in value]
        if isinstance(value, str):
            projected = value
            for source_name, target_name in name_projection.items():
                projected = projected.replace(source_name, target_name)
            return projected
        return value

    return str(_rename(spec.get("description")) or ""), dict(_rename(spec.get("parameters")) or {})


def _conversation_read_contract(description: str, schema: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    properties = dict(schema.get("properties") or {})
    properties.pop("cross_conversation", None)
    properties["conversation"] = {
        "type": "string", "maxLength": 48,
        "description": 'Optional single target: "current" (default), "master", or "group:123456". '
                       "Use list_memory_conversations if the group number is unknown.",
    }
    return (
        "历史记忆通过记忆读取工具查询。首次跨 QQ 会话指定一个 conversation；已选卡片 ID 和读取凭据继承可信工具结果的来源会话。"
        "空结果不会自动搜索其他会话；后续打开和分页保持所选来源。"
        "回答范围以实际读到的证据为准；选取部分原话时说明是举例。 " + description,
        {**schema, "properties": properties},
    )


_RETRIEVE_MEMORY_DESCRIPTION, _RETRIEVE_MEMORY_SCHEMA = _memcore_tool_contract(
    "retrieve_for_turn",
    "retrieve_memory",
)
_READ_MEMORY_TIMELINE_DESCRIPTION, _READ_MEMORY_TIMELINE_SCHEMA = _memcore_tool_contract(
    "read_timeline",
    "read_memory_timeline",
)
_BROWSE_MEMORY_DESCRIPTION, _BROWSE_MEMORY_SCHEMA = _memcore_tool_contract(
    "browse_memory",
    "browse_memory",
)
_OPEN_MEMORY_DESCRIPTION, _OPEN_MEMORY_SCHEMA = _memcore_tool_contract(
    "open_memory",
    "open_memory",
)

_RETRIEVE_MEMORY_DESCRIPTION, _RETRIEVE_MEMORY_SCHEMA = _conversation_read_contract(
    _RETRIEVE_MEMORY_DESCRIPTION, _RETRIEVE_MEMORY_SCHEMA)
_READ_MEMORY_TIMELINE_DESCRIPTION, _READ_MEMORY_TIMELINE_SCHEMA = _conversation_read_contract(
    _READ_MEMORY_TIMELINE_DESCRIPTION, _READ_MEMORY_TIMELINE_SCHEMA)
_BROWSE_MEMORY_DESCRIPTION, _BROWSE_MEMORY_SCHEMA = _conversation_read_contract(
    _BROWSE_MEMORY_DESCRIPTION, _BROWSE_MEMORY_SCHEMA)
_OPEN_MEMORY_DESCRIPTION, _OPEN_MEMORY_SCHEMA = _conversation_read_contract(
    _OPEN_MEMORY_DESCRIPTION, _OPEN_MEMORY_SCHEMA)


def _clarify_explicit_kind_contract(schema: dict[str, Any]) -> dict[str, Any]:
    """State the include_explicit/kind_patterns coupling the package schema omits.

    Passing include_explicit=true without kind_patterns (or the reverse) is
    rejected by validation, which historically turned whole retrievals into
    empty results. The descriptions are patched Akane-side so the fix reaches
    the model without a memcore wheel change.
    """
    properties = dict(schema.get("properties") or {})
    if "include_explicit" in properties:
        properties["include_explicit"] = {
            **properties["include_explicit"],
            "description": (
                "Whether this query needs explicit trace/event/material records. "
                "Set true only WITH kind_patterns "
                '(e.g. ["tool.*"]); true without kind_patterns is rejected '
                "and returns nothing. Leave false for ordinary chat."
            ),
        }
    if "kind_patterns" in properties:
        properties["kind_patterns"] = {
            **properties["kind_patterns"],
            "description": (
                'Kinds of explicit trace/event/material records to include (e.g. "tool.*"). '
                "Only valid together with include_explicit=true; "
                "omit both unless tool/material traces are specifically needed."
            ),
        }
    return {**schema, "properties": properties}


_RETRIEVE_MEMORY_SCHEMA = _clarify_explicit_kind_contract(_RETRIEVE_MEMORY_SCHEMA)


RETRIEVE_MEMORY_TOOL_SPEC = CapabilityToolSpec(
    capability_id="retrieve_memory",
    display_name="Retrieve memory",
    description=_RETRIEVE_MEMORY_DESCRIPTION,
    input_schema=_RETRIEVE_MEMORY_SCHEMA,
    risk="low",
    confirm="never",
    effects=(),
    visible_in=("desktop", "qq", "web"),
    spec_version="2.1.0",
    schema_version=3,
    execution_class="sync",
    idempotency="read_only",
    max_result_bytes=16384,
)

READ_MEMORY_TIMELINE_TOOL_SPEC = CapabilityToolSpec(
    capability_id="read_memory_timeline",
    display_name="Read memory timeline",
    description=_READ_MEMORY_TIMELINE_DESCRIPTION,
    input_schema=_READ_MEMORY_TIMELINE_SCHEMA,
    risk="low",
    confirm="never",
    effects=(),
    visible_in=("desktop", "qq", "web"),
    spec_version="2.1.0",
    schema_version=3,
    execution_class="sync",
    idempotency="read_only",
    max_result_bytes=16384,
)

BROWSE_MEMORY_TOOL_SPEC = CapabilityToolSpec(
    capability_id="browse_memory",
    display_name="Browse memory catalog",
    description=_BROWSE_MEMORY_DESCRIPTION,
    input_schema=_BROWSE_MEMORY_SCHEMA,
    risk="low",
    confirm="never",
    effects=(),
    visible_in=("desktop", "qq", "web"),
    spec_version="2.1.0",
    schema_version=3,
    execution_class="sync",
    idempotency="read_only",
    max_result_bytes=65536,
)

OPEN_MEMORY_TOOL_SPEC = CapabilityToolSpec(
    capability_id="open_memory",
    display_name="Open memory evidence",
    description=_OPEN_MEMORY_DESCRIPTION,
    input_schema=_OPEN_MEMORY_SCHEMA,
    risk="low",
    confirm="never",
    effects=(),
    visible_in=("desktop", "qq", "web"),
    spec_version="2.3.0",
    schema_version=5,
    execution_class="sync",
    idempotency="read_only",
    max_result_bytes=65536,
)

LIST_MEMORY_CONVERSATIONS_TOOL_SPEC = CapabilityToolSpec(
    capability_id="list_memory_conversations",
    display_name="List readable memory conversations",
    description=("只列当前 Bot 与角色域中可读的 QQ 记忆会话目录，不检索消息。"
                 "关闭跨会话开关或非主人私聊时仅列当前会话；用返回的 conversation 指定记忆读取工具的来源。"),
    input_schema={
        "type": "object", "additionalProperties": False,
        "properties": {
            "cursor": {"type": "string", "description": "上一页返回的 opaque cursor；续页只传 cursor。"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50},
        },
        "required": [],
    },
    risk="low", confirm="never", effects=(), visible_in=("desktop", "qq", "web"),
    spec_version="1.0.0", schema_version=1, execution_class="sync",
    idempotency="read_only", max_result_bytes=16384,
)

# ── M66-C: Canonical ToolSpecs for remaining built-in families ──────────────
# Each family below removes the corresponding TOOL_METADATA_BY_TYPE input_schema
# and build_prompt_instruction() as semantic authorities for that tool.

LOAD_CHARACTER_CONTEXT_TOOL_SPEC = CapabilityToolSpec(
    capability_id="load_character_context",
    display_name="Load character context",
    description=(
        "Load specific entries from the active character pack's context libraries by their target names. "
        "Read-only; does not modify anything."
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "targets": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": 20,
                "description": "Target names to load, taken from the character pack's listed libraries/entries.",
            },
        },
        "required": ["targets"],
    },
    risk="low",
    confirm="never",
    effects=(),
    visible_in=("desktop", "qq", "web"),
    spec_version="1.0.0",
    schema_version=1,
    execution_class="sync",
    idempotency="read_only",
    max_result_bytes=32768,
)
