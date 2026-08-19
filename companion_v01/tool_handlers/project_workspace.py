from __future__ import annotations

import json
from typing import Any, Mapping

from ..project_workspace import ProjectWorkspaceError, ProjectWorkspaceService
from ..project_workspace_specs import (
    MANAGE_PROJECT_WORKSPACE_TOOL_SPEC,
    WORKSPACE_PATCH_TOOL_SPEC,
    WORKSPACE_WRITE_TOOL_SPEC,
)
from .core import BaseToolHandler, ToolExecutionContext, ToolExecutionResult, ToolFollowupEnvelope


class _ProjectWorkspaceHandler(BaseToolHandler):
    policy_accepted_native_tool = True

    def __init__(self, *, service: ProjectWorkspaceService) -> None:
        self.service = service

    def _scope(self, context: ToolExecutionContext):
        request_context = context.request_context if isinstance(context.request_context, dict) else {}
        return self.service.scope_for(
            profile_user_id=context.profile_user_id,
            session_id=context.session_id,
            client_mode=context.client_mode,
            actor_stable_id=str(request_context.get("actor_stable_id") or ""),
        )

    def _result(self, payload: Mapping[str, Any]) -> ToolExecutionResult:
        data = dict(payload)
        status = str(data.get("status") or "failed")
        reason = str(data.get("reason") or "")
        ok = status in {"ok", "succeeded"}
        content = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        if ok:
            feedback = f"项目工作区操作已完成。实际返回数据：{content}"
        else:
            feedback = (
                f"项目工作区操作没有完成（reason={reason or status}）。实际返回数据：{content}。"
                "请依据 reason 调整；不要声称文件或项目已经修改。"
            )
        event = {
            "type": "project_workspace_result",
            "tool_type": self.tool_type,
            "status": "succeeded" if ok else status,
        }
        if reason:
            event["reason"] = reason
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=[event],
            followup_context=feedback,
            followup_envelope=ToolFollowupEnvelope(content=feedback, producer_bounded=True, complete=True),
            state_updates={"project_workspace": data},
        )

    def _execute(self, operation) -> ToolExecutionResult:
        try:
            return self._result(operation())
        except ProjectWorkspaceError as exc:
            return self._result({"status": "rejected", "reason": exc.reason, **exc.details})
        except Exception:
            return self._result({"status": "failed", "reason": "project_workspace_internal_error"})


class ManageProjectWorkspaceToolHandler(_ProjectWorkspaceHandler):
    tool_type = "manage_project_workspace"

    def tool_spec(self):
        return MANAGE_PROJECT_WORKSPACE_TOOL_SPEC

    def build_prompt_instruction(self) -> str:
        return (
            "- manage_project_workspace：管理持久编程项目。开始多文件/可执行项目时先 current/list；"
            "没有合适项目就 create，继续旧项目时按 workspace_id select。选中后 Shell 的 cwd 使用 alias:project。"
            "archive 只归档，不删除项目文件。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict) or str(value.get("type") or "") != self.tool_type:
            return None
        action = str(value.get("action") or "").strip()
        if action not in {"list", "create", "select", "archive", "current"}:
            return None
        normalized = {"type": self.tool_type, "action": action}
        if action == "create":
            name = str(value.get("display_name") or "").strip()
            if not name:
                return None
            normalized["display_name"] = name
        if action in {"select", "archive"}:
            workspace_id = str(value.get("workspace_id") or "").strip()
            if not workspace_id:
                return None
            normalized["workspace_id"] = workspace_id
        if action == "list":
            normalized["include_archived"] = bool(value.get("include_archived"))
        return normalized

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        scope = lambda: self._scope(context)
        action = str(call.get("action") or "")
        if action == "create":
            return self._execute(lambda: {"status": "succeeded", **self.service.create(scope=scope(), display_name=call["display_name"])})
        if action == "select":
            return self._execute(lambda: {"status": "succeeded", **self.service.select(scope=scope(), workspace_id=call["workspace_id"])})
        if action == "archive":
            return self._execute(lambda: {"status": "succeeded", **self.service.archive(scope=scope(), workspace_id=call["workspace_id"])})
        if action == "current":
            return self._execute(
                lambda: {
                    "status": "succeeded",
                    "workspace": self.service.current(scope=scope()),
                }
            )
        return self._execute(lambda: self.service.list(scope=scope(), include_archived=bool(call.get("include_archived"))))


class WorkspaceWriteToolHandler(_ProjectWorkspaceHandler):
    tool_type = "workspace_write"

    def tool_spec(self):
        return WORKSPACE_WRITE_TOOL_SPEC

    def build_prompt_instruction(self) -> str:
        return (
            "- workspace_write：在当前持久项目内原子创建或替换一个 UTF-8 文件；只传项目相对 path。"
            "已有文件优先带 expected_sha256，冲突时重新读取再修改。源码不要经 Shell 命令传输。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict) or str(value.get("type") or "") != self.tool_type:
            return None
        path = str(value.get("path") or "").strip()
        if not path or "content" not in value:
            return None
        normalized = {
            "type": self.tool_type,
            "path": path,
            "content": str(value.get("content") or ""),
            "mode": str(value.get("mode") or "create_or_replace"),
        }
        for key in ("workspace_id", "expected_sha256"):
            if value.get(key):
                normalized[key] = str(value.get(key)).strip()
        return normalized

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        return self._execute(
            lambda: self.service.write(
                scope=self._scope(context),
                workspace_id=str(call.get("workspace_id") or ""),
                path=str(call.get("path") or ""),
                content=str(call.get("content") or ""),
                expected_sha256=str(call.get("expected_sha256") or ""),
                mode=str(call.get("mode") or "create_or_replace"),
            )
        )


class WorkspacePatchToolHandler(_ProjectWorkspaceHandler):
    tool_type = "workspace_patch"

    def tool_spec(self):
        return WORKSPACE_PATCH_TOOL_SPEC

    def build_prompt_instruction(self) -> str:
        return (
            "- workspace_patch：对当前持久项目中的既有 UTF-8 文件应用 unified diff。"
            "所有 hunk 先校验再提交；失败时不会留下半应用结果。可用 expected_files 绑定每个文件的旧 sha256。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict) or str(value.get("type") or "") != self.tool_type:
            return None
        patch_text = str(value.get("patch") or "")
        if not patch_text.strip():
            return None
        expected = value.get("expected_files")
        if expected is not None and not isinstance(expected, dict):
            return None
        normalized = {
            "type": self.tool_type,
            "patch": patch_text,
            "expected_files": {str(key): str(item) for key, item in dict(expected or {}).items()},
        }
        if value.get("workspace_id"):
            normalized["workspace_id"] = str(value.get("workspace_id")).strip()
        return normalized

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        return self._execute(
            lambda: self.service.patch(
                scope=self._scope(context),
                workspace_id=str(call.get("workspace_id") or ""),
                patch_text=str(call.get("patch") or ""),
                expected_files=dict(call.get("expected_files") or {}),
            )
        )


__all__ = [
    "ManageProjectWorkspaceToolHandler",
    "WorkspacePatchToolHandler",
    "WorkspaceWriteToolHandler",
]
