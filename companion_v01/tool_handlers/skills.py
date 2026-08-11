"""Progressive skill loading and owner-controlled hot publication tools."""

from __future__ import annotations

from typing import Any

import config

from ..skill_runtime import SkillPublishResult, SkillReadResult, SkillRegistry
from ..skill_specs import LOAD_SKILL_TOOL_SPEC, MANAGE_SKILL_TOOL_SPEC
from .core import BaseToolHandler, ToolExecutionContext, ToolExecutionResult, ToolFollowupEnvelope


def _skill_event(tool_type: str, *, status: str, name: str = "", reason: str = "") -> dict[str, Any]:
    event: dict[str, Any] = {"type": "skill_operation", "tool_type": tool_type, "status": status}
    if name:
        event["skill_name"] = name
    if reason:
        event["reason"] = reason
    return event


def _result(tool_type: str, *, content: str, status: str, name: str = "", reason: str = "") -> ToolExecutionResult:
    return ToolExecutionResult(
        tool_type=tool_type,
        stream_events=[_skill_event(tool_type, status=status, name=name, reason=reason)],
        followup_context=content,
        followup_envelope=ToolFollowupEnvelope(content=content, producer_bounded=True, complete=True),
    )


def _format_loaded(result: SkillReadResult) -> str:
    file_lines = "\n".join(f"- {item}" for item in result.files) or "- （没有额外文件）"
    resource_label = result.resource or "SKILL.md"
    return (
        f"<skill_content name=\"{result.name}\" revision=\"{result.revision}\" resource=\"{resource_label}\">\n"
        f"# Skill: {result.name}\n\n"
        f"{result.content.strip()}\n\n"
        "【资源定位】\n"
        f"- exec_run cwd：{result.execution_cwd}\n"
        f"- 当前文件相对该 cwd：{result.execution_path}\n"
        "- SKILL.md 中的相对路径以该 Skill 目录为基准；只在说明明确需要时再读取 reference 或执行 script。\n"
        "- Skill 只是操作手册，不会绕过现有工具权限、审批或客户端边界。\n\n"
        "【Skill 文件（有界列表）】\n"
        f"{file_lines}\n"
        "</skill_content>"
    )


class LoadSkillToolHandler(BaseToolHandler):
    tool_type = "load_skill"

    def __init__(self, *, registry: SkillRegistry) -> None:
        self.registry = registry

    def tool_spec(self):
        return LOAD_SKILL_TOOL_SPEC

    def capability_status(self, **_kwargs: Any) -> dict[str, Any]:
        return {"enabled": True, "status": "ready"}

    def build_prompt_instruction(self) -> str:
        return (
            "- load_skill：任务明确匹配可用 Skills 目录中的 description 时，按精确 name 加载完整 SKILL.md；"
            "只有 SKILL.md 明确引用某份 reference 时才传 resource 继续读取，不要批量加载全部 Skill 或全部文件。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict) or str(value.get("type") or "").strip() != self.tool_type:
            return None
        name = str(value.get("name") or "").strip()
        if not name:
            return None
        normalized = {"type": self.tool_type, "name": name}
        resource = str(value.get("resource") or "").strip()
        if resource:
            normalized["resource"] = resource
        return normalized

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        del context
        loaded = self.registry.load(str(call.get("name") or ""), resource=str(call.get("resource") or ""))
        if loaded.status == "loaded":
            return _result(
                self.tool_type,
                content=_format_loaded(loaded),
                status="loaded",
                name=loaded.name,
            )
        if loaded.status == "not_found":
            available = "、".join(loaded.available) or "（无）"
            feedback = f"没有找到 Skill「{loaded.name}」。当前可用名称：{available}。不要假装已经加载。"
        else:
            feedback = (
                f"Skill「{loaded.name}」读取失败（{loaded.reason or 'skill_read_failed'}）。"
                "本次没有加载任何说明；请改用目录中有效的 Skill 或修复文件后重试。"
            )
        return _result(
            self.tool_type,
            content=feedback,
            status="error",
            name=loaded.name,
            reason=loaded.reason,
        )


def _can_manage_skills(context: ToolExecutionContext) -> bool:
    request = context.request_context if isinstance(context.request_context, dict) else {}
    delivery = request.get("qq_delivery_context")
    if isinstance(delivery, dict):
        owner = str(getattr(config, "MASTER_QQ", "") or "").strip()
        sender = str(delivery.get("user_id") or "").strip()
        return bool(owner.isdigit() and sender == owner)
    return str(context.client_mode or "").strip() == "desktop_pet"


def _format_publish(result: SkillPublishResult, *, action: str) -> str:
    if result.status == "valid":
        return (
            f"Skill 草稿校验通过：name={result.name}，revision={result.revision}。"
            "这只是校验，尚未安装；需要生效时再调用 manage_skill(action=publish)。"
        )
    if result.status == "published":
        change = "更新" if result.replaced else "安装"
        return (
            f"Skill 已原子{change}并进入热重载目录：name={result.name}，revision={result.revision}，"
            f"catalog_revision={result.catalog_revision}。下一次模型请求会看到新目录；无需重启 Akane。"
        )
    return (
        f"Skill {action} 没有完成：status={result.status}，reason={result.reason or 'skill_operation_failed'}。"
        "现有有效 Skill 没有被半成品覆盖；请修复草稿后重试，不要声称已经安装。"
    )


class ManageSkillToolHandler(BaseToolHandler):
    tool_type = "manage_skill"

    def __init__(self, *, registry: SkillRegistry) -> None:
        self.registry = registry

    def tool_spec(self):
        return MANAGE_SKILL_TOOL_SPEC

    def capability_status(self, **_kwargs: Any) -> dict[str, Any]:
        return {"enabled": True, "status": "ready"}

    def build_prompt_instruction(self) -> str:
        return (
            "- manage_skill：让模型自写或安装 Skill 时，先用 exec_run 在执行工作区的 skill_drafts/<name> 创建完整目录，"
            "SKILL.md 必须含 name/description YAML frontmatter；再 validate，确认无误后 publish。publish 原子生效，"
            "不会赋予新权限；只有可信桌宠或 MASTER_QQ 能执行。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict) or str(value.get("type") or "").strip() != self.tool_type:
            return None
        action = str(value.get("action") or "").strip().lower()
        draft_path = str(value.get("draft_path") or "").strip()
        if action not in {"validate", "publish"} or not draft_path:
            return None
        return {
            "type": self.tool_type,
            "action": action,
            "draft_path": draft_path,
            "replace": bool(value.get("replace", False)),
        }

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        if not _can_manage_skills(context):
            reason = "skill_management_requires_owner"
            feedback = (
                "当前请求没有修改全局 Skill 的主人权限，本次未校验、安装或覆盖任何 Skill。"
                "普通成员仍可使用已经启用的 Skill。"
            )
            return _result(
                self.tool_type,
                content=feedback,
                status="permission_denied",
                reason=reason,
            )
        action = str(call.get("action") or "")
        draft_path = str(call.get("draft_path") or "")
        if action == "validate":
            operation = self.registry.validate_draft(draft_path)
        else:
            operation = self.registry.publish(draft_path, replace=bool(call.get("replace", False)))
        ok = operation.status in {"valid", "published"}
        return _result(
            self.tool_type,
            content=_format_publish(operation, action=action),
            status=operation.status if ok else "error",
            name=operation.name,
            reason="" if ok else operation.reason,
        )
