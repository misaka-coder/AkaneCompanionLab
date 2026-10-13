"""QQAdministrationCommands: command behavior and domain-specific reply formatting."""
from __future__ import annotations

from typing import Any, Callable, Mapping

import config
from companion_v01.channels.qq.context import QQMessageContext


class QQAdministrationCommands:
    """Own this command domain; host operations use the supplied gateway port."""

    def __init__(self, *, gateway: Any) -> None:
        self.gateway = gateway

    def handle_private_memory_command(
        self,
        context: QQMessageContext,
        *,
        command: dict[str, Any] | None = None,
        available: bool,
        queue_status=None,
    ) -> dict[str, Any] | None:
        def result(ok, status, reply):
            return {"handled": True, "ok": ok, "status": status, "reply": reply}
        if command is None:
            from companion_v01.channels.qq.commands.parser import resolve_qq_command
            res = resolve_qq_command(context.clean_message)
            if res is None or res.definition.kind != "private_memory":
                return None
            if res.error:
                return result(False, "invalid", res.error)
            payload = res.payload or {}
        else:
            payload = command
        if not context.is_group or context.group_id <= 0:
            return result(False, "group_only", "请在要接收记忆的群内使用。")
        enabled, _ = self.gateway._preferences.private_memory_policy(context.group_id)
        action = payload.get("action")
        if action == "status":
            state = "已开启" if enabled else "已关闭"
            suffix = "宿主功能尚未启用。" if not available else ""
            if queue_status and str(context.user_id) == self.gateway.master_qq:
                if queue_status.get("unavailable"):
                    suffix += "待写入状态暂时无法读取。"
                elif queue_status.get("pending") or queue_status.get("retry_pending"):
                    suffix += f"待写入 {queue_status['pending']} 轮，重试 {queue_status['retry_pending']} 轮。"
                    if queue_status.get("reason"):
                        suffix += f"最近重试原因：{queue_status['reason']}。"
            return result(True, "current", f"本群自动接收私人记忆{state}。{suffix}")
        if not self.gateway.master_qq or str(context.user_id) != self.gateway.master_qq:
            return result(False, "forbidden", "只有主人可以修改本群私人记忆接收开关。")
        if action == "on" and not available:
            return result(False, "unavailable", "请先在记忆设置中启用主人私人交流写入群记忆，并重启后端。")
        if not self.gateway._preferences.set_private_memory_enabled(context.group_id, action == "on"):
            return result(False, "persist_failed", "设置保存失败，原接收状态已保留。")
        return result(True, "enabled" if action == "on" else "disabled", (
            "本群将接收此后完成的主人私人交流，不复制工具和文件。" if action == "on"
            else "本群已停止自动接收，已写入的记忆保留。"
        ))

    def handle_group_vision_command(
        self,
        context: QQMessageContext,
        *,
        command: dict[str, Any] | None = None,
        sender_role: str = "",
    ) -> dict[str, Any] | None:
        if command is None:
            command = self.gateway.parse_group_vision_command(context.clean_message)
        if command is None:
            return None
        if not context.is_group or context.group_id <= 0:
            return {
                "handled": True,
                "ok": False,
                "status": "group_only",
                "reply": "请在群内设置识图模式",
                "vision_enabled": True,
            }

        vision_enabled = self.gateway.is_group_vision_enabled(context.group_id)
        action = str(command.get("action") or "")
        if action == "status":
            return {
                "handled": True,
                "ok": True,
                "status": "current",
                "reply": f"本群识图模式已{'开启' if vision_enabled else '关闭'}。",
                "vision_enabled": vision_enabled,
            }
        role = str(sender_role or "").strip().lower()
        is_master = bool(self.gateway.master_qq) and str(context.user_id) == self.gateway.master_qq
        if role not in {"owner", "admin"} and not is_master:
            return {
                "handled": True,
                "ok": False,
                "status": "forbidden",
                "reply": "只有群主、群管理员或 Akane 主账号可以修改本群识图开关。",
                "vision_enabled": vision_enabled,
            }

        requested_enabled = action == "enable"
        state_persisted = self.gateway.set_group_vision_enabled(context.group_id, requested_enabled)
        if requested_enabled:
            reply = "识图模式已打开"
            status = "enabled"
        else:
            reply = "识图模式已关闭"
            status = "disabled"
        return {
            "handled": True,
            "ok": True,
            "status": status,
            "reply": self.gateway._append_state_persistence_warning(reply, state_persisted),
            "vision_enabled": requested_enabled,
            "state_persisted": state_persisted,
        }

    def handle_group_emotion_command(
        self,
        context: QQMessageContext,
        *,
        command: dict[str, Any] | None = None,
        sender_role: str = "",
    ) -> dict[str, Any] | None:
        if command is None:
            command = self.gateway.parse_group_emotion_command(context.clean_message)
        if command is None:
            return None
        if not context.is_group or context.group_id <= 0:
            return {
                "handled": True,
                "ok": False,
                "status": "group_only",
                "reply": "请在群内设置 emotion 图片投递开关。",
                "emotion_enabled": True,
            }
        enabled = self.gateway.is_group_emotion_enabled(context.group_id)
        role = str(sender_role or "").strip().lower()
        is_master = bool(self.gateway.master_qq) and str(context.user_id) == self.gateway.master_qq
        if role not in {"owner", "admin"} and not is_master:
            return {
                "handled": True,
                "ok": False,
                "status": "forbidden",
                "reply": "只有群主、群管理员或 Akane 主账号可以修改本群 emotion 图片开关。",
                "emotion_enabled": enabled,
            }
        action = str(command.get("action") or "status")
        if action == "status":
            return {
                "handled": True,
                "ok": True,
                "status": "current",
                "reply": f"本群 emotion 图片投递已{'开启' if enabled else '关闭'}。模型仍会正常生成 emotion，桌宠表情不受影响。",
                "emotion_enabled": enabled,
            }
        requested_enabled = action == "on"
        state_persisted = self.gateway.set_group_emotion_enabled(context.group_id, requested_enabled)
        reply = (
            "本群 emotion 图片投递已打开。模型 emotion 和桌宠表情保持不变。"
            if requested_enabled
            else "本群 emotion 图片投递已关闭。模型仍会正常生成 emotion，桌宠表情不受影响。"
        )
        return {
            "handled": True,
            "ok": True,
            "status": "enabled" if requested_enabled else "disabled",
            "reply": self.gateway._append_state_persistence_warning(reply, state_persisted),
            "emotion_enabled": requested_enabled,
            "state_persisted": state_persisted,
        }

    def handle_group_attention_command(
        self,
        context: QQMessageContext,
        *,
        command: dict[str, Any] | None = None,
        sender_role: str = "",
        default_mode: str = "engaged",
    ) -> dict[str, Any] | None:
        if command is None:
            command = self.gateway.parse_group_attention_command(context.clean_message)
        if command is None:
            return None
        if not context.is_group or context.group_id <= 0:
            return {"handled": True, "ok": False, "status": "group_only", "reply": "请在群内设置注意力模式。"}
        current = self.gateway.resolve_group_attention_mode(context.group_id, default=default_mode)
        action = str(command.get("action") or "status")
        if action == "status":
            return {
                "handled": True,
                "ok": True,
                "status": "current",
                "reply": f"本群注意力模式为 {current}。普通聊天的真实 @ 和引用本 Bot 不受该模式影响；群指令请真实 @ 目标 Bot 后发送。",
                "attention_mode": current,
            }
        role = str(sender_role or "").strip().lower()
        is_master = bool(self.gateway.master_qq) and str(context.user_id) == self.gateway.master_qq
        if role not in {"owner", "admin"} and not is_master:
            return {
                "handled": True,
                "ok": False,
                "status": "forbidden",
                "reply": "只有群主、群管理员或 Akane 主账号可以修改本群注意力模式。",
                "attention_mode": current,
            }
        state_persisted = self.gateway.set_group_attention_mode(context.group_id, action, default=default_mode)
        descriptions = {
            "off": "普通消息不触发注意力判断",
            "engaged": "仅在 Akane 刚参与后的活跃窗口内判断普通消息",
            "adaptive": "活跃窗口外也允许低频环境观察",
        }
        reply = f"本群注意力模式已设为 {action}：{descriptions[action]}。"
        return {
            "handled": True,
            "ok": True,
            "status": "updated",
            "reply": self.gateway._append_state_persistence_warning(reply, state_persisted),
            "attention_mode": action,
            "state_persisted": state_persisted,
        }

    def handle_access_permission_command(
        self,
        context: QQMessageContext,
        *,
        command: dict[str, str] | None = None,
        current_modes: Mapping[str, str] | None = None,
        apply_modes: Callable[[dict[str, str]], Mapping[str, str]] | None = None,
    ) -> dict[str, Any] | None:
        command = command or self.gateway.parse_access_permission_command(context.clean_message)
        if command is None:
            return None
        master_qq = self.gateway._safe_int(getattr(config, "MASTER_QQ", 0))
        if master_qq <= 0 or int(context.user_id or 0) != master_qq:
            return {
                "handled": True,
                "ok": False,
                "status": "forbidden",
                "reply": "只有 Akane 主人账号可以查看或修改访问权限。",
            }
        modes = dict(current_modes or {})
        for family_id in ("ops", "extensions"):
            if modes.get(family_id) not in {"trusted_auto_allow", "ask_each_time", "disabled"}:
                modes[family_id] = "ask_each_time"
        scope = str(command.get("scope") or "all")
        action = str(command.get("action") or "status")
        labels = {
            "trusted_auto_allow": "直接允许",
            "ask_each_time": "每次询问",
            "disabled": "关闭",
        }
        if action == "status":
            lines = ["Akane 访问权限："]
            if scope in {"all", "ops"}:
                lines.append(f"ops 本机与外部操作：{labels[modes['ops']]}")
            if scope in {"all", "extensions"}:
                lines.append(f"extensions 扩展管理：{labels[modes['extensions']]}")
            lines.append("设置：/权限 本机|扩展|全部 开|询问|关")
            lines.append("审批：/审批 · /批准 [编号] · /拒绝 [编号]")
            lines.append("群聊请先真实 @ 目标 Bot 再发送指令；私聊可直接发送。")
            return {"handled": True, "ok": True, "status": "current", "reply": "\n".join(lines), "modes": modes}
        target_mode = {"on": "trusted_auto_allow", "ask": "ask_each_time", "off": "disabled"}.get(action)
        if not target_mode or scope not in {"ops", "extensions", "all"}:
            return {"handled": True, "ok": False, "status": "invalid_action", "reply": "用法：/权限 本机|扩展|全部 开|询问|关"}
        updates = {
            family_id: target_mode
            for family_id in (("ops", "extensions") if scope == "all" else (scope,))
        }
        if apply_modes is None:
            return {"handled": True, "ok": False, "status": "runtime_update_unavailable", "reply": "当前运行环境不能保存访问权限。"}
        try:
            applied = dict(apply_modes(updates) or {})
        except Exception:
            return {"handled": True, "ok": False, "status": "runtime_update_failed", "reply": "访问权限保存失败，原配置保持不变。"}
        if any(applied.get(key) != value for key, value in updates.items()):
            return {"handled": True, "ok": False, "status": "runtime_update_mismatch", "reply": "访问权限没有完整保存，原配置保持不变。"}
        scope_label = {"ops": "本机与外部操作", "extensions": "扩展管理", "all": "全部两组权限"}[scope]
        return {
            "handled": True,
            "ok": True,
            "status": "saved",
            "reply": f"{scope_label}已设为：{labels[target_mode]}。",
            "modes": {**modes, **updates},
        }

    def handle_capability_approval_command(
        self,
        context: QQMessageContext,
        *,
        command: dict[str, str] | None = None,
        approval_store: Any = None,
    ) -> dict[str, Any] | None:
        command = command or self.gateway.parse_capability_approval_command(context.clean_message)
        if command is None:
            return None
        master_qq = self.gateway._safe_int(getattr(config, "MASTER_QQ", 0))
        if master_qq <= 0 or int(context.user_id or 0) != master_qq:
            return {
                "handled": True,
                "ok": False,
                "status": "forbidden",
                "reply": "只有 Akane 主人账号可以查看或处理能力审批。",
            }
        if approval_store is None:
            return {
                "handled": True,
                "ok": False,
                "status": "unavailable",
                "reply": "能力审批服务当前不可用。",
            }
        pending = approval_store.list_requests(
            profile_user_id=context.profile_user_id,
            include_resolved=False,
            limit=20,
        )
        requests = [item for item in list(pending.get("approvalRequests") or []) if isinstance(item, dict)]
        action = str(command.get("action") or "")
        if action == "list":
            if not requests:
                reply = "当前没有待处理的能力审批。"
            else:
                lines = ["待处理的能力审批："]
                for index, item in enumerate(requests[:10], start=1):
                    request_id = str(item.get("requestId") or "")
                    capability_id = str(item.get("capabilityId") or item.get("actionId") or "能力")
                    lines.append(f"{index}. {capability_id} · {request_id[-8:]}")
                lines.append("使用 /批准 编号后8位 或 /拒绝 编号后8位。只有一项时可省略编号。")
                reply = "\n".join(lines)
            if context.is_group:
                reply += "\n群聊请先真实 @ 目标 Bot 再发送审批指令；私聊可直接发送。"
            return {"handled": True, "ok": True, "status": "listed", "reply": reply}

        selector = str(command.get("selector") or "").strip()
        selected: list[dict[str, Any]] = []
        if selector:
            selected = [
                item
                for item in requests
                if str(item.get("requestId") or "") == selector
                or str(item.get("requestId") or "").endswith(selector)
            ]
        elif len(requests) == 1:
            selected = requests
        if not selected:
            reason = "当前没有待处理的能力审批。" if not requests else "请指定唯一的审批编号；发送 /approvals 查看。"
            return {"handled": True, "ok": False, "status": "not_found", "reply": reason}
        if len(selected) != 1:
            return {
                "handled": True,
                "ok": False,
                "status": "ambiguous",
                "reply": "这个编号匹配到多项请求，请发送 /approvals 后使用更完整的编号。",
            }
        request_id = str(selected[0].get("requestId") or "")
        decision = "approved" if action == "approve" else "denied"
        result = approval_store.decide_request(
            profile_user_id=context.profile_user_id,
            request_id=request_id,
            payload={"decision": decision},
        )
        if not result.get("ok"):
            return {
                "handled": True,
                "ok": False,
                "status": str(result.get("status") or "failed"),
                "reply": "这项能力审批没有处理成功，可能已经过期或被处理。",
            }
        resume_binding_getter = getattr(approval_store, "get_resume_binding", None)
        resume_binding = (
            dict(resume_binding_getter(profile_user_id=context.profile_user_id, request_id=request_id) or {})
            if callable(resume_binding_getter)
            else {}
        )
        capability_id = str(selected[0].get("capabilityId") or "")
        action_id = str(selected[0].get("actionId") or "")
        capability_label = capability_id or action_id or "该能力"
        reply = (
            f"已批准 {capability_label}。本次授权只匹配原调用且会短时有效，Akane 正在自动续接。"
            if decision == "approved"
            else f"已拒绝 {capability_label}，原动作不会执行；Akane 会自动收到结果。"
        )
        return {
            "handled": True,
            "ok": True,
            "status": decision,
            "reply": reply,
            "request_id": request_id,
            "capability_id": capability_id,
            "action_id": action_id,
            "authorization_profile_user_id": str(
                resume_binding.get("authorizationProfileUserId") or ""
            ),
        }
