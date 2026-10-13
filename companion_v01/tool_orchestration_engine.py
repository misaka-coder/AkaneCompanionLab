"""Compatibility exports; implementations live in tool_orchestration."""

from __future__ import annotations

import config as config


from .tool_orchestration.feedback import (
    _bounded_int as _bounded_int,
    max_tool_rounds as max_tool_rounds,
    tool_round_warning_remaining as tool_round_warning_remaining,
    tool_metadata_dict as tool_metadata_dict,
    tool_call_signature as tool_call_signature,
    describe_tool_call_for_prompt as describe_tool_call_for_prompt,
    append_structured_artifact_receipts as append_structured_artifact_receipts,
    shape_tool_followup as shape_tool_followup,
    build_multi_tool_followup_context as build_multi_tool_followup_context,
    build_tool_round_warning as build_tool_round_warning,
)
from .tool_orchestration.native import (
    NativeToolDecisionPlan as NativeToolDecisionPlan,
    native_tool_decision_allowlist as native_tool_decision_allowlist,
    _native_tool_decision_allowlist_items as _native_tool_decision_allowlist_items,
    build_native_tool_schemas as build_native_tool_schemas,
    build_native_tool_decision_plan as build_native_tool_decision_plan,
    _native_tool_candidate_names as _native_tool_candidate_names,
    _native_tool_schema_for_handler as _native_tool_schema_for_handler,
    native_legacy_prompt_exclusions as native_legacy_prompt_exclusions,
)
from .tool_orchestration.normalization import (
    parse_tool_call as parse_tool_call,
    normalize_tool_call as normalize_tool_call,
    normalize_tool_invocation as normalize_tool_invocation,
    _resolved_handler_for_round as _resolved_handler_for_round,
    _normalize_invocation_source as _normalize_invocation_source,
    promote_narrated_tool_call as promote_narrated_tool_call,
)
from .tool_orchestration.validation import (
    PreparedToolInvocation as PreparedToolInvocation,
    prepare_tool_invocation as prepare_tool_invocation,
    validate_tool_invocation as validate_tool_invocation,
    _unknown_tool_recovery_message as _unknown_tool_recovery_message,
    _tool_schema_rejection_message as _tool_schema_rejection_message,
    validate_legacy_tool_call as validate_legacy_tool_call,
    classify_tool_call_rejection as classify_tool_call_rejection,
)
from .tool_orchestration.permissions import (
    _satellite_channel_rejection as _satellite_channel_rejection,
    _satellite_permission_gate as _satellite_permission_gate,
    _create_satellite_approval_request as _create_satellite_approval_request,
    _satellite_ask_result as _satellite_ask_result,
    _satellite_blocked_result as _satellite_blocked_result,
)
from .tool_orchestration.execution import (
    execute_tool_call as execute_tool_call,
    execute_tool_invocation as execute_tool_invocation,
)
from .tool_orchestration.desktop import (
    _execute_open_browser_with_broker as _execute_open_browser_with_broker,
    _execute_satellite_with_broker as _execute_satellite_with_broker,
)
from .tool_orchestration.results import (
    _final_exec_envelope as _final_exec_envelope,
    validation_result_to_envelope as validation_result_to_envelope,
    tool_execution_result_to_envelope as tool_execution_result_to_envelope,
)
