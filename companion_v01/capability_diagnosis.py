"""Host capability diagnosis for the ``/能力`` QQ command (Phase 5).

Presentation layer only: it reads the existing capability selection, approval
policy, gateway, settings and satellite state and renders a bounded text
reply.  It never mutates state and never registers a second capability
authority; the tool-count row comes from the same engine capability selection
the chat turns use, never from a hand-written tool list.
"""

from __future__ import annotations

import hashlib
from typing import Any, Callable

from .local_capability_config import (
    approval_mode_override_for_capability,
    get_approval_policy_config,
)
from .plugin_api import PluginQQCommandHandler, PluginQQCommandRequest, PluginQQCommandResult

CAPABILITY_COMMAND = "/能力"

_SHELL_MODE_LABELS = {
    "trusted_auto_allow": "已开启（直接执行）",
    "ask_each_time": "每次询问",
    "disabled": "已关闭",
}
_MEDIA_TOOL_NAMES = ("load_material", "transcribe_media", "send_file", "exec_run")
_SKILL_LIST_LIMIT = 20


def _text(value: Any, *, fallback: str = "") -> str:
    return str(value or "").strip() or fallback


def _hash_session_id(session_id: str) -> str:
    raw = str(session_id or "").strip()
    if not raw:
        return "unknown"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]


def _resolve_qq_selection(
    engine: Any,
    *,
    profile_user_id: str,
    session_id: str,
) -> Any | None:
    """Reuse the engine's own QQ capability selection (existing authority)."""
    resolve_context = getattr(engine, "_resolve_client_protocol_context", None)
    resolve_selection = getattr(engine, "_resolve_capability_selection", None)
    if not callable(resolve_context) or not callable(resolve_selection):
        return None
    try:
        client_context = resolve_context({"client_mode": "qq_text"})
        return resolve_selection(
            client_context=client_context,
            profile_user_id=profile_user_id,
            session_id=session_id,
            domain_profile_id="",
        )
    except Exception:
        return None


def _shell_state(
    *,
    engine: Any,
    config_module: Any,
    profile_user_id: str,
) -> dict[str, str]:
    capability_config_base_dir = getattr(
        engine,
        "capability_config_base_dir",
        getattr(config_module, "DATA_DIR", None),
    )
    try:
        policy_payload = get_approval_policy_config(
            base_dir=capability_config_base_dir,
            profile_user_id=profile_user_id,
        )
    except Exception:
        policy_payload = {}
    mode = str(
        approval_mode_override_for_capability(
            policy_payload.get("approvalPolicy"),
            "exec_run",
        )
        or "disabled"
    )
    execution_handler = (getattr(engine, "tool_handlers", {}) or {}).get("exec_run")
    execution_supported = bool(
        getattr(config_module, "EXECUTION_QQ_ENABLED", False)
        and getattr(engine, "execution_provider", None) is not None
    )
    capability_status = getattr(execution_handler, "capability_status", None)
    if execution_supported and callable(capability_status):
        try:
            execution_supported = bool((capability_status() or {}).get("enabled"))
        except Exception:
            execution_supported = False
    return {
        "mode": str(mode or "disabled"),
        "supported": "true" if execution_supported else "false",
    }


def build_capability_diagnosis(
    *,
    engine: Any,
    qq_gateway: Any,
    config_module: Any,
    satellite_service: Any = None,
    bot_label: str = "",
    profile_user_id: str,
    session_id: str,
    group_id: Any = 0,
    is_group: bool = False,
    qq_number: Any = 0,
    character_pack_id: str = "",
) -> dict[str, Any]:
    """Render the diagnosis; mirrors the gateway command-result dict shape."""
    session_type = "群聊" if is_group else "私聊"
    session_label = f"{session_type} #{_hash_session_id(session_id)}"
    role = (
        _text(character_pack_id)
        or _text(getattr(config_module, "QQ_CHARACTER_PACK_ID", ""))
        or "默认角色"
    )
    bot = _text(bot_label) or _text(getattr(config_module, "QQ_BOT_QQ", "")) or "Akane"

    settings = getattr(engine, "settings", None)
    chat_model = _text(getattr(settings, "chat_model_name", ""), fallback="未配置")
    vision_enabled = bool(getattr(settings, "vision_enabled", True))
    vision_model = _text(getattr(settings, "vision_model_name", ""), fallback="未配置")
    vision_status = getattr(engine, "native_chat_vision_status", None)
    if callable(vision_status):
        try:
            resolved_vision = vision_status() or {}
            vision_enabled = bool(resolved_vision.get("enabled"))
            vision_model = _text(resolved_vision.get("model"), fallback=vision_model)
        except Exception:
            # Keep the settings snapshot as a bounded diagnostic fallback.  A
            # diagnosis command must not make an otherwise healthy QQ command
            # fail because a capability probe raised.
            pass

    master_qq = _text(getattr(qq_gateway, "master_qq", ""))
    is_owner = bool(master_qq) and _text(qq_number) == master_qq

    shell = _shell_state(engine=engine, config_module=config_module, profile_user_id=profile_user_id)
    shell_mode = _SHELL_MODE_LABELS.get(shell["mode"], "未知")
    shell_supported = shell["supported"] == "true"

    group_vision: str | None = None
    if is_group:
        try:
            group_vision = "已开启" if qq_gateway.is_group_vision_enabled(group_id) else "已关闭"
        except Exception:
            group_vision = "未知"

    selection = _resolve_qq_selection(engine, profile_user_id=profile_user_id, session_id=session_id)
    ready_tool_names = tuple(getattr(selection, "tool_names", ()) or ()) if selection is not None else ()
    schema_tool_names = (
        tuple(getattr(selection, "schema_tool_names", ()) or ready_tool_names)
        if selection is not None
        else ()
    )
    tool_count = len(schema_tool_names)
    ready_tool_count = len(ready_tool_names)
    available_tools = frozenset(ready_tool_names)
    media_rows = {
        tool_name: (
            "未知"
            if selection is None
            else ("可用" if tool_name in available_tools else "当前不可用")
        )
        for tool_name in _MEDIA_TOOL_NAMES
    }

    skill_count = 0
    skill_names: tuple[str, ...] = ()
    skill_status = "未启用"
    skill_registry = getattr(engine, "skill_registry", None)
    if skill_registry is not None:
        try:
            snapshot = skill_registry.snapshot()
            entries = tuple(snapshot.entries or ())
            skill_count = len(entries)
            skill_names = tuple(entry.name for entry in entries)[:_SKILL_LIST_LIMIT]
            skill_status = "available"
        except Exception:
            skill_status = "unknown"

    satellite_status: str | None = None
    if satellite_service is not None:
        try:
            diagnostics = satellite_service.diagnostics()
            raw = str(diagnostics.get("status") or "offline")
            satellite_status = {"online": "在线", "offline": "离线", "disabled": "未启用"}.get(raw, raw)
        except Exception:
            satellite_status = "未知"

    provider = getattr(engine, "execution_provider", None)
    provider_id = _text(getattr(provider, "provider_id", ""), fallback="")

    unavailable: list[str] = []
    if shell["mode"] == "disabled":
        unavailable.append("Shell：已关闭（本会话不能执行命令/脚本）")
    elif not shell_supported:
        unavailable.append("Shell：宿主 QQ 总闸或执行提供者未启用")
    if not vision_enabled:
        unavailable.append("视觉模型：未启用或未配置（图片/视频画面理解不可用）")
    if is_owner and satellite_status == "离线":
        unavailable.append("Satellite：离线")
    if not provider:
        unavailable.append("执行器：不可用")

    lines = [
        f"【{bot} · 能力诊断】",
        f"会话：{session_label}",
        f"角色：{role}",
        f"Chat 模型：{chat_model}",
        f"Vision 模型：{'已启用 · ' + vision_model if vision_enabled else '已关闭 · ' + vision_model}",
        f"Shell：{shell_mode}" + ("" if shell_supported else "（宿主总闸未开）"),
    ]
    if group_vision is not None:
        lines.append(f"群识图：{group_vision}")
    if skill_status == "available":
        # Skill names can disclose private workflows.  Members only need the
        # public count; the owner gets the bounded inventory for diagnosis.
        lines.append(
            f"Skill：可用 {skill_count} 个"
            + (f"（{'、'.join(skill_names)}）" if is_owner and skill_names else "")
        )
    elif skill_status == "unknown":
        lines.append("Skill：状态未知")
    else:
        lines.append("Skill：未启用")
    if selection is None:
        lines.append("工具画像：未知")
    elif ready_tool_count == tool_count:
        lines.append(f"工具画像：{tool_count} 个（全部可执行）")
    else:
        lines.append(f"工具画像：{tool_count} 个（当前可执行 {ready_tool_count} 个）")
    lines.append(
        "媒体/文件处理："
        + " · ".join(f"{name}={media_rows[name]}" for name in _MEDIA_TOOL_NAMES)
    )
    if is_owner:
        if provider_id:
            lines.append(f"Shell 执行位置：宿主本机执行器（{provider_id}）")
        if satellite_status is not None:
            lines.append(f"Satellite：{satellite_status}")
    if unavailable:
        lines.append("当前不可用：" + "；".join(unavailable))
    lines.append("数据来自当前配置与能力快照，只读；开关请使用 /shell、/识图 等命令。")
    return {
        "handled": True,
        "ok": True,
        "status": "ok",
        "reply": "\n".join(lines),
        "is_owner": is_owner,
        "tool_count": tool_count,
        "ready_tool_count": ready_tool_count,
    }


class CapabilityDiagnosisCommand:
    """Host QQ command handler for ``/能力`` (presentation layer)."""

    def __init__(
        self,
        *,
        engine: Any,
        qq_gateway: Any,
        config_module: Any,
        satellite_service: Any = None,
        bot_label: str = "",
    ) -> None:
        self._engine = engine
        self._qq_gateway = qq_gateway
        self._config_module = config_module
        self._satellite_service = satellite_service
        self._bot_label = bot_label

    async def handle(self, request: PluginQQCommandRequest) -> PluginQQCommandResult:
        result = build_capability_diagnosis(
            engine=self._engine,
            qq_gateway=self._qq_gateway,
            config_module=self._config_module,
            satellite_service=self._satellite_service,
            bot_label=self._bot_label,
            profile_user_id=request.profile_user_id,
            session_id=request.session_id,
            group_id=request.group_id,
            is_group=request.is_group,
            qq_number=request.qq_number,
            character_pack_id=request.character_pack_id,
        )
        return PluginQQCommandResult(
            handled=True,
            reply_text=str(result.get("reply") or "").strip(),
            reason="",
        )


def build_host_command_registrations(
    *,
    engine: Any,
    qq_gateway: Any,
    config_module: Any,
    satellite_service: Any = None,
    bot_label: str = "",
) -> tuple[Any, ...]:
    """Return the host ``/能力`` registration tuple for the command broker."""
    from .plugin_qq_commands import _PluginCommandRegistration

    return (
        _PluginCommandRegistration(
            plugin_id="host",
            command=CAPABILITY_COMMAND,
            handler=CapabilityDiagnosisCommand(
                engine=engine,
                qq_gateway=qq_gateway,
                config_module=config_module,
                satellite_service=satellite_service,
                bot_label=bot_label,
            ),
        ),
    )


__all__ = [
    "CAPABILITY_COMMAND",
    "CapabilityDiagnosisCommand",
    "build_capability_diagnosis",
    "build_host_command_registrations",
]
