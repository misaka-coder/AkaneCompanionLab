"""Service-bound shop tools over the desktop/QQ shared editing service."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from capcore import CapabilityToolSpec

from .. import care_shop_settings as shop
from ..dialogue_targets import DialogueTarget, registry_for
from ..qq_commerce_cards import build_qq_shop_change_card, render_qq_shop_change_card
from .core import (
    BaseToolHandler,
    ToolExecutionContext,
    ToolExecutionResult,
    ToolFollowupEnvelope,
    _project_tool_metadata_from_spec,
)

logger = logging.getLogger(__name__)

READ_SHOP_SPEC = CapabilityToolSpec(
    capability_id="read_character_shop",
    display_name="查看商店",
    description="查看本次服务角色的商品和版本。编辑前先读取，保留返回的 revision；custom=true 的商品可编辑。price 是统一售价，桌宠使用金币，QQ 使用兑换额度。",
    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
    risk="low",
    confirm="never",
    effects=(),
    visible_in=("qq",),
    execution_class="sync",
    idempotency="read_only",
    max_result_bytes=16384,
)
EDIT_SHOP_SPEC = CapabilityToolSpec(
    capability_id="edit_character_shop",
    display_name="编辑商品",
    description="维护本次服务角色的自定义商品，只有主人可写。create 需名称、价格和效果；update 仅传修改字段，delete 下架商品。revision 使用刚读取的版本。description 为简短介绍，空字符串清空。成功后系统自动发送商品确认图，并向服务角色的当前会话追加变更事件；event_notification 返回记录状态。",
    input_schema={
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["create", "update", "delete"]},
            "revision": {"type": "string"},
            "item_id": {"type": "string"},
            "name": {"type": "string", "maxLength": 40},
            "description": {"type": "string", "maxLength": 120},
            "price": {
                "type": "integer",
                "minimum": 0,
                "maximum": 999999,
                "description": "统一售价；桌宠使用金币，QQ 使用兑换额度。",
            },
            "effects": {
                "type": "object",
                "properties": {
                    k: {"type": "integer", "minimum": -100, "maximum": 100} for k in ("hunger", "energy", "affection")
                },
                "description": "饱腹、精力、好感的变化；饱腹 hunger 越大越饱。",
                "additionalProperties": False,
            },
            "usable_in": {
                "type": "array",
                "items": {"type": "string", "enum": ["desktop_pet", "qq"]},
                "description": "保留 desktop_pet；包含 qq 时也在 QQ 上架。",
            },
        },
        "required": ["action", "revision"],
        "additionalProperties": False,
    },
    risk="medium",
    confirm="never",
    effects=("character_shop_write",),
    visible_in=("qq",),
    execution_class="sync",
    idempotency="idempotent",
    max_result_bytes=16384,
)


class CharacterShopToolHandler(BaseToolHandler):
    service_only = True
    default_exposure_mode = "resident"

    def __init__(self, engine: Any, *, write: bool = False):
        self.engine = engine
        self.write = write
        self.tool_type = "edit_character_shop" if write else "read_character_shop"

    def tool_spec(self):
        return EDIT_SHOP_SPEC if self.write else READ_SHOP_SPEC

    def tool_metadata(self):
        return _project_tool_metadata_from_spec(
            self.tool_spec(),
            classification={
                "family": "character_shop",
                "operation": "write" if self.write else "read",
            },
        )

    def capability_status(self):
        ready = getattr(self.engine, "desktop_pet_character_resources", None) is not None
        return {"enabled": ready, "status": "available" if ready else "unavailable"}

    def normalize_call(self, value):
        if not isinstance(value, dict) or value.get("type") != self.tool_type:
            return None
        allowed = {"type", *self.tool_spec().input_schema["properties"]}
        if set(value) - allowed:
            return None
        if self.write and (
            value.get("action") not in {"create", "update", "delete"} or not isinstance(value.get("revision"), str)
        ):
            return None
        return dict(value)

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        try:
            result = self._execute(call, context)
        except (ValueError, OSError, TypeError, KeyError) as exc:
            reason = str(exc) if str(exc) in shop.SHOP_ERROR_REASONS else "shop_operation_failed"
            result = {"ok": False, "status": "failed", "reason": reason}
        confirmation = result.pop("_confirmation", None)
        events = []
        if confirmation is not None:
            # Rendering/delivery is auxiliary to the already committed shop write.
            delivery_event = None
            try:
                target = context.dialogue_target
                resources = self.engine.desktop_pet_character_resources
                identity = resources.build_character_identity(target.service_target)
                document = build_qq_shop_change_card(
                    confirmation["action"], confirmation["item"],
                    character_label=identity.get("assistant_name") or target.service_target,
                    user_scope=f"{context.profile_user_id}|{context.session_id}|{target.memory_domain}",
                    character_pack_id=target.service_target,
                    resources=resources,
                )
                delivery_event = {"type": "file_ready", "client_mode": "qq_text", "send_to_user": True,
                               "delivery_mode": "auto", "fallback_text": document.as_text(),
                               "file": {"source_type": "shop_confirmation",
                                   "source_id": f"shop-change:{confirmation['id']}",
                                   "name": f"商品{document.title}.png", "mime_type": "image/png"}}
                path = render_qq_shop_change_card(document, Path(self.engine.base_dir) / "qq_commerce_cache")
                delivery_event["file"]["absolute_path"] = str(path)
                events.append(delivery_event)
                result["confirmation_card"] = {"ok": True, "status": "ready"}
            except Exception as exc:
                reason = str(exc) if str(exc) in {"chinese_font_unavailable", "commerce_background_unavailable"} else "shop_card_unavailable"
                result["confirmation_card"] = {"ok": False, "status": "failed", "reason": reason}
                if delivery_event is not None:
                    events.append({**delivery_event, "type": "file_unavailable"})
        content = json.dumps(result, ensure_ascii=False)
        return ToolExecutionResult(
            tool_type=self.tool_type,
            followup_context=content,
            followup_envelope=ToolFollowupEnvelope(content=content, producer_bounded=True, complete=True),
            stream_events=events
            if result.get("ok")
            else [
                {
                    "type": "tool_execution_failed",
                    "tool_type": self.tool_type,
                    "reason": result.get("reason") or result.get("status"),
                }
            ],
        )

    def _execute(self, call, context):
        target = context.dialogue_target
        if (
            not isinstance(target, DialogueTarget)
            or target.kind != "service"
            or target.memory_domain != context.character_pack_id
            or context.client_mode != "qq_text"
        ):
            return {"ok": False, "status": "forbidden", "reason": "shop_service_context_required"}
        registry = registry_for(self.engine)
        registry.validate(target)
        registry.service_persona(target)
        # A presentation rename must not change a replayed business event.
        service_name = registry.services[target.speaker_id].name
        if self.tool_type not in registry.services[target.speaker_id].tools:
            return {"ok": False, "status": "forbidden", "reason": "shop_tool_not_allowed"}
        policy = getattr(self.engine, "memory_read_policy", None)
        origin = policy._origin(context) if policy is not None else None
        if origin is None:
            return {"ok": False, "status": "forbidden", "reason": "shop_verified_qq_origin_required"}
        actor = str(origin.get("actor") or "") if origin.get("kind") == "group" else str(origin.get("recipient") or "")
        owner = str(policy.master_qq or "")
        if self.write and (not owner or actor not in {f"qq:{owner}", f"user:{owner}"}):
            return {"ok": False, "status": "forbidden", "reason": "shop_owner_required"}
        if not target.service_target:
            return {"ok": False, "status": "unavailable", "reason": "character_care_unavailable"}
        if context.cancel_requested is not None and context.cancel_requested():
            return {"ok": False, "status": "cancelled", "reason": "shop_operation_cancelled"}
        service = self.engine.desktop_pet_character_resources
        current = shop.read_shop(service, target.service_target)
        if not self.write:
            return current
        if not context.current_user_source_id or not context.invocation_id:
            return {"ok": False, "status": "rejected", "reason": "shop_operation_identity_required"}
        action = call.get("action")
        if action == "create":
            # Stable across retries, including a crash after the atomic file replace.
            operation = {k: v for k, v in call.items() if k not in {"type", "revision"}}
            operation_id = hashlib.sha256(
                json.dumps(
                    [
                        context.profile_user_id,
                        context.session_id,
                        target.memory_domain,
                        context.current_user_source_id,
                        operation,
                    ],
                    ensure_ascii=False,
                    sort_keys=True,
                ).encode()
            ).hexdigest()
            result = shop.create_shop_item(service, target.service_target, call, operation_id=operation_id)
            return self._with_change_event(call, context, current, result, service_name=service_name)
        if current["revision"] != call.get("revision"):
            return {"ok": False, "status": "conflict", "reason": "shop_revision_conflict"}
        item = next((i for i in current["items"] if i["id"] == call.get("item_id")), None)
        if item is None:
            return {"ok": False, "status": "not_found", "reason": "shop_item_not_found"}
        if not item.get("custom"):
            return {"ok": False, "status": "forbidden", "reason": "shop_custom_item_required"}
        if action == "delete":
            result = shop.delete_shop_item(service, target.service_target, call)
            return self._with_change_event(call, context, current, result, service_name=service_name)
        if action != "update":
            raise ValueError("invalid_shop_request")
        changes = {k: v for k, v in call.items() if k not in {"type", "action"}}
        effects = changes.get("effects", {})
        if not isinstance(effects, dict) or set(effects) - {"hunger", "energy", "affection"}:
            raise ValueError("invalid_shop_effects")
        merged = {
            "price": item["price"],
            **changes,
            "effects": {**{k: item["effects"].get(k, 0) for k in ("hunger", "energy", "affection")}, **effects},
        }
        result = shop.update_shop_item(service, target.service_target, merged)
        return self._with_change_event(call, context, current, result, service_name=service_name)

    def _with_change_event(self, call, context, before, result, *, service_name):
        """Append business evidence to the served role, without copying service chat.

        Shop and memory have separate transactions. A notification failure must
        never turn an already committed shop edit into an apparent failed edit.
        """
        if not result.get("ok"):
            return result
        action = call["action"]
        item_id = result.get("item_id") or call.get("item_id")
        old = next((i for i in before["items"] if i["id"] == item_id), None)
        if action == "create":
            # Use the original request on replay, even if someone later edits
            # this item. MemCore deduplicates the same source and event payload.
            new = {
                "name": call["name"].strip(),
                "price": call["price"],
                "description": str(call.get("description", "")).strip(),
                "effects": call["effects"],
                "usable_in": call.get("usable_in", list(shop.SHOP_MODES)),
            }
        else:
            new = next((i for i in result["items"] if i["id"] == item_id), None)
        changes = []
        labels = {"name": "名称", "description": "介绍", "price": "售价", "effects": "效果", "usable_in": "使用渠道"}
        for key, label in labels.items():
            if action == "update" and old.get(key) == new.get(key):
                continue
            if action == "delete":
                break
            value = new.get(key)
            if key == "effects":
                value = "，".join(
                    f"{name}{value.get(effect, 0):+d}"
                    for effect, name in (("hunger", "饱腹"), ("energy", "精力"), ("affection", "好感"))
                )
            elif key == "usable_in":
                value = "、".join("QQ" if mode == "qq" else "桌宠" for mode in value)
            else:
                value = str(value or "无") if key != "price" else f"{value}（桌宠金币／QQ 兑换额度）"
            changes.append(f"{label}：{value}")
        if action == "update" and not changes:
            return {**result, "event_notification": {"ok": True, "status": "not_needed"}}
        operation = {k: v for k, v in call.items() if k != "type" and (action != "create" or k != "revision")}
        target = context.dialogue_target
        event_id = hashlib.sha256(
            json.dumps(
                [
                    context.profile_user_id,
                    context.session_id,
                    target.memory_domain,
                    context.current_user_source_id,
                    operation,
                ],
                ensure_ascii=False,
                sort_keys=True,
            ).encode()
        ).hexdigest()
        event = {
            "event_type": "shop.changed",
            "source": service_name,
            "fields": {
                "action": {"create": "上架", "update": "修改", "delete": "下架"}[action],
                "item": (new or old)["name"],
                "changes": "；".join(changes) if changes else "已下架。",
            },
        }
        try:
            getter = getattr(self.engine, "_memcore_manager_if_enabled", None)
            manager = getter() if callable(getter) else getattr(self.engine, "memcore_manager", None)
            if manager is None:
                notice = {"ok": False, "status": "unavailable"}
            else:
                written = manager.append_standalone_event(
                    event,
                    source_id=f"shop-change:{event_id}",
                    profile_user_id=context.profile_user_id,
                    session_id=context.session_id,
                    character_pack_id=target.service_target,
                    timestamp=context.now_ts,
                )
                notice = {"ok": bool(written.get("ok")), "status": "recorded" if written.get("ok") else "failed"}
        except Exception:
            logger.warning("Shop edit committed but character event recording failed")
            notice = {"ok": False, "status": "failed"}
        confirmation = {"_confirmation": {"id": event_id, "action": action,
                         "item": {**(new or old), "id": item_id}}} if result.get("status") != "already_created" else {}
        return {**result, "event_notification": notice, **confirmation}
