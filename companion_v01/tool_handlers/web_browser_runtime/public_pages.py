"""Public page requests through the executor broker."""

from __future__ import annotations

import json
from typing import Any

from ...text_utils import normalize_text
from ..core import ToolExecutionContext, ToolExecutionResult, ToolMetadata
from .common import normalize_http_url


class OpenBrowserMixin:
    def capability_status(self, **_kwargs: Any) -> dict[str, Any]:
        if self._offer_source is None:
            return {"enabled": False, "status": "unavailable", "reason": "satellite_not_configured"}
        try:
            ready = self._offer_source.resolve_receipt(self.tool_spec()) is not None
        except Exception:
            ready = False
        return {
            "enabled": ready,
            "status": "ready" if ready else "unavailable",
            "reason": "" if ready else "satellite_offline",
        }

    def tool_metadata(self) -> ToolMetadata:
        spec = self.tool_spec()
        return ToolMetadata(
            family="browser_control",
            operation="control",
            risk=spec.risk,
            default_round_budget=6,
            input_schema=spec.input_schema,
            requires_confirmation=spec.confirm != "never",
        )

    def build_prompt_instruction(self) -> str:
        spec = self.tool_spec()
        return (
            f"- {spec.capability_id}：{spec.description}"
            f"调用参数遵循：{json.dumps(spec.input_schema, ensure_ascii=False, sort_keys=True)}"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        if str(value.get("type") or "").strip() != self.tool_type:
            return None
        url = self._normalize_public_url(value.get("url") or value.get("link") or value.get("href"))
        if not url:
            return None
        label = normalize_text(str(value.get("label") or value.get("title") or "")).strip()
        reason = normalize_text(str(value.get("reason") or "")).strip()
        return {
            "type": self.tool_type,
            "url": url,
            "label": label[:80],
            "reason": reason[:120],
        }

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        del call, context
        raise RuntimeError("open_browser_requires_executor_broker")

    def _normalize_public_url(self, value: Any) -> str:
        return normalize_http_url(
            value, is_private_host=self._is_private_or_local_host,
        )
