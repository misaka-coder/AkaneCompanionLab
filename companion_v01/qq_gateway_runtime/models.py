"""QQModelCommands: command behavior and domain-specific reply formatting."""
from __future__ import annotations
from typing import Any, Callable
import config
from .definitions import QQ_REPLY_MODE_LABELS
from .validation import (
    _safe_chat_model_id,
    _chat_model_route_prefix,
    _chat_model_base_id,
    _safe_reply_mode,
)
from .context import QQMessageContext
from .preferences import QQSessionPreferences


class QQModelCommands:
    """Own this command domain; host operations use the supplied gateway port."""

    def __init__(self, *, gateway: Any, preferences: QQSessionPreferences) -> None:
        self.gateway = gateway
        self.preferences = preferences

    def handle_chat_model_command(
        self,
        context: QQMessageContext,
        *,
        command: dict[str, str] | None = None,
        default_model: str = "",
        available_models: list[str] | None = None,
        list_error: str = "",
    ) -> dict[str, Any] | None:
        command = command or self.gateway.parse_chat_model_command(context.clean_message)
        if command is None:
            return None

        master_qq = self.gateway._safe_int(getattr(config, "MASTER_QQ", 0))
        if master_qq and int(context.user_id or 0) != master_qq:
            return {
                "handled": True,
                "ok": False,
                "status": "forbidden",
                "reply": "这个命令只允许主人使用。",
                "chat_model": self.gateway.resolve_chat_model(
                    context.session_id,
                    default_model=default_model,
                ),
                "chat_model_override": self.gateway.resolve_chat_model_override(context.session_id),
            }

        default_model_id = _safe_chat_model_id(default_model)
        active_override = self.gateway.resolve_chat_model_override(context.session_id)
        active_model = active_override or default_model_id
        action = str(command.get("action") or "")
        if action == "list":
            labels = self._ordered_chat_model_ids(
                available_models,
                # Numbering must keep the switch command's default-route order,
                # including after a session override changes the active model.
                preferred_model=default_model_id or active_model,
            )
            if labels:
                visible_labels = labels[:40]
                return {
                    "handled": True,
                    "ok": True,
                    "status": "listed",
                    "reply": self._build_numbered_model_reply(
                        visible_labels,
                        instruction=(
                            "切换：/模型 序号 或 /模型 基础模型名，"
                            "系统会优先选择当前默认分组。"
                        ),
                        overflow_count=max(0, len(labels) - 40),
                    ),
                    "chat_model": active_model,
                    "chat_model_override": active_override,
                    "available_models": labels,
                }
            reason = str(list_error or "").strip()
            reply = "当前供应商没有返回可用模型列表。"
            if reason:
                reply += f"\n原因：{reason}"
            reply += "\n仍可发送 /模型 模型名 手动切换。"
            return {
                "handled": True,
                "ok": False,
                "status": "model_list_unavailable",
                "reply": reply,
                "chat_model": active_model,
                "chat_model_override": active_override,
                "available_models": [],
            }
        if action == "current":
            return {
                "handled": True,
                "ok": True,
                "status": "current",
                "reply": self._build_current_chat_model_reply(
                    active_model,
                    has_override=bool(active_override),
                    default_model=default_model_id,
                ),
                "chat_model": active_model,
                "chat_model_override": active_override,
            }
        if action == "default":
            state_persisted = self.gateway.clear_session_chat_model_override(context.session_id)
            active_model = _safe_chat_model_id(default_model)
            reply = f"已恢复 QQ 默认聊天模型：{active_model or '未配置'}。"
            return {
                "handled": True,
                "ok": True,
                "status": "default",
                "reply": self.gateway._append_state_persistence_warning(reply, state_persisted),
                "chat_model": active_model,
                "chat_model_override": "",
                "state_persisted": state_persisted,
            }
        if action == "switch":
            requested_model = _safe_chat_model_id(command.get("model"))
            model_id, resolution_status, candidates = self._resolve_chat_model_choice(
                requested_model,
                available_models=available_models,
                preferred_model=default_model_id or active_model,
            )
            if not requested_model:
                return {
                    "handled": True,
                    "ok": False,
                    "status": "invalid_chat_model",
                    "reply": "这个模型名不太对。请发送 /模型 列表查看可用序号和完整模型名。",
                    "chat_model": active_model,
                    "chat_model_override": active_override,
                }
            if not model_id:
                if resolution_status == "index_out_of_range":
                    count = len(self._ordered_chat_model_ids(available_models, preferred_model=default_model_id or active_model))
                    reply = f"模型序号超出范围，当前可选范围是 1-{count}。请重新发送 /模型 列表查看。"
                elif resolution_status == "ambiguous":
                    reply = self.gateway._build_multiline_option_reply(
                        "这个基础模型名对应多个分组",
                        candidates[:8],
                        instruction="切换：/模型 完整名称，或发送 /模型 列表 后按序号切换。",
                        overflow_count=max(0, len(candidates) - 8),
                    )
                elif resolution_status == "model_list_unavailable":
                    reason = str(list_error or "").strip()
                    reply = "当前无法读取模型列表，因此不能按序号切换。请稍后再试，或发送完整模型名。"
                    if reason:
                        reply += f"\n原因：{reason}"
                else:
                    reply = (
                        f"当前供应商没有返回模型“{requested_model}”。"
                        "请发送 /模型 列表查看可用项，避免切到不存在的分组。"
                    )
                return {
                    "handled": True,
                    "ok": False,
                    "status": resolution_status or "model_unavailable",
                    "reply": reply,
                    "chat_model": active_model,
                    "chat_model_override": active_override,
                }
            state_persisted = self.gateway.set_session_chat_model_override(context.session_id, model_id)
            reply = f"已把当前 QQ 会话聊天模型切换为：{model_id}。"
            return {
                "handled": True,
                "ok": True,
                "status": "switched",
                "reply": self.gateway._append_state_persistence_warning(reply, state_persisted),
                "chat_model": model_id,
                "chat_model_override": model_id,
                "state_persisted": state_persisted,
            }
        return None

    def handle_thinking_mode_command(
        self,
        context: QQMessageContext,
        *,
        command: dict[str, str] | None = None,
        current_mode: str = "disabled",
        supported: bool = True,
        apply_mode: Callable[[str], str] | None = None,
    ) -> dict[str, Any] | None:
        command = command or self.gateway.parse_thinking_mode_command(context.clean_message)
        if command is None:
            return None

        normalized_current = str(current_mode or "").strip().lower()
        if normalized_current not in {"enabled", "disabled"}:
            normalized_current = "disabled"
        master_qq = self.gateway._safe_int(getattr(config, "MASTER_QQ", 0))
        if master_qq and int(context.user_id or 0) != master_qq:
            return {
                "handled": True,
                "ok": False,
                "status": "forbidden",
                "reply": "这个命令只允许主人使用。",
                "thinking_mode": normalized_current,
                "supported": supported,
            }

        action = str(command.get("action") or "")
        if action == "current":
            label = "开启" if normalized_current == "enabled" else "关闭"
            support_note = "" if supported else "\n当前聊天模型不支持 DeepSeek thinking 开关。"
            return {
                "handled": True,
                "ok": True,
                "status": "current",
                "reply": (
                    f"当前思考模式：{label}。{support_note}\n"
                    "切换：/思考 开 或 /思考 关。"
                ),
                "thinking_mode": normalized_current,
                "supported": supported,
            }

        target_mode = {"enable": "enabled", "disable": "disabled"}.get(action, "")
        if not target_mode:
            return {
                "handled": True,
                "ok": False,
                "status": "invalid_action",
                "reply": "这个思考模式指令暂时不支持。",
                "thinking_mode": normalized_current,
                "supported": supported,
            }
        if not supported:
            return {
                "handled": True,
                "ok": False,
                "status": "unsupported_current_model",
                "reply": "当前聊天模型不支持 DeepSeek thinking 开关，配置没有修改。",
                "thinking_mode": normalized_current,
                "supported": False,
            }
        if apply_mode is None:
            return {
                "handled": True,
                "ok": False,
                "status": "runtime_update_unavailable",
                "reply": "当前运行环境暂时不能切换思考模式，配置没有修改。",
                "thinking_mode": normalized_current,
                "supported": True,
            }
        try:
            applied_mode = str(apply_mode(target_mode) or "").strip().lower()
        except Exception:
            return {
                "handled": True,
                "ok": False,
                "status": "runtime_update_failed",
                "reply": "思考模式切换失败，原配置保持不变。",
                "thinking_mode": normalized_current,
                "supported": True,
            }
        if applied_mode != target_mode:
            return {
                "handled": True,
                "ok": False,
                "status": "runtime_update_mismatch",
                "reply": "思考模式没有切换成功，原配置保持不变。",
                "thinking_mode": normalized_current,
                "supported": True,
            }

        enabled = applied_mode == "enabled"
        return {
            "handled": True,
            "ok": True,
            "status": "enabled" if enabled else "disabled",
            "reply": (
                "已开启 DeepSeek 思考模式；从下一条消息开始生效，开启时不会发送温度参数。"
                if enabled
                else "已关闭 DeepSeek 思考模式；从下一条消息开始恢复非思考采样。"
            ),
            "thinking_mode": applied_mode,
            "supported": True,
            "state_persisted": True,
        }

    def handle_reply_mode_command(
        self,
        context: QQMessageContext,
        *,
        command: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if command is None:
            command = self.gateway.parse_reply_mode_command(context.clean_message)
        if command is None:
            return None

        action = str(command.get("action") or "")
        if action == "current":
            active_mode = self.gateway.resolve_reply_mode(context.session_id)
            return {
                "handled": True,
                "ok": True,
                "status": "current",
                "reply": self._build_current_reply_mode_reply(active_mode, session_id=context.session_id),
                "reply_mode": active_mode,
            }
        if action == "default":
            if not self.gateway.clear_session_reply_mode_override(context.session_id):
                return self._reply_mode_save_failed(context.session_id)
            active_mode = self.gateway.resolve_reply_mode(context.session_id)
            return {
                "handled": True,
                "ok": True,
                "status": "default",
                "reply": f"已恢复 QQ 默认回复模式：{self._format_reply_mode_label(active_mode)}。",
                "reply_mode": active_mode,
            }
        if action == "switch":
            reply_mode = _safe_reply_mode(command.get("reply_mode"), default="")
            if not reply_mode:
                return {
                    "handled": True,
                    "ok": False,
                    "status": "invalid_reply_mode",
                    "reply": "回复模式只能是文字模式、语音模式或双发模式。",
                    "reply_mode": self.gateway.resolve_reply_mode(context.session_id),
                }
            if not self.gateway.set_session_reply_mode(context.session_id, reply_mode):
                return self._reply_mode_save_failed(context.session_id)
            return {
                "handled": True,
                "ok": True,
                "status": "switched",
                "reply": f"已把当前 QQ 会话切到{self._format_reply_mode_label(reply_mode)}。",
                "reply_mode": reply_mode,
            }
        return None

    def _reply_mode_save_failed(self, session_id: str) -> dict[str, Any]:
        return {
            "handled": True, "ok": False, "status": "state_persist_failed",
            "reply": "回复模式未保存成功，当前设置保持不变。",
            "reply_mode": self.gateway.resolve_reply_mode(session_id),
            "reason": self.preferences._state_error or "state_persist_failed",
        }

    def _build_current_reply_mode_reply(self, active_mode: str, *, session_id: str = "") -> str:
        key = str(session_id or "").strip()
        with self.preferences._reply_mode_lock:
            has_override = bool(key and key in self.preferences.reply_mode_overrides)
        source = "本会话设置" if has_override else "QQ 默认配置"
        return f"当前 QQ 会话回复模式：{self._format_reply_mode_label(active_mode)}（来源：{source}）。"

    def _build_current_chat_model_reply(self, active_model: str, *, has_override: bool, default_model: str = "") -> str:
        source = "本会话临时切换" if has_override else "QQ 默认配置"
        lines = ["当前 QQ 会话聊天模型", "─" * 18]
        lines.append(f"  模型：{_safe_chat_model_id(active_model) or '未配置'}")
        lines.append(f"  来源：{source}")
        if has_override and _safe_chat_model_id(default_model):
            lines.append(f"  默认：{_safe_chat_model_id(default_model)}")
        return "\n".join(lines)

    def _ordered_chat_model_ids(
        self,
        available_models: list[str] | None,
        *,
        preferred_model: str = "",
    ) -> list[str]:
        models: list[str] = []
        seen: set[str] = set()
        for raw_model in list(available_models or []):
            model = _safe_chat_model_id(raw_model)
            if not model or model in seen:
                continue
            seen.add(model)
            models.append(model)
        preferred = _safe_chat_model_id(preferred_model)
        if preferred and all(model.casefold() != preferred.casefold() for model in models):
            # Some compatible providers accept a configured model while
            # omitting it from /models. The configured default is still a
            # verified route and must remain selectable/listed.
            models.append(preferred)
        preferred_prefix = _chat_model_route_prefix(preferred)
        return sorted(
            models,
            key=lambda model: (
                0 if preferred and model.casefold() == preferred.casefold() else 1,
                0
                if preferred_prefix and _chat_model_route_prefix(model).casefold() == preferred_prefix.casefold()
                else 1,
                _chat_model_base_id(model).casefold(),
                model.casefold(),
            ),
        )

    def _resolve_chat_model_choice(
        self,
        requested_model: str,
        *,
        available_models: list[str] | None,
        preferred_model: str = "",
    ) -> tuple[str, str, list[str]]:
        requested = _safe_chat_model_id(requested_model)
        if not requested:
            return "", "invalid_chat_model", []
        if available_models is None:
            if requested.isdecimal():
                return "", "model_list_unavailable", []
            return requested, "direct", []
        models = self._ordered_chat_model_ids(
            available_models,
            preferred_model=preferred_model,
        )
        if not models:
            if requested.isdecimal():
                return "", "model_list_unavailable", []
            return requested, "direct", []
        if requested.isdecimal():
            index = int(requested)
            if 1 <= index <= len(models):
                return models[index - 1], "index", []
            return "", "index_out_of_range", []
        for model in models:
            if model.casefold() == requested.casefold():
                return model, "exact", []
        if _chat_model_route_prefix(requested):
            return "", "model_unavailable", []
        candidates = [
            model
            for model in models
            if _chat_model_base_id(model).casefold() == requested.casefold()
        ]
        if len(candidates) == 1:
            return candidates[0], "unique_base_name", []
        preferred_prefix = _chat_model_route_prefix(preferred_model)
        preferred_candidates = [
            model
            for model in candidates
            if preferred_prefix
            and _chat_model_route_prefix(model).casefold() == preferred_prefix.casefold()
        ]
        if len(preferred_candidates) == 1:
            return preferred_candidates[0], "preferred_group", []
        if candidates:
            return "", "ambiguous", candidates
        return "", "model_unavailable", []

    def _build_numbered_model_reply(
        self,
        labels: list[str],
        *,
        instruction: str,
        overflow_count: int = 0,
    ) -> str:
        lines = ["当前供应商可用模型", "─" * 18]
        lines.extend(f"  {index}. {label}" for index, label in enumerate(labels, start=1))
        if overflow_count > 0:
            lines.append(f"还有 {overflow_count} 个未显示。")
        instruction_text = str(instruction or "").strip()
        if instruction_text:
            lines.append("─" * 18)
            lines.append(instruction_text)
        return "\n".join(lines)

    def _format_reply_mode_label(self, reply_mode: str) -> str:
        return QQ_REPLY_MODE_LABELS.get(_safe_reply_mode(reply_mode), QQ_REPLY_MODE_LABELS["text"])
