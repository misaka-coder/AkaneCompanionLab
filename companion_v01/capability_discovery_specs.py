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
        "Load the complete current contract for exact ids from the visible directory or history. "
        "No search prerequisite. Contracts and scoped contract_refs are returned in this tool result; "
        "loading does not change native tools or grant permission. Invoke a loaded contract through "
        "capability_invoke with its contract_ref. After a resident contract becomes stale, use this "
        "load/invoke path until its native declaration is refreshed at the context boundary. "
        "Each available id is returned independently; missing ids do not discard usable contracts. "
        "Repeated ids are returned once. A partial result can be used immediately."
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
    display_name="Invoke a loaded capability",
    description=(
        "Execute one exact capability using the contract_ref disclosed by capability_load. "
        "A still-valid contract from this conversation can be reused without reloading. "
        "The target's validation, permissions, approval, execution policies and result handling apply. "
        "If the contract is stale, this attempt is not executed: load the current contract and retry "
        "through this entry point. Never retry an action whose completion is unknown."
    ),
    input_schema={
        "type": "object", "additionalProperties": False,
        "properties": {
            "capability_id": {"type": "string", "minLength": 1},
            "contract_ref": {"type": "string", "minLength": 1},
            "arguments": {"type": "object", "additionalProperties": True},
        }, "required": ["capability_id", "contract_ref", "arguments"],
    },
    risk="medium", confirm="never", effects=(), visible_in=("desktop", "qq", "web"),
    spec_version="1.0.0", schema_version=1, execution_class="sync", idempotency="effectful",
)

__all__ = ["CAPABILITY_SEARCH_TOOL_SPEC", "CAPABILITY_LOAD_TOOL_SPEC", "CAPABILITY_LIST_TOOL_SPEC", "CAPABILITY_INVOKE_TOOL_SPEC"]
