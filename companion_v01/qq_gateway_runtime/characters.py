"""QQCharacterCommands: command behavior and domain-specific reply formatting."""
from __future__ import annotations
from typing import Any
from .definitions import QQ_CHARACTER_BUILTIN_IDS
from .validation import (
    _safe_character_pack_id,
    _safe_qq_session_key,
    _safe_outfit_id,
    _outfit_lookup_key,
)
from .context import QQMessageContext
from .preferences import QQSessionPreferences


class QQCharacterCommands:
    """Own this command domain; host operations use the supplied gateway port."""

    def __init__(self, *, gateway: Any, preferences: QQSessionPreferences) -> None:
        self.gateway = gateway
        self.preferences = preferences

    def handle_character_command(
        self,
        context: QQMessageContext,
        *,
        command: dict[str, Any] | None = None,
        character_resource_service: Any = None,
    ) -> dict[str, Any] | None:
        if command is None:
            command = self.gateway.parse_character_command(context.clean_message)
        if command is None:
            return None

        action = str(command.get("action") or "")
        if action == "list":
            packs = self._list_character_packs(character_resource_service)
            if not packs:
                return {
                    "handled": True,
                    "ok": False,
                    "status": "no_character_packs",
                    "reply": "当前还没有可用角色包。",
                    "character_pack_id": self.gateway.resolve_character_pack_id(context.session_id),
                    "available_packs": [],
                }
            labels = [self._format_pack_label(item) for item in packs[:20]]
            overflow_count = max(0, len(packs) - len(labels))
            return {
                "handled": True,
                "ok": True,
                "status": "listed",
                "reply": self._build_multiline_option_reply(
                    "可用角色包",
                    labels,
                    instruction="切换：/角色 角色包ID",
                    overflow_count=overflow_count,
                ),
                "character_pack_id": self.gateway.resolve_character_pack_id(context.session_id),
                "available_packs": [str(item.get("pack_id") or "") for item in packs if str(item.get("pack_id") or "")],
            }

        if action == "current":
            active_pack_id = self.gateway.resolve_character_pack_id(context.session_id)
            return {
                "handled": True,
                "ok": True,
                "status": "current",
                "reply": self._build_current_character_reply(
                    active_pack_id,
                    character_resource_service=character_resource_service,
                    session_id=context.session_id,
                ),
                "character_pack_id": active_pack_id,
            }

        if action == "default":
            state_persisted = self.gateway.clear_session_character_override(context.session_id)
            active_pack_id = self.gateway.resolve_character_pack_id(context.session_id)
            reply = self._build_default_character_reply(
                active_pack_id,
                character_resource_service=character_resource_service,
            )
            return {
                "handled": True,
                "ok": True,
                "status": "default",
                "reply": self._append_state_persistence_warning(reply, state_persisted),
                "character_pack_id": active_pack_id,
                "state_persisted": state_persisted,
            }

        if action == "builtin":
            state_persisted = self.gateway.set_session_character_pack_id(context.session_id, "")
            return {
                "handled": True,
                "ok": True,
                "status": "builtin",
                "reply": self._append_state_persistence_warning(
                    "已切回内置 Akane 人设。之后这个 QQ 会话会使用未绑定角色包的默认聊天记忆。",
                    state_persisted,
                ),
                "character_pack_id": "",
                "state_persisted": state_persisted,
            }

        if action == "switch":
            requested_pack_id = _safe_character_pack_id(command.get("pack_id"))
            if not requested_pack_id:
                return {
                    "handled": True,
                    "ok": False,
                    "status": "invalid_character_pack_id",
                    "reply": "这个角色包 id 不太对。只能使用字母、数字、下划线、点和短横线，比如 reimu。",
                    "character_pack_id": self.gateway.resolve_character_pack_id(context.session_id),
                }
            if requested_pack_id.lower() in QQ_CHARACTER_BUILTIN_IDS:
                state_persisted = self.gateway.set_session_character_pack_id(context.session_id, "")
                return {
                    "handled": True,
                    "ok": True,
                    "status": "builtin",
                    "reply": self._append_state_persistence_warning(
                        "已切回内置 Akane 人设。之后这个 QQ 会话会使用未绑定角色包的默认聊天记忆。",
                        state_persisted,
                    ),
                    "character_pack_id": "",
                    "state_persisted": state_persisted,
                }
            identity = self.gateway._resolve_pack_identity(
                requested_pack_id,
                character_resource_service=character_resource_service,
            )
            if not identity:
                packs = self._list_character_packs(character_resource_service)
                available = [str(item.get("pack_id") or "") for item in packs[:12] if str(item.get("pack_id") or "")]
                hint = self._build_multiline_available_hint(
                    available,
                    empty_message="可以先在角色工坊创建或导入角色包。",
                )
                return {
                    "handled": True,
                    "ok": False,
                    "status": "unknown_character_pack",
                    "reply": f"没有找到角色包 {requested_pack_id}。\n{hint}",
                    "character_pack_id": self.gateway.resolve_character_pack_id(context.session_id),
                    "requested_pack_id": requested_pack_id,
                }
            state_persisted = self.gateway.set_session_character_pack_id(context.session_id, requested_pack_id)
            label = self._format_identity_label(identity)
            reply = f"已切换本 QQ 会话角色为 {label}（{requested_pack_id}）。之后的聊天和记忆会按这个角色包隔离。"
            return {
                "handled": True,
                "ok": True,
                "status": "switched",
                "reply": self._append_state_persistence_warning(reply, state_persisted),
                "character_pack_id": requested_pack_id,
                "requested_pack_id": requested_pack_id,
                "state_persisted": state_persisted,
            }

        return None

    def handle_outfit_command(
        self,
        context: QQMessageContext,
        *,
        command: dict[str, Any] | None = None,
        resource_manifest_builder: Any = None,
    ) -> dict[str, Any] | None:
        if command is None:
            command = self.gateway.parse_outfit_command(context.clean_message)
        if command is None:
            return None

        active_pack_id = self.gateway.resolve_character_pack_id(context.session_id)
        if not active_pack_id:
            return {
                "handled": True,
                "ok": False,
                "status": "missing_character_pack",
                "reply": "当前 QQ 会话还没有绑定角色包。请先发送 /角色 列表 或 /角色 角色包ID。",
                "character_pack_id": "",
                "outfit_id": "",
            }

        outfits = self._list_manifest_outfits(
            active_pack_id,
            resource_manifest_builder=resource_manifest_builder,
        )
        action = str(command.get("action") or "")
        if action == "list":
            if not outfits:
                return {
                    "handled": True,
                    "ok": False,
                    "status": "no_outfits",
                    "reply": f"角色包 {active_pack_id} 当前没有可用服装资源。",
                    "character_pack_id": active_pack_id,
                    "outfit_id": self.gateway.resolve_session_outfit_id(context.session_id),
                }
            labels = [self._format_outfit_label(item) for item in outfits[:20]]
            overflow_count = max(0, len(outfits) - len(labels))
            return {
                "handled": True,
                "ok": True,
                "status": "listed",
                "reply": self._build_multiline_option_reply(
                    "可用服装",
                    labels,
                    instruction="切换：/服装 服装ID",
                    overflow_count=overflow_count,
                ),
                "character_pack_id": active_pack_id,
                "outfit_id": self.gateway.resolve_session_outfit_id(context.session_id),
                "available_outfits": [str(item.get("id") or "") for item in outfits if str(item.get("id") or "")],
            }

        if action == "current":
            active_outfit = self.gateway.resolve_session_outfit_id(context.session_id)
            default_outfit = self._manifest_default_outfit(
                active_pack_id,
                resource_manifest_builder=resource_manifest_builder,
                outfits=outfits,
            )
            return {
                "handled": True,
                "ok": True,
                "status": "current",
                "reply": self._build_current_outfit_reply(
                    active_outfit or default_outfit,
                    default_outfit=default_outfit,
                    outfits=outfits,
                    session_id=context.session_id,
                ),
                "character_pack_id": active_pack_id,
                "outfit_id": active_outfit or default_outfit,
            }

        if action == "default":
            state_persisted = self.gateway.clear_session_outfit_override(context.session_id)
            default_outfit = self._manifest_default_outfit(
                active_pack_id,
                resource_manifest_builder=resource_manifest_builder,
                outfits=outfits,
            )
            outfit_label = self._format_outfit_label(
                self._find_outfit(outfits, default_outfit) or {"id": default_outfit}
            )
            note = (
                f"【当前换装】用户刚把你在当前 QQ 会话中的服装恢复为角色包默认服装：{outfit_label}。"
                "角色本身没有切换，只是同一个角色换回默认服装；请自然回应这次换装，不要像系统通知。"
            )
            return {
                "handled": True,
                "ok": True,
                "status": "default",
                "_llm_passthrough": True,
                "qq_action_note": note,
                "turn_message": self._build_outfit_event_turn_message(
                    context,
                    f"把你的 QQ 当前会话服装恢复为默认服装：{outfit_label}。",
                ),
                "character_pack_id": active_pack_id,
                "outfit_id": default_outfit,
                "state_persisted": state_persisted,
            }

        if action == "switch":
            requested_outfit = _safe_outfit_id(command.get("outfit_id"))
            if not requested_outfit:
                return {
                    "handled": True,
                    "ok": False,
                    "status": "invalid_outfit_id",
                    "reply": "这个服装名不太对。请不要包含路径分隔符或过长文本，比如 /服装 default。",
                    "character_pack_id": active_pack_id,
                    "outfit_id": self.gateway.resolve_session_outfit_id(context.session_id),
                }
            outfit = self._find_outfit(outfits, requested_outfit)
            if outfit is None:
                available = [self._format_outfit_label(item) for item in outfits[:12]]
                hint = self._build_multiline_available_hint(
                    available,
                    empty_message="这个角色包暂时没有可用服装资源。",
                )
                return {
                    "handled": True,
                    "ok": False,
                    "status": "unknown_outfit",
                    "reply": f"没有找到服装 {requested_outfit}。\n{hint}",
                    "character_pack_id": active_pack_id,
                    "outfit_id": self.gateway.resolve_session_outfit_id(context.session_id),
                    "requested_outfit_id": requested_outfit,
                }
            outfit_id = str(outfit.get("id") or requested_outfit).strip()
            state_persisted = self.gateway.set_session_outfit_id(context.session_id, outfit_id)
            outfit_label = self._format_outfit_label(outfit)
            note = (
                f"【当前换装】用户刚把你在当前 QQ 会话中的服装切换为：{outfit_label}。"
                "角色本身没有切换，只是同一个角色换了这套衣服；请按当前服装自然回应，可以简短提到穿着感受，"
                "不要复述系统字段。"
            )
            return {
                "handled": True,
                "ok": True,
                "status": "switched",
                "_llm_passthrough": True,
                "qq_action_note": note,
                "turn_message": self._build_outfit_event_turn_message(
                    context,
                    f"把你的 QQ 当前会话服装切换为：{outfit_label}。",
                ),
                "character_pack_id": active_pack_id,
                "outfit_id": outfit_id,
                "requested_outfit_id": requested_outfit,
                "state_persisted": state_persisted,
            }

        return None

    def _list_character_packs(self, character_resource_service: Any = None) -> list[dict[str, str]]:
        if character_resource_service is None:
            return []
        listing = getattr(character_resource_service, "list_character_packs", None)
        if listing is None:
            return []
        try:
            raw_items = listing()
        except Exception:
            return []
        normalized: list[dict[str, str]] = []
        seen: set[str] = set()
        for item in raw_items if isinstance(raw_items, list) else []:
            if not isinstance(item, dict):
                continue
            pack_id = _safe_character_pack_id(item.get("pack_id") or item.get("id"))
            if not pack_id or pack_id in seen:
                continue
            seen.add(pack_id)
            normalized.append(
                {
                    "pack_id": pack_id,
                    "name": str(item.get("name") or pack_id).strip()[:80],
                    "app_name": str(item.get("app_name") or item.get("appName") or item.get("name") or pack_id).strip()[
                        :80
                    ],
                    "user_title": str(item.get("user_title") or item.get("userTitle") or "").strip()[:80],
                }
            )
        return normalized

    def _list_manifest_outfits(
        self,
        character_pack_id: str,
        *,
        resource_manifest_builder: Any = None,
    ) -> list[dict[str, Any]]:
        manifest = self._build_outfit_manifest(
            character_pack_id,
            resource_manifest_builder=resource_manifest_builder,
        )
        raw_outfits = manifest.get("characters", {}).get("outfits") if isinstance(manifest, dict) else []
        outfits: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in raw_outfits if isinstance(raw_outfits, list) else []:
            if not isinstance(item, dict):
                continue
            outfit_id = _safe_outfit_id(item.get("id") or item.get("name"))
            if not outfit_id or outfit_id in seen:
                continue
            seen.add(outfit_id)
            aliases = [_safe_outfit_id(alias) for alias in list(item.get("aliases") or []) if _safe_outfit_id(alias)]
            outfits.append(
                {
                    **item,
                    "id": outfit_id,
                    "name": str(item.get("name") or outfit_id).strip()[:80],
                    "aliases": aliases,
                }
            )
        return outfits

    def _build_outfit_manifest(
        self,
        character_pack_id: str,
        *,
        resource_manifest_builder: Any = None,
    ) -> dict[str, Any]:
        if resource_manifest_builder is None:
            return {}
        try:
            manifest = resource_manifest_builder(character_pack_id)
        except TypeError:
            try:
                manifest = resource_manifest_builder(character_pack_id=character_pack_id)
            except Exception:
                return {}
        except Exception:
            return {}
        return manifest if isinstance(manifest, dict) else {}

    def _manifest_default_outfit(
        self,
        character_pack_id: str,
        *,
        resource_manifest_builder: Any = None,
        outfits: list[dict[str, Any]] | None = None,
    ) -> str:
        outfit_items = list(outfits or [])
        manifest = self._build_outfit_manifest(
            character_pack_id,
            resource_manifest_builder=resource_manifest_builder,
        )
        defaults = manifest.get("defaults") if isinstance(manifest.get("defaults"), dict) else {}
        clients = manifest.get("clients") if isinstance(manifest.get("clients"), dict) else {}
        desktop = clients.get("desktop_pet") if isinstance(clients.get("desktop_pet"), dict) else {}
        candidates = [
            desktop.get("default_outfit"),
            defaults.get("desktop_pet_outfit"),
            defaults.get("outfit"),
        ]
        for candidate in candidates:
            outfit = self._find_outfit(outfit_items, _safe_outfit_id(candidate))
            if outfit is not None:
                return str(outfit.get("id") or "").strip()
        if outfit_items:
            return str(outfit_items[0].get("id") or "").strip()
        return _safe_outfit_id(candidates[0] if candidates else "")

    def _find_outfit(self, outfits: list[dict[str, Any]], requested: str) -> dict[str, Any] | None:
        target = _outfit_lookup_key(requested)
        if not target:
            return None
        for outfit in outfits:
            candidates = [
                outfit.get("id"),
                outfit.get("name"),
                *list(outfit.get("aliases") or []),
            ]
            if any(_outfit_lookup_key(candidate) == target for candidate in candidates):
                return outfit
        return None

    def _build_multiline_option_reply(
        self,
        title: str,
        labels: list[str],
        *,
        instruction: str,
        overflow_count: int = 0,
    ) -> str:
        lines = [str(title or "").strip() or "可用选项", "─" * 18]
        lines.extend(f"  {label}" for label in labels if str(label or "").strip())
        if overflow_count > 0:
            lines.append(f"还有 {overflow_count} 个未显示。")
        instruction_text = str(instruction or "").strip()
        if instruction_text:
            lines.append("─" * 18)
            lines.append(instruction_text)
        return "\n".join(lines)

    def _build_multiline_available_hint(self, labels: list[str], *, empty_message: str) -> str:
        visible_labels = [str(label or "").strip() for label in labels if str(label or "").strip()]
        if not visible_labels:
            return str(empty_message or "").strip()
        lines = ["当前可用："]
        lines.extend(f"  {label}" for label in visible_labels)
        return "\n".join(lines)

    def _format_outfit_label(self, item: dict[str, Any]) -> str:
        outfit_id = str(item.get("id") or "").strip()
        name = str(item.get("name") or "").strip()
        return (
            f"{outfit_id}（{name}）"
            if outfit_id and name and name != outfit_id
            else (outfit_id or name or "未命名服装")
        )

    def _resolve_pack_identity(
        self,
        pack_id: str,
        *,
        character_resource_service: Any = None,
    ) -> dict[str, str]:
        normalized_pack_id = _safe_character_pack_id(pack_id)
        if not normalized_pack_id or character_resource_service is None:
            return {}
        builder = getattr(character_resource_service, "build_character_identity", None)
        if builder is None:
            return {}
        try:
            identity = builder(normalized_pack_id)
        except Exception:
            return {}
        if not isinstance(identity, dict) or not identity:
            return {}
        resolved_pack_id = _safe_character_pack_id(identity.get("pack_id") or normalized_pack_id)
        if resolved_pack_id != normalized_pack_id:
            return {}
        assistant_name = str(identity.get("assistant_name") or identity.get("name") or normalized_pack_id).strip()
        app_name = str(identity.get("app_name") or assistant_name or normalized_pack_id).strip()
        user_label = str(identity.get("user_label") or "").strip()
        return {
            "pack_id": normalized_pack_id,
            "assistant_name": assistant_name[:80] or normalized_pack_id,
            "app_name": app_name[:80] or assistant_name[:80] or normalized_pack_id,
            "user_label": user_label[:80],
        }

    def _format_pack_label(self, item: dict[str, Any]) -> str:
        pack_id = str(item.get("pack_id") or "").strip()
        name = str(item.get("name") or "").strip()
        app_name = str(item.get("app_name") or "").strip()
        display = " / ".join(part for part in (app_name, name) if part and part != pack_id)
        return f"{pack_id}（{display}）" if display else pack_id

    def _format_identity_label(self, identity: dict[str, Any]) -> str:
        assistant_name = str(identity.get("assistant_name") or "").strip()
        app_name = str(identity.get("app_name") or "").strip()
        label = " / ".join(part for part in (app_name, assistant_name) if part)
        return label or str(identity.get("pack_id") or "角色包").strip() or "角色包"

    def _append_state_persistence_warning(self, reply: str, state_persisted: bool) -> str:
        if state_persisted:
            return reply
        return f"{reply} 但状态文件保存失败，本次运行内会生效，重启后可能恢复默认角色。"

    def _build_current_character_reply(
        self,
        active_pack_id: str,
        *,
        character_resource_service: Any = None,
        session_id: str = "",
    ) -> str:
        key = str(session_id or "").strip()
        with self.preferences._character_pack_lock:
            has_override = bool(key and key in self.preferences.character_pack_overrides)
        if not active_pack_id:
            source = "本会话临时切换" if has_override else "QQ 默认配置"
            return f"当前 QQ 会话使用内置 Akane 人设（来源：{source}）。"
        identity = self.gateway._resolve_pack_identity(
            active_pack_id,
            character_resource_service=character_resource_service,
        )
        label = self._format_identity_label(identity) if identity else active_pack_id
        source = "本会话临时切换" if has_override else "QQ 默认配置"
        return f"当前 QQ 会话角色：{label}（{active_pack_id}，来源：{source}）。"

    def _build_default_character_reply(
        self,
        active_pack_id: str,
        *,
        character_resource_service: Any = None,
    ) -> str:
        if not active_pack_id:
            return "已恢复 QQ 默认角色：内置 Akane 人设。"
        identity = self.gateway._resolve_pack_identity(
            active_pack_id,
            character_resource_service=character_resource_service,
        )
        label = self._format_identity_label(identity) if identity else active_pack_id
        return f"已恢复 QQ 默认角色：{label}（{active_pack_id}）。"

    def _build_current_outfit_reply(
        self,
        active_outfit_id: str,
        *,
        default_outfit: str,
        outfits: list[dict[str, Any]],
        session_id: str = "",
    ) -> str:
        key = _safe_qq_session_key(session_id)
        with self.preferences._outfit_lock:
            has_override = bool(key and key in self.preferences.outfit_overrides)
        source = "本会话临时切换" if has_override else "角色包默认"
        outfit = self._find_outfit(outfits, active_outfit_id) or {"id": active_outfit_id or default_outfit}
        return f"当前 QQ 会话服装：{self._format_outfit_label(outfit)}（来源：{source}）。"

    def _build_outfit_event_turn_message(self, context: QQMessageContext, action_text: str) -> str:
        text = str(action_text or "").strip()
        if not text:
            text = "切换了你的 QQ 当前会话服装。"
        if context.is_group:
            label = context.sender_label or (f"QQ {context.user_id}" if context.user_id else "群成员")
            return f"【{label}】用户刚刚{text}"
        return f"我{text}"
