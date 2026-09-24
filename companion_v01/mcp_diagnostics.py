"""Bounded, actionable diagnostics for model-facing MCP failures."""

from __future__ import annotations

import asyncio
import re
from typing import Any


_MAX_DETAIL_CHARS = 800
_GENERIC_REASONS = {
    "mcp_tools_list_failed",
    "mcp_tool_call_failed",
    "mcp_start_failed",
    "adapter_invoke_failed",
}
_REASON_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{1,95}$")


def mcp_failure_reason(error: BaseException, *, fallback: str) -> str:
    """Return a stable code; human diagnostics belong in the bounded detail."""

    for item in _exception_chain(error):
        candidate = str(item or "").strip().split(":", 1)[0].strip().lower()
        if _REASON_CODE_RE.fullmatch(candidate):
            return candidate
    safe_fallback = str(fallback or "mcp_operation_failed").strip().lower()
    return safe_fallback if _REASON_CODE_RE.fullmatch(safe_fallback) else "mcp_operation_failed"


def build_mcp_failure_diagnostic(error: BaseException, *, stage: str) -> dict[str, str]:
    """Classify an exception chain without exposing credentials or a traceback."""

    chain = _exception_chain(error)
    joined = " | ".join(str(item or "") for item in chain).lower()
    normalized_stage = str(stage or "").strip().lower()
    is_tool_call = normalized_stage in {"call_tool", "tool_call", "tools_call"}
    if any(isinstance(item, FileNotFoundError) for item in chain):
        category = "command_not_found"
        action = "Verify the current official install instructions and the configured executable or PATH."
    elif any(isinstance(item, PermissionError) for item in chain):
        category = "command_not_executable"
        action = "Verify that the configured executable exists and is runnable by this Host."
    elif any(isinstance(item, (asyncio.TimeoutError, TimeoutError)) for item in chain) or "timed out" in joined:
        category = "tool_timeout" if is_tool_call else "initialization_timeout"
        action = "Run the exact configured command with exec_run, inspect its bounded stderr, and verify its official startup mode."
    elif any(marker in joined for marker in ("deprecated", "no longer supported", "unmaintained")):
        category = "deprecated_distribution"
        action = "Re-open the official repository or registry and select its currently maintained package, binary, or image."
    elif any(
        marker in joined
        for marker in (
            "authentication required",
            "authorization required",
            "missing credential",
            "mcp_credential_missing",
            "missing token",
            "token is required",
            "api key is required",
            "unauthorized",
            "forbidden",
        )
    ):
        category = "authentication_required"
        action = "Follow the current official authentication flow and bind credentials through Host environment placeholders, never literals."
    elif any(marker in joined for marker in ("initialize", "protocol", "stdout", "stdio", "jsonrpc", "json-rpc")):
        category = "protocol_startup_failed"
        action = "Verify the official MCP startup command and ensure stdout is reserved for the MCP protocol."
    elif any(marker in joined for marker in ("connection", "connect", "dns", "http", "network")):
        category = "transport_unavailable"
        action = "Verify the configured endpoint, network reachability, and the provider's current service instructions."
    else:
        category = "tool_call_failed" if is_tool_call else "startup_failed"
        action = "Inspect the exact command or endpoint with exec_run and re-check the current official MCP documentation."

    detail = _useful_detail(chain)
    payload = {
        "stage": str(stage or "mcp_operation").strip() or "mcp_operation",
        "category": category,
        "recommendedAction": action,
    }
    if detail:
        payload["detail"] = detail
    return payload


def _exception_chain(error: BaseException) -> list[BaseException]:
    pending: list[BaseException] = [error]
    seen: set[int] = set()
    result: list[BaseException] = []
    while pending and len(result) < 12:
        current = pending.pop(0)
        if id(current) in seen:
            continue
        seen.add(id(current))
        result.append(current)
        nested = getattr(current, "exceptions", None)
        if isinstance(nested, (list, tuple)):
            pending.extend(item for item in nested if isinstance(item, BaseException))
        cause = current.__cause__ or current.__context__
        if isinstance(cause, BaseException):
            pending.append(cause)
    return result


def _useful_detail(chain: list[BaseException]) -> str:
    messages: list[str] = []
    for error in reversed(chain):
        text = _sanitize_detail(str(error or ""))
        if not text or text.lower() in _GENERIC_REASONS or text in messages:
            continue
        messages.append(text)
    return " | ".join(messages)[:_MAX_DETAIL_CHARS]


def _sanitize_detail(value: Any) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"(?i)\bbearer\s+[^\s,;]+", "Bearer [redacted]", text)
    text = re.sub(
        r"(?i)\b(api[_-]?key|authorization|cookie|password|secret|token)\s*[:=]\s*[^\s,;]+",
        r"\1=[redacted]",
        text,
    )
    text = re.sub(r"\b(?:github_pat_|gh[pousr]_)[A-Za-z0-9_]+", "[redacted]", text)
    text = re.sub(r"\bsk[-_][A-Za-z0-9_-]+", "[redacted]", text)
    return re.sub(r"\s+", " ", text).strip()


__all__ = ["build_mcp_failure_diagnostic", "mcp_failure_reason"]
