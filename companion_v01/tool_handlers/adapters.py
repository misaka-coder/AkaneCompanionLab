"""Capability-adapter and desktop-satellite tool handlers."""

from __future__ import annotations

import asyncio
import inspect
import json
import re
import threading
from pathlib import Path
from typing import Any, Mapping

import config
from capcore import (
    build_permission_request as capcore_build_permission_request,
    build_tool_spec as capcore_build_tool_spec,
    validate_invocation_args as capcore_validate_invocation_args,
)
from ..capcore_runtime import (
    approval_required_event as capcore_approval_required_event,
    invocation_context_from_execution as capcore_invocation_context_from_execution,
    resolve_permission_for_profile as capcore_resolve_permission_for_profile,
)
from ..capability_adapters import CapabilityProtocolError, InvocationContext
from ..desktop_satellite_specs import desktop_satellite_spec
from .core import (
    BaseToolHandler,
    ToolExecutionContext,
    ToolExecutionResult,
    ToolMetadata,
)

class DesktopSatelliteToolHandler(BaseToolHandler):
    """Schema adapter for a reviewed tool executed by the bound PC satellite.

    This handler never performs the local action itself. The invocation layer
    validates the call and sends it through ExecutorBroker to the live offer.
    """

    policy_accepted_native_tool = True

    def __init__(self, *, tool_id: str, offer_source: Any = None) -> None:
        spec = desktop_satellite_spec(tool_id)
        if spec is None:
            raise ValueError(f"unknown desktop satellite tool: {tool_id}")
        self.tool_type = spec.capability_id
        self._spec = spec
        self._offer_source = offer_source

    def tool_spec(self):
        return self._spec

    def tool_metadata(self) -> ToolMetadata:
        return ToolMetadata(
            family="desktop_satellite",
            operation="external",
            risk=self._spec.risk,
            default_round_budget=3,
            input_schema=self._spec.input_schema,
            requires_confirmation=self._spec.confirm != "never",
        )

    def build_prompt_instruction(self) -> str:
        return (
            f"- {self._spec.capability_id}：{self._spec.description}"
            f"调用参数遵循：{json.dumps(self._spec.input_schema, ensure_ascii=False, sort_keys=True)}"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict) or str(value.get("type") or "").strip() != self.tool_type:
            return None
        args = {
            key: item
            for key, item in value.items()
            if key != "type" and not str(key).startswith("_tool_")
        }
        if self.tool_type in {"desktop_context_snapshot", "system_media_snapshot", "system_process_snapshot"}:
            return {"type": self.tool_type} if not args else None
        if self.tool_type == "system_media_control":
            action = str(args.get("action") or "").strip().lower()
            if action not in {"play", "pause", "stop", "previous", "next"} or set(args) != {"action"}:
                return None
            return {"type": self.tool_type, "action": action}
        if self.tool_type == "system_process_terminate":
            if set(args) != {"pid"}:
                return None
            raw_pid = args.get("pid")
            if isinstance(raw_pid, bool) or not isinstance(raw_pid, int):
                return None
            pid = raw_pid
            if pid < 1 or pid > 0xFFFFFFFF:
                return None
            return {"type": self.tool_type, "pid": pid}
        if self.tool_type == "system_volume":
            action = str(args.get("action") or "").strip().lower()
            if action == "get":
                return {"type": self.tool_type, "action": "get"} if set(args) == {"action"} else None
            if action == "set":
                if set(args) != {"action", "value"}:
                    return None
                raw_value = args.get("value")
                if isinstance(raw_value, bool) or not isinstance(raw_value, int):
                    return None
                value = raw_value
                if value < 0 or value > 100:
                    return None
                return {"type": self.tool_type, "action": "set", "value": value}
            return None
        return None

    def capability_status(self, **_kwargs: Any) -> dict[str, Any]:
        source = self._offer_source
        if source is None:
            return {"enabled": False, "status": "unavailable", "reason": "satellite_not_configured"}
        try:
            ready = source.resolve_receipt(self._spec) is not None
        except Exception:
            ready = False
        return {
            "enabled": ready,
            "status": "ready" if ready else "unavailable",
            "reason": "" if ready else "satellite_offline",
        }

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        del call, context
        raise RuntimeError("desktop_satellite_requires_executor_broker")


class AdapterCapabilityToolHandler(BaseToolHandler):
    MAX_FOLLOWUP_CHARS = 6000

    def __init__(
        self,
        *,
        capability_id: str,
        adapter: Any,
        descriptor: Any,
        config_base_dir: Path | str | None = None,
    ) -> None:
        self.tool_type = str(capability_id or "").strip()
        self.adapter = adapter
        self.descriptor = descriptor
        self.config_base_dir = config_base_dir

    def tool_spec(self):
        """Project reviewed adapter descriptors through capcore's canonical contract."""
        try:
            return capcore_build_tool_spec(self.descriptor)
        except Exception:
            return None

    def capability_status(self) -> dict[str, Any]:
        """Expose adapter tools only after an explicit live-provider check."""
        is_live_fn = getattr(self.adapter, "is_live", None)
        if callable(is_live_fn):
            try:
                try:
                    live = bool(is_live_fn(self.tool_type))
                except TypeError:
                    live = bool(is_live_fn())
            except Exception:
                return {"enabled": False, "status": "unavailable", "reason": "adapter_liveness_check_failed"}
            return {
                "enabled": live,
                "status": "ready" if live else "unavailable",
                "reason": "" if live else "mcp_session_not_live",
            }
        return {"enabled": False, "status": "unavailable", "reason": "adapter_liveness_check_missing"}

    def tool_metadata(self) -> ToolMetadata:
        risk = str(getattr(self.descriptor, "risk", "") or "medium").strip() or "medium"
        return ToolMetadata(
            family="adapter_capability",
            operation="external",
            risk=risk,
            default_round_budget=3,
            input_schema=self._input_schema(),
            requires_confirmation=str(getattr(self.descriptor, "confirm", "never") or "never") != "never",
        )

    def build_prompt_instruction(self) -> str:
        description = self._safe_public_text(str(getattr(self.descriptor, "short_hint", "") or ""), limit=240)
        schema_text = self._schema_prompt_text()
        source_label = self._source_label()
        parts = [
            f"- {self.tool_type}：{description or f'调用{source_label}。'}",
            f'调用格式为 {{"type":"{self.tool_type}", ...参数...}}。',
        ]
        if schema_text:
            parts.append(f"参数 schema: {schema_text}。")
        parts.append(f"该能力来自{source_label}；失败时不要假装已经完成。")
        return "".join(parts)

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        if str(value.get("type") or "").strip() != self.tool_type:
            return None
        args: dict[str, Any] = {}
        source = value.get("arguments") if isinstance(value.get("arguments"), Mapping) else value
        for key, item in dict(source or {}).items():
            clean_key = str(key or "").strip()
            if clean_key == "type" or clean_key.startswith("_tool_"):
                continue
            args[clean_key] = self._safe_arg_value(item)
        return {"type": self.tool_type, "arguments": args}

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        raw_args = call.get("arguments") if isinstance(call.get("arguments"), Mapping) else {}
        validation = capcore_validate_invocation_args(self.descriptor, raw_args)
        if not validation.ok:
            return self._validation_failed(validation)
        normalized_args = dict(validation.normalized_args)
        request = capcore_build_permission_request(
            self.descriptor,
            normalized_args,
            capcore_invocation_context_from_execution(context),
        )
        decision = capcore_resolve_permission_for_profile(
            request,
            base_dir=self.config_base_dir or getattr(config, "DATA_DIR", "users_data"),
            profile_user_id=context.profile_user_id,
        )
        if not decision.allowed:
            if decision.requires_user_decision:
                return self._approval_required(decision=decision, context=context)
            return self._blocked_by_policy(decision.reason)
        try:
            result = self._run_coro_blocking(
                self.adapter.invoke(
                    self.tool_type,
                    normalized_args,
                    InvocationContext(
                        profile_user_id=context.profile_user_id,
                        session_id=context.session_id,
                        client_mode=context.client_mode,
                    ),
                )
            )
        except CapabilityProtocolError as exc:
            return self._failure(str(exc) or "adapter_protocol_error")
        except Exception:
            return self._failure("adapter_invoke_failed")
        followup = self._format_capability_result(result)
        is_error = bool(getattr(result, "is_error", False))
        status = self._safe_public_text(getattr(result, "status", ""), limit=80) if is_error else "ok"
        status = status or ("error" if is_error else "ok")
        reason = self._safe_public_text(getattr(result, "reason", ""), limit=120) if is_error else ""
        event = {
            "type": "adapter_capability_completed",
            "capabilityId": self.tool_type,
            "status": status,
        }
        provider = self._capability_result_provider(result)
        if provider:
            event["provider"] = provider
        state_updates = {
            "adapter_capability_status": status,
            "adapter_capability_id": self.tool_type,
        }
        if reason:
            event["reason"] = reason
            state_updates["adapter_capability_reason"] = reason
        execution_result = ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=[event],
            followup_context=followup,
            state_updates=state_updates,
        )
        return self._finalize_execution_result(
            execution_result,
            capability_result=result,
            context=context,
        )

    def _capability_result_provider(self, result: Any) -> str:
        content = getattr(result, "content", None)
        if not isinstance(content, Mapping):
            return ""
        candidates = [content.get("provider")]
        nested = content.get("result")
        if isinstance(nested, Mapping):
            candidates.append(nested.get("provider"))
        for value in candidates:
            provider = self._safe_public_text(value, limit=80).strip().lower()
            if re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,79}", provider):
                return provider
        return ""

    def _finalize_execution_result(
        self,
        execution_result: ToolExecutionResult,
        *,
        capability_result: Any,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        del capability_result, context
        return execution_result

    def _approval_required(self, *, decision: Any, context: ToolExecutionContext) -> ToolExecutionResult:
        event = capcore_approval_required_event(
            decision=decision,
            capability_id=self.tool_type,
            action_id=self.tool_type,
            title=f"{self._source_label()}需要确认",
            summary=f"Akane 想执行一个{self._source_label()}。",
            client_mode=context.client_mode,
        )
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=[event],
            followup_context=f"这个{self._source_label()}需要用户确认；请自然说明需要在能力审批中允许后再执行，不要声称已经完成。",
            state_updates={
                "adapter_capability_status": "approval_required",
                "adapter_capability_id": self.tool_type,
            },
        )

    def _validation_failed(self, validation: Any) -> ToolExecutionResult:
        first = validation.errors[0] if validation.errors else None
        reason = self._safe_public_text(getattr(first, "code", "") or "validation_error", limit=80)
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=[
                {
                    "type": "adapter_capability_failed",
                    "capabilityId": self.tool_type,
                    "status": "validation_error",
                    "reason": reason,
                    "errors": [error.as_dict() for error in validation.errors[:8]],
                }
            ],
            followup_context=f"{self._source_label()}参数没有通过校验；请根据工具 schema 修正后再调用，不要声称已经完成。",
            state_updates={
                "adapter_capability_status": "validation_error",
                "adapter_capability_id": self.tool_type,
                "adapter_capability_reason": reason,
            },
        )

    def _blocked_by_policy(self, reason: str) -> ToolExecutionResult:
        safe_reason = self._safe_public_text(reason, limit=120) or "capability_blocked_by_policy"
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=[
                {
                    "type": "adapter_capability_failed",
                    "capabilityId": self.tool_type,
                    "status": "blocked",
                    "reason": safe_reason,
                }
            ],
            followup_context=f"这个{self._source_label()}已被当前能力策略阻止；请自然说明无法执行，不要假装已经完成。",
            state_updates={
                "adapter_capability_status": "blocked",
                "adapter_capability_id": self.tool_type,
                "adapter_capability_reason": safe_reason,
            },
        )

    def _failure(self, reason: str) -> ToolExecutionResult:
        safe_reason = self._safe_public_text(reason, limit=120) or "adapter_invoke_failed"
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=[
                {
                    "type": "adapter_capability_failed",
                    "capabilityId": self.tool_type,
                    "status": "error",
                    "reason": safe_reason,
                }
            ],
            followup_context=f"{self._source_label()}调用失败：{safe_reason}。不要假装已经完成。",
            state_updates={
                "adapter_capability_status": "error",
                "adapter_capability_reason": safe_reason,
            },
        )

    def _format_capability_result(self, result: Any) -> str:
        content = getattr(result, "content", None)
        if isinstance(content, Mapping):
            pieces: list[str] = []
            raw_content = content.get("content")
            if isinstance(raw_content, list):
                for item in raw_content[:8]:
                    if not isinstance(item, Mapping):
                        continue
                    if str(item.get("type") or "").strip() == "text":
                        text = self._safe_public_text(item.get("text"), limit=1200)
                        if text:
                            pieces.append(text)
                    elif item.get("type"):
                        pieces.append(f"[{self._safe_public_text(item.get('type'), limit=40)} content]")
            if not pieces:
                pieces.append(self._safe_public_text(json.dumps(content, ensure_ascii=False, default=str), limit=4000))
            body = "\n".join(piece for piece in pieces if piece).strip()
        else:
            body = self._safe_public_text(str(content or ""), limit=4000)
        if not body:
            body = f"({self._source_label()}没有返回可读内容。)"
        if bool(getattr(result, "is_error", False)):
            status = self._safe_public_text(getattr(result, "status", ""), limit=80) or "error"
            reason = self._safe_public_text(getattr(result, "reason", ""), limit=120)
            label = f"{status}/{reason}" if reason else status
            return f"{self._source_label()}返回业务错误（{label}）：\n{body[: self.MAX_FOLLOWUP_CHARS]}"
        return f"{self._source_label()}返回：\n{body[: self.MAX_FOLLOWUP_CHARS]}"

    def _source_label(self) -> str:
        raw = getattr(self.descriptor, "raw", None)
        adapter_name = ""
        if isinstance(raw, Mapping):
            adapter_name = str(raw.get("adapter") or "").strip().lower()
        if not adapter_name:
            adapter_name = str(getattr(self.adapter, "type", "") or "").strip().lower()
        if adapter_name == "python":
            return "本地 Python 能力"
        if "mcp" in adapter_name:
            return "本地 MCP 工具"
        if adapter_name == "plugin":
            return "已安装插件能力"
        return "本地 adapter 能力"

    def _schema_prompt_text(self) -> str:
        schema = self._input_schema()
        properties = schema.get("properties") if isinstance(schema.get("properties"), Mapping) else {}
        required = set(schema.get("required") or []) if isinstance(schema.get("required"), list) else set()
        parts: list[str] = []
        for name, prop in list(properties.items())[:12]:
            clean_name = self._safe_key(name)
            if not clean_name:
                continue
            prop = prop if isinstance(prop, Mapping) else {}
            prop_type = self._safe_key(prop.get("type")) or "string"
            mark = " required" if clean_name in required else ""
            desc = self._safe_public_text(prop.get("description"), limit=80)
            parts.append(f"{clean_name}:{prop_type}{mark}{(' - ' + desc) if desc else ''}")
        return "; ".join(parts)

    def _input_schema(self) -> Mapping[str, Any]:
        try:
            return capcore_build_tool_spec(self.descriptor).input_schema
        except Exception:
            return {"type": "object", "properties": {}, "required": [], "additionalProperties": False}

    def _safe_payload_preview(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            return {}
        preview: dict[str, Any] = {}
        for key, item in list(value.items())[:12]:
            clean_key = self._safe_key(key)
            if clean_key:
                preview[clean_key] = self._safe_arg_value(item)
        return preview

    def _safe_arg_value(self, value: Any) -> Any:
        if isinstance(value, bool) or isinstance(value, (int, float)):
            return value
        if isinstance(value, str):
            return self._safe_public_text(value, limit=500)
        if isinstance(value, list):
            return [self._safe_arg_value(item) for item in value[:12]]
        if isinstance(value, Mapping):
            return self._safe_payload_preview(value)
        return self._safe_public_text(str(value), limit=200)

    def _safe_key(self, value: Any) -> str:
        text = str(value or "").strip()
        if not re.fullmatch(r"^[A-Za-z0-9_.-]{1,80}$", text):
            return ""
        return text

    def _safe_public_text(self, value: Any, *, limit: int) -> str:
        text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
        text = re.sub(
            r"(?i)\b(api[_-]?key|authorization|bearer|cookie|password|secret|token)\s*[:=]\s*[^\s,;]+",
            r"\1=[redacted]",
            text,
        )
        text = re.sub(r"(?i)\bbearer\s+[^\s]+", "Bearer [redacted]", text)
        return re.sub(r"\s+", " ", text).strip()[:limit]

    def _run_coro_blocking(self, awaitable: Any) -> Any:
        if not inspect.isawaitable(awaitable):
            return awaitable
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(awaitable)
        result_box: dict[str, Any] = {}
        error_box: dict[str, BaseException] = {}

        def runner() -> None:
            try:
                result_box["result"] = asyncio.run(awaitable)
            except BaseException as exc:
                error_box["error"] = exc

        thread = threading.Thread(target=runner, daemon=True)
        thread.start()
        thread.join()
        if error_box:
            raise error_box["error"]
        return result_box.get("result")
