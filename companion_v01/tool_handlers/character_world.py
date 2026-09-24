"""Character-pack context tools.

Legacy NPC, gift, artifact and persona-card controls were retired from the
model tool surface. Character-pack libraries remain the current opt-in source
for extra character context.
"""

from __future__ import annotations

import re
from typing import Any

from ..capability_registry import LOAD_CHARACTER_CONTEXT_TOOL_SPEC
from ..text_utils import normalize_text
from .core import BaseToolHandler, ToolExecutionContext, ToolExecutionResult, ToolFollowupEnvelope


class LoadCharacterContextToolHandler(BaseToolHandler):
    tool_type = "load_character_context"

    def __init__(self, *, context_library_service: Any) -> None:
        self.context_library_service = context_library_service

    def tool_spec(self):
        return LOAD_CHARACTER_CONTEXT_TOOL_SPEC

    def build_prompt_instruction(self) -> str:
        return ""

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict) or str(value.get("type") or "").strip() != self.tool_type:
            return None
        raw_targets = value.get("targets")
        if raw_targets is None:
            raw_targets = value.get("files")
        if isinstance(raw_targets, str):
            candidates = [part.strip() for part in re.split(r"[,，;；、\n]+", raw_targets) if part.strip()]
        elif isinstance(raw_targets, (list, tuple, set)):
            candidates = [str(item or "").strip() for item in raw_targets]
        else:
            candidates = []
        targets: list[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            target = normalize_text(candidate).strip()
            if not target or target in seen:
                continue
            seen.add(target)
            targets.append(target)
        if not targets:
            return None
        return {"type": self.tool_type, "targets": targets}

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        service = self.context_library_service
        if service is None:
            return ToolExecutionResult(
                tool_type=self.tool_type,
                followup_context=(
                    "【角色资料读取结果】\n"
                    "status=unavailable\nreason=context_library_service_unavailable\n"
                    "请不要猜测缺失的角色设定，基于当前已经可见的信息自然回应。"
                ),
                state_updates={
                    "character_context": {
                        "status": "unavailable",
                        "loaded": [],
                        "failed": list(call.get("targets") or []),
                    }
                },
            )
        result = service.load_context(
            str(context.character_pack_id or ""),
            list(call.get("targets") or []),
        )
        loaded_targets = [
            str(item.get("target") or "")
            for item in result.get("loaded") or []
            if isinstance(item, dict) and str(item.get("target") or "")
        ]
        failed_targets = [
            {
                "target": str(item.get("target") or ""),
                "status": str(item.get("status") or "unavailable"),
                "reason": str(item.get("reason") or ""),
            }
            for item in result.get("failed") or []
            if isinstance(item, dict)
        ]
        followup_text = str(result.get("followup_context") or "")
        return ToolExecutionResult(
            tool_type=self.tool_type,
            followup_context=followup_text,
            followup_envelope=ToolFollowupEnvelope(
                content=followup_text,
                producer_bounded=True,
                complete=True,
                continuation=None,
                diagnostics={"loaded": len(loaded_targets), "failed": len(failed_targets)},
            ),
            state_updates={
                "character_context": {
                    "status": str(result.get("status") or "unavailable"),
                    "loaded": loaded_targets,
                    "failed": failed_targets,
                }
            },
        )
