"""Stable entry points for the host-owned capability directory and dispatch."""

from __future__ import annotations

from capcore import CapabilityToolSpec


CAPABILITY_SEARCH_TOOL_SPEC = CapabilityToolSpec(
    capability_id="capability_search",
    display_name="Search capabilities",
    description=(
        "Search the capabilities that are already available in this turn. "
        "Optional case-insensitive substring search, not semantic search. "
        "Known exact ids can be loaded directly without searching. "
        "Use capability_list to browse even when a query has no matches."
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "query": {
                "type": "string",
                "maxLength": 160,
                "description": "Optional case-insensitive text matched against id, name, and short description.",
            },
            "cursor": {
                "type": "string",
                "maxLength": 2048,
                "description": "Opaque cursor returned by a previous capability_search page.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 32,
                "description": "Maximum number of short entries to return. Defaults to 12.",
            },
        },
        "required": [],
    },
    output_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "status": {"type": "string", "enum": ["ok", "rejected"]},
            "items": {"type": "array", "items": {"type": "object"}},
            "next_cursor": {"type": "string"},
            "catalog_revision": {"type": "string"},
            "catalog_size": {"type": "integer", "minimum": 0},
            "match_count": {"type": "integer", "minimum": 0},
            "reason": {"type": "string"},
            "recovery_hint": {"type": "string"},
        },
        "required": ["status", "items", "next_cursor", "catalog_revision", "reason"],
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


CAPABILITY_LOAD_TOOL_SPEC = CapabilityToolSpec(
    capability_id="capability_load",
    display_name="Load capability schemas",
    description=(
        "Load current usage and input schema for exact capability ids. Search is optional. "
        "Use capability_invoke with the capability_id and business arguments; loading does not "
        "grant permission or add native tools. Available ids in a partial result remain usable."
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "capability_ids": {
                "type": "array",
                "items": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 128,
                },
                "minItems": 1,
                "maxItems": 32,
                "description": "Exact capability ids from the visible directory, enumeration or history; search is optional.",
            },
        },
        "required": ["capability_ids"],
    },
    output_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "status": {"type": "string", "enum": ["ok", "partial", "rejected"]},
            "capabilities": {"type": "array", "items": {"type": "object"}},
            "missing_capability_ids": {"type": "array", "items": {"type": "string"}},
            "recovery_hint": {"type": "string"},
            "catalog_revision": {"type": "string"},
            "reason": {"type": "string"},
        },
        "required": [
            "status",
            "capabilities",
            "catalog_revision",
            "reason",
        ],
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


CAPABILITY_LIST_TOOL_SPEC = CapabilityToolSpec(
    capability_id="capability_list",
    display_name="List capabilities",
    description="Enumerate the currently visible capability directory with exact ids and short purposes. No search required; follow next_cursor for further entries.",
    input_schema={
        "type": "object", "additionalProperties": False,
        "properties": {
            "cursor": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 32},
        }, "required": [],
    },
    risk="low", confirm="never", effects=(), visible_in=("desktop", "qq", "web"),
    spec_version="1.0.0", schema_version=1, execution_class="sync", idempotency="read_only",
)

CAPABILITY_INVOKE_TOOL_SPEC = CapabilityToolSpec(
    capability_id="capability_invoke",
    display_name="Invoke a capability",
    description=(
        "Execute an exact capability_id with its business arguments inside one arguments object. "
        "Use capability_load if its current usage is unclear. Current validation and permissions apply. "
        "Never retry an action whose completion is unknown."
    ),
    input_schema={
        "type": "object", "additionalProperties": False,
        "properties": {
            "capability_id": {"type": "string", "minLength": 1},
            "arguments": {"type": "object", "additionalProperties": True},
        }, "required": ["capability_id", "arguments"],
    },
    risk="medium", confirm="never", effects=(), visible_in=("desktop", "qq", "web"),
    spec_version="1.0.0", schema_version=1, execution_class="sync", idempotency="effectful",
)

__all__ = ["CAPABILITY_SEARCH_TOOL_SPEC", "CAPABILITY_LOAD_TOOL_SPEC", "CAPABILITY_LIST_TOOL_SPEC", "CAPABILITY_INVOKE_TOOL_SPEC"]
