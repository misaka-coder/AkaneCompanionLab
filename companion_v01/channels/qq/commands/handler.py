"""QQ built-in and plugin command dispatch, preserving permission and command order."""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from typing import Any

from fastapi.responses import JSONResponse

from companion_v01.channels.qq.commands.card_results import QQCommandCards
from companion_v01.channels.qq.commands.parser import (
    QQCommandResolution,
    build_qq_help,
    resolve_qq_command,
    split_qq_command,
)
from companion_v01.channels.qq.commands.workspace import (
    _build_qq_workspace_clear_reply,
    _build_qq_workspace_list_reply,
    _parse_qq_workspace_command,
)
from companion_v01.channels.qq.identity import _qq_sender_role
from companion_v01.channels.qq.resources import (
    build_qq_resource_manifest_builder as _build_qq_resource_manifest_builder,
)
from companion_v01.desktop_pet_character_resources import load_character_care_shop_items
from companion_v01.local_capability_config import (
    approval_mode_for_capability,
    load_capability_config,
    save_capability_approval_modes,
)
from companion_v01.model_service_config import (
    effective_settings_from_config,
    effective_settings_from_runtime_settings,
    probe_model_ids,
    redact_provider_error,
)
from companion_v01.workspace_management import clear_workspace_files


def _is_qq_stop_command(value: Any) -> bool:
    command = resolve_qq_command(str(value or "").strip())
    return bool(command is not None and not command.error and command.definition.name == "停止")


def _group_command_target_status(qq_gateway: Any, context: Any, plugin_broker: Any = None) -> str:
    """A slash command in a group always requires a real target mention."""
    if not bool(getattr(context, "is_group", False)) or getattr(context, "inbound_message", None) is None:
        return "not_command"
    text = str(getattr(context, "clean_message", "") or "").strip()
    # A typed @name cannot grant targeting; it only helps classify commands so
    # quoting this Bot or a plugin cannot accidentally promote them to a turn.
    text = re.sub(r"^(?:@\S+\s+)+", "", text)
    if split_qq_command(text) is None:
        return "not_command"
    return "selected" if bool(getattr(context, "mentioned_bot", False)) else "unselected"


@dataclass
class QQCommandOutcome:
    response: JSONResponse | None = None
    action_note: str = ""
    message_override: str = ""


class QQCommandHandler:
    def __init__(
        self, *, engine, config_module, qq_gateway, runtime_metrics, log_event,
        dialogue_registry, plugin_command_broker_provider, plugin_conversation_ref_issuer,
        thinking_mode_setter, schedule_followup, send_reply, send_replies, resume_after_decision,
    ):
        self.engine = engine
        self.config_module = config_module
        self.qq_gateway = qq_gateway
        self.runtime_metrics = runtime_metrics
        self.log_event = log_event
        self.dialogue_registry = dialogue_registry
        self.plugin_command_broker_provider = plugin_command_broker_provider
        self.plugin_conversation_ref_issuer = plugin_conversation_ref_issuer
        self.thinking_mode_setter = thinking_mode_setter
        self.schedule_followup = schedule_followup
        self.send_reply = send_reply
        self.send_replies = send_replies
        self.resume_after_decision = resume_after_decision

        self.cards = QQCommandCards(engine=engine, config_module=config_module,
                                    qq_gateway=qq_gateway, send_reply=send_reply)

    def _local_command_response(self,
        context: Any, *, started_at: float, reason: str, command_status: str,
        command_ok: bool, send_result: dict[str, Any], **details: Any,
    ) -> JSONResponse:
        duration_ms = (time.perf_counter() - started_at) * 1000
        self.runtime_metrics.observe_request("qq_napcat_event", duration_ms=duration_ms,
                                        ok=bool(send_result.get("ok")) and command_ok)
        self.log_event(reason, session_id=context.session_id, profile_user_id=context.profile_user_id,
                  command_status=command_status, command_ok=command_ok,
                  sent=bool(send_result.get("ok")), duration_ms=round(duration_ms, 1), **details)
        return JSONResponse({"status": "ok" if send_result.get("ok") else "send_failed",
                             "reason": reason, "command_status": command_status, "command_ok": command_ok,
                             "session_id": context.session_id, "profile_user_id": context.profile_user_id,
                             "send_result": send_result, **details})

    def _help_document(self, context: Any, help_request: Any, broker: Any) -> Any:
        getter = getattr(self.engine, "get_care_module", None)
        care = getter() if callable(getter) else None
        try:
            master_id = int(getattr(self.config_module, "MASTER_QQ", getattr(self.qq_gateway, "master_qq", 0)) or 0)
        except (TypeError, ValueError):
            master_id = 0
        master_id = max(0, master_id)
        return build_qq_help(
            help_request, is_group=bool(context.is_group),
            is_master=not master_id or str(context.user_id) == str(master_id),
            master_configured=bool(master_id), care_enabled=bool(getattr(care, "enabled", False)),
            plugin_commands=tuple(getattr(broker, "registered_commands", ()) or ()),
            character_pack_id=str(getattr(context, "character_pack_id", "") or ""),
            directed_commands=self.dialogue_registry.help_commands(broker),
        )

    async def handle_early(self, *, context, command_context, request, started_at,
                           resolution: QQCommandResolution | None):
        registered_command = resolution
        if registered_command is not None and registered_command.error:
            send_result = await self.send_reply(context, registered_command.error)
            return self._local_command_response(context, started_at=started_at, reason="qq_command_error",
                                           command_status="invalid_arguments", command_ok=False, send_result=send_result)
        if registered_command is not None and registered_command.definition.name == "缓存":
            from datetime import date
            from pathlib import Path

            from companion_v01.cache_usage import conversation_cache_scope, read_daily_cache_usage
            from companion_v01.channels.qq.cards.cache import cache_report_text, render_qq_cache_card
            master = str(getattr(self.qq_gateway, "master_qq", "") or "")
            if master and str(context.user_id) != master:
                sent = await self.send_reply(context, "缓存统计只有主人可以查看。")
                return self._local_command_response(context, started_at=started_at, reason="qq_cache_usage",
                    command_status="forbidden", command_ok=False, send_result=sent)
            runtime = self.engine.llm
            log_dir = getattr(runtime, "log_dir", None)
            if log_dir is None:
                sent = await self.send_reply(context, "当前没有可用的缓存审计记录。")
                return self._local_command_response(context, started_at=started_at, reason="qq_cache_usage",
                    command_status="unavailable", command_ok=False, send_result=sent)
            try:
                report = await asyncio.to_thread(read_daily_cache_usage, Path(log_dir),
                    day=date.fromisoformat(registered_command.payload["day"]),
                    scope_hash=conversation_cache_scope(context.profile_user_id, context.session_id, context.character_pack_id),
                    instance_id=str(getattr(runtime, "instance_id", "")))
            except OSError as exc:
                sent = await self.send_reply(context, "暂时无法读取缓存统计，请稍后再试。")
                return self._local_command_response(context, started_at=started_at, reason="qq_cache_usage",
                    command_status="unavailable", command_ok=False, send_result=sent,
                    failure_reason=type(exc).__name__)
            document = {"report": report, "character_pack_id": context.character_pack_id, "is_group": context.is_group}
            sent, card_format, failure = await self.cards.render_and_deliver(context, document,
                renderer=render_qq_cache_card, cache_name="qq_cache_usage", title="缓存小记", fallback=cache_report_text(report))
            return self._local_command_response(context, started_at=started_at, reason="qq_cache_usage",
                command_status="shown", command_ok=True, send_result=sent, card_format=card_format,
                image_failure_reason=failure)
        if registered_command is not None and registered_command.help_request is not None:
            broker = (self.plugin_command_broker_provider() if self.plugin_command_broker_provider is not None
                      else getattr(request.app.state, "akane_plugin_command_broker", None))
            try:
                document = await asyncio.to_thread(self._help_document, context, registered_command.help_request, broker)
            except ValueError as exc:
                send_result = await self.send_reply(context, str(exc))
                return self._local_command_response(context, started_at=started_at, reason="qq_command_help",
                                               command_status="unavailable_page", command_ok=False, send_result=send_result)
            send_result, help_format, image_failure = await self.cards.present_help(
                context, document, text_only=registered_command.help_request.text_only)
            return self._local_command_response(context, started_at=started_at, reason="qq_command_help",
                                           command_status="shown", command_ok=True, send_result=send_result,
                                           help_format=help_format, image_failure_reason=image_failure,
                                           help_page=document.page, help_page_count=document.page_count)
        return None

    async def _reply_to_result(
        self, context, result, *, reason, started_at,
        metrics_require_command_ok=False, response_requires_command_ok=False,
        log_fields=None, response_fields=None,
    ) -> JSONResponse:
        reply = str(result.get("reply") or "").strip()
        sent = await self.send_reply(context, reply) if reply else {"ok": False, "reason": "empty_reply"}
        command_ok = bool(result.get("ok"))
        delivery_ok = bool(sent.get("ok"))
        command_status = str(result.get("status") or "")
        duration_ms = (time.perf_counter() - started_at) * 1000
        self.runtime_metrics.observe_request(
            "qq_napcat_event", duration_ms=duration_ms,
            ok=delivery_ok and (command_ok or not metrics_require_command_ok),
        )
        self.log_event(
            reason, session_id=context.session_id, profile_user_id=context.profile_user_id,
            command_status=command_status, command_ok=command_ok, sent=delivery_ok,
            duration_ms=round(duration_ms, 1), **(log_fields or {}),
        )
        return JSONResponse({
            "status": "ok" if delivery_ok and (command_ok or not response_requires_command_ok) else "send_failed",
            "reason": reason, "command_status": command_status, "command_ok": command_ok,
            "session_id": context.session_id, "profile_user_id": context.profile_user_id,
            "send_result": sent, **(response_fields or {}),
        })

    async def _handle_group_vision(self, *, context, command_context, event, started_at, command=None):
        group_vision_command_result = self.qq_gateway.handle_group_vision_command(
            command_context,
            command=command,
            sender_role=_qq_sender_role(event),
        )
        if isinstance(group_vision_command_result, dict):
            return await self._reply_to_result(
                context, group_vision_command_result, reason='qq_group_vision_command', started_at=started_at,
                metrics_require_command_ok=True,
                log_fields={'vision_enabled': bool(group_vision_command_result.get('vision_enabled')), 'state_persisted': group_vision_command_result.get('state_persisted')},
                response_fields={'vision_enabled': bool(group_vision_command_result.get('vision_enabled')), 'state_persisted': group_vision_command_result.get('state_persisted')},
            )

    async def _handle_private_memory(self, *, context, command_context, started_at, command=None):
        if command is None:
            cmd = resolve_qq_command(command_context.clean_message)
            if cmd is None or cmd.definition.kind != "private_memory":
                return None
            payload = cmd.payload or {}
            has_error = bool(cmd.error)
        else:
            payload = command
            has_error = False
        mirror = getattr(self.engine, "owner_private_group_memory", None)
        queue_status = await asyncio.to_thread(mirror.status, f"qq_group_shared_{command_context.group_id}") if (
            mirror and not has_error and payload.get("action") == "status"
            and command_context.is_group and str(command_context.user_id) == self.qq_gateway.master_qq) else None
        result = await asyncio.to_thread(self.qq_gateway.handle_private_memory_command, command_context,
                                         command=command,
                                         available=bool(mirror and mirror.enabled), queue_status=queue_status)
        if isinstance(result, dict):
            return await self._reply_to_result(
                context, result, reason="qq_private_memory_command", started_at=started_at,
                metrics_require_command_ok=True, response_requires_command_ok=True,
            )

    async def _handle_group_emotion(self, *, context, command_context, event, started_at, command=None):
        group_emotion_command_result = self.qq_gateway.handle_group_emotion_command(
            command_context,
            command=command,
            sender_role=_qq_sender_role(event),
        )
        if isinstance(group_emotion_command_result, dict):
            return await self._reply_to_result(
                context, group_emotion_command_result, reason='qq_group_emotion_command', started_at=started_at,
                metrics_require_command_ok=True,
                response_requires_command_ok=True,
                log_fields={'emotion_enabled': bool(group_emotion_command_result.get('emotion_enabled')), 'state_persisted': group_emotion_command_result.get('state_persisted')},
                response_fields={'emotion_enabled': bool(group_emotion_command_result.get('emotion_enabled')), 'state_persisted': group_emotion_command_result.get('state_persisted')},
            )

    async def _handle_group_attention(self, *, context, command_context, event, started_at, command=None):
        group_attention_command_result = self.qq_gateway.handle_group_attention_command(
            command_context,
            command=command,
            sender_role=_qq_sender_role(event),
            default_mode=str(getattr(self.config_module, "QQ_GROUP_ATTENTION_MODE", "engaged") or "engaged"),
        )
        if isinstance(group_attention_command_result, dict):
            return await self._reply_to_result(
                context, group_attention_command_result, reason='qq_group_attention_command', started_at=started_at,
                metrics_require_command_ok=True,
                response_requires_command_ok=True,
                log_fields={'attention_mode': str(group_attention_command_result.get('attention_mode') or ''), 'state_persisted': group_attention_command_result.get('state_persisted')},
                response_fields={'attention_mode': str(group_attention_command_result.get('attention_mode') or ''), 'state_persisted': group_attention_command_result.get('state_persisted')},
            )

    async def _handle_access_permission(self, *, context, command_context, started_at, command=None):
        access_permission_command = command if command is not None else self.qq_gateway.parse_access_permission_command(context.clean_message)
        if isinstance(access_permission_command, dict):
            capability_config_base_dir = getattr(
                self.engine,
                "capability_config_base_dir",
                getattr(self.config_module, "DATA_DIR", None),
            )
            policy_profile_user_id = str(
                getattr(context, "actor_profile_user_id", "") or context.profile_user_id
            )
            policy_payload = load_capability_config(
                base_dir=capability_config_base_dir,
                profile_user_id=policy_profile_user_id,
            )
            current_modes = {
                family_id: approval_mode_for_capability(
                    policy_payload.get("approvalPolicy"),
                    "",
                    family_id=family_id,
                )
                for family_id in ("ops", "extensions")
            }

            def _save_access_modes(modes: dict[str, str]) -> dict[str, str]:
                saved = save_capability_approval_modes(
                    base_dir=capability_config_base_dir,
                    profile_user_id=policy_profile_user_id,
                    modes=modes,
                )
                if not saved.get("ok"):
                    raise RuntimeError(str(saved.get("reason") or "access_permission_save_failed"))
                return dict(saved.get("approvalModes") or {})

            access_result = self.qq_gateway.handle_access_permission_command(
                command_context,
                command=access_permission_command,
                current_modes=current_modes,
                apply_modes=_save_access_modes,
            )
            if isinstance(access_result, dict):
                reply = str(access_result.get("reply") or "").strip()
                send_result = await self.send_reply(context, reply) if reply else {"ok": False, "reason": "empty_reply"}
                command_ok = bool(access_result.get("ok"))
                duration_ms = (time.perf_counter() - started_at) * 1000
                self.runtime_metrics.observe_request("qq_napcat_event", duration_ms=duration_ms, ok=bool(send_result.get("ok")) and command_ok)
                self.log_event(
                    "qq_access_permission_command",
                    session_id=context.session_id,
                    profile_user_id=context.profile_user_id,
                    policy_profile_user_id=policy_profile_user_id,
                    command_status=str(access_result.get("status") or ""),
                    command_ok=command_ok,
                    scope=str(access_permission_command.get("scope") or ""),
                    sent=bool(send_result.get("ok")),
                    duration_ms=round(duration_ms, 1),
                )
                return JSONResponse(
                    {
                        "status": "ok" if send_result.get("ok") else "send_failed",
                        "reason": "qq_access_permission_command",
                        "command_status": str(access_result.get("status") or ""),
                        "command_ok": command_ok,
                        "modes": dict(access_result.get("modes") or {}),
                        "session_id": context.session_id,
                        "profile_user_id": context.profile_user_id,
                        "send_result": send_result,
                    }
                )

    async def _handle_capability_approval(self, *, context, command_context, event, started_at, command=None):
        capability_approval_command = command if command is not None else self.qq_gateway.parse_capability_approval_command(context.clean_message)
        if isinstance(capability_approval_command, dict):
            approval_store_getter = getattr(self.engine, "_get_approval_store", None)
            approval_store = (
                approval_store_getter()
                if callable(approval_store_getter)
                else getattr(self.engine, "approval_store", None)
            )
            approval_result = self.qq_gateway.handle_capability_approval_command(
                command_context,
                command=capability_approval_command,
                approval_store=approval_store,
            )
            if isinstance(approval_result, dict):
                reply = str(approval_result.get("reply") or "").strip()
                send_result = (
                    await self.send_reply(context, reply)
                    if reply
                    else {"ok": False, "reason": "empty_reply"}
                )
                command_ok = bool(approval_result.get("ok"))
                duration_ms = (time.perf_counter() - started_at) * 1000
                self.runtime_metrics.observe_request(
                    "qq_napcat_event",
                    duration_ms=duration_ms,
                    ok=bool(send_result.get("ok")) and command_ok,
                )
                self.log_event(
                    "qq_capability_approval_command",
                    session_id=context.session_id,
                    profile_user_id=context.profile_user_id,
                    is_group=bool(context.is_group),
                    command_status=str(approval_result.get("status") or ""),
                    command_ok=command_ok,
                    request_id_suffix=str(approval_result.get("request_id") or "")[-8:],
                    sent=bool(send_result.get("ok")),
                    duration_ms=round(duration_ms, 1),
                )
                decision = str(approval_result.get("status") or "")
                if command_ok and send_result.get("ok") and decision in {"approved", "denied"}:
                    self.schedule_followup(
                        self.resume_after_decision(
                            context=context,
                            event=event,
                            decision=decision,
                            capability_id=str(approval_result.get("capability_id") or ""),
                            action_id=str(approval_result.get("action_id") or ""),
                            authorization_profile_user_id=str(
                                approval_result.get("authorization_profile_user_id") or ""
                            ),
                        )
                    )
                return JSONResponse(
                    {
                        "status": "ok" if send_result.get("ok") else "send_failed",
                        "reason": "qq_capability_approval_command",
                        "command_status": str(approval_result.get("status") or ""),
                        "command_ok": command_ok,
                        "session_id": context.session_id,
                        "profile_user_id": context.profile_user_id,
                        "send_result": send_result,
                    }
                )

    async def _handle_mface(self, *, context, event, started_at, command=None):
        mface_config_result = self.qq_gateway.handle_mface_config_command(context, event, command=command)
        if isinstance(mface_config_result, dict):
            return await self._reply_to_result(
                context, mface_config_result, reason='qq_mface_config_command', started_at=started_at,
                log_fields={'character_pack_id': str(mface_config_result.get('character_pack_id') or ''), 'emotion': str(mface_config_result.get('emotion') or '')},
                response_fields={'character_pack_id': str(mface_config_result.get('character_pack_id') or ''), 'emotion': str(mface_config_result.get('emotion') or ''), 'mface': mface_config_result.get('mface')},
            )

    async def _handle_workspace(self, *, context, event, started_at, command=None):
        workspace_command = command if command is not None else _parse_qq_workspace_command(self.qq_gateway, context.clean_message)
        if isinstance(workspace_command, dict):
            action = str(workspace_command.get("action") or "").strip()
            command_status = "ok"
            command_ok = True
            if action == "list":
                reply = _build_qq_workspace_list_reply(
                    self.engine,
                    profile_user_id=context.profile_user_id,
                    session_id=context.session_id,
                )
            elif action == "clear":
                clear_result = clear_workspace_files(
                    self.engine,
                    profile_user_id=context.profile_user_id,
                    session_id=context.session_id,
                    character_pack_id=str(getattr(context, "character_pack_id", "") or ""),
                    target=str(workspace_command.get("target") or "current"),
                    kind=str(workspace_command.get("kind") or "any"),
                    reason="用户通过 QQ 工作台指令清理当前文件工作台。",
                    delete_storage=bool(workspace_command.get("delete_storage")),
                    timestamp=int(event.get("time") or time.time()),
                )
                command_ok = bool(clear_result.get("ok")) if isinstance(clear_result, dict) else False
                command_status = (
                    str(clear_result.get("status") or "failed")
                    if isinstance(clear_result, dict)
                    else "failed"
                )
                reply = _build_qq_workspace_clear_reply(
                    clear_result if isinstance(clear_result, dict) else {},
                    delete_storage=bool(workspace_command.get("delete_storage")),
                )
            else:
                command_status = "unknown_action"
                command_ok = False
                reply = "这个工作台指令暂时不支持。发送 /help 材料 查看用法。"
            send_result = (
                await self.send_reply(context, reply)
                if reply
                else {"ok": False, "reason": "empty_reply"}
            )
            duration_ms = (time.perf_counter() - started_at) * 1000
            self.runtime_metrics.observe_request(
                "qq_napcat_event",
                duration_ms=duration_ms,
                ok=bool(send_result.get("ok")) and command_ok,
            )
            self.log_event(
                "qq_workspace_command",
                session_id=context.session_id,
                profile_user_id=context.profile_user_id,
                command_action=action,
                command_status=command_status,
                command_ok=command_ok,
                sent=bool(send_result.get("ok")),
                duration_ms=round(duration_ms, 1),
            )
            return JSONResponse(
                {
                    "status": "ok" if send_result.get("ok") else "send_failed",
                    "reason": "qq_workspace_command",
                    "command_action": action,
                    "command_status": command_status,
                    "command_ok": command_ok,
                    "session_id": context.session_id,
                    "profile_user_id": context.profile_user_id,
                    "send_result": send_result,
                }
            )

    async def _handle_character(self, *, context, command_context, started_at, command=None):
        character_command_result = self.qq_gateway.handle_character_command(
            command_context,
            command=command,
            character_resource_service=getattr(self.engine, "desktop_pet_character_resources", None),
        )
        if isinstance(character_command_result, dict):
            return await self._reply_to_result(
                context, character_command_result, reason='qq_character_command', started_at=started_at,
                log_fields={'character_pack_id': str(character_command_result.get('character_pack_id') or ''), 'state_persisted': character_command_result.get('state_persisted')},
                response_fields={'character_pack_id': str(character_command_result.get('character_pack_id') or ''), 'state_persisted': character_command_result.get('state_persisted')},
            )

    async def _handle_outfit(self, *, context, command_context, started_at, outcome, command=None):
        outfit_command_result = self.qq_gateway.handle_outfit_command(
            command_context,
            command=command,
            resource_manifest_builder=_build_qq_resource_manifest_builder(self.engine, context),
        )
        if isinstance(outfit_command_result, dict):
            if outfit_command_result.get("_llm_passthrough"):
                note = str(outfit_command_result.get("qq_action_note") or "").strip()
                outcome.action_note = "\n".join(part for part in [outcome.action_note, note] if part)
                turn_msg = str(outfit_command_result.get("turn_message") or "").strip()
                if turn_msg:
                    outcome.message_override = turn_msg
                self.log_event(
                    "qq_outfit_command",
                    session_id=context.session_id,
                    profile_user_id=context.profile_user_id,
                    command_status=str(outfit_command_result.get("status") or ""),
                    command_ok=bool(outfit_command_result.get("ok")),
                    character_pack_id=str(outfit_command_result.get("character_pack_id") or ""),
                    outfit_id=str(outfit_command_result.get("outfit_id") or ""),
                    state_persisted=outfit_command_result.get("state_persisted"),
                    llm_passthrough=True,
                    sent=False,
                )
                return None
            else:
                reply = str(outfit_command_result.get("reply") or "").strip()
                send_result = (
                    await self.send_reply(context, reply)
                    if reply
                    else {"ok": False, "reason": "empty_reply"}
                )
                duration_ms = (time.perf_counter() - started_at) * 1000
                self.runtime_metrics.observe_request(
                    "qq_napcat_event",
                    duration_ms=duration_ms,
                    ok=bool(send_result.get("ok")),
                )
                self.log_event(
                    "qq_outfit_command",
                    session_id=context.session_id,
                    profile_user_id=context.profile_user_id,
                    command_status=str(outfit_command_result.get("status") or ""),
                    command_ok=bool(outfit_command_result.get("ok")),
                    character_pack_id=str(outfit_command_result.get("character_pack_id") or ""),
                    outfit_id=str(outfit_command_result.get("outfit_id") or ""),
                    state_persisted=outfit_command_result.get("state_persisted"),
                    sent=bool(send_result.get("ok")),
                    duration_ms=round(duration_ms, 1),
                )
                return JSONResponse(
                    {
                        "status": "ok" if send_result.get("ok") else "send_failed",
                        "reason": "qq_outfit_command",
                        "command_status": str(outfit_command_result.get("status") or ""),
                        "command_ok": bool(outfit_command_result.get("ok")),
                        "session_id": context.session_id,
                        "profile_user_id": context.profile_user_id,
                        "character_pack_id": str(outfit_command_result.get("character_pack_id") or ""),
                        "outfit_id": str(outfit_command_result.get("outfit_id") or ""),
                        "state_persisted": outfit_command_result.get("state_persisted"),
                        "send_result": send_result,
                    }
                )
        reply = "服装指令处理失败，请稍后再试。"
        send_result = await self.send_reply(context, reply)
        return self._local_command_response(
            context, started_at=started_at, reason="qq_outfit_command",
            command_status="unexpected_result", command_ok=False, send_result=send_result,
        )

    async def _handle_reply_mode(self, *, context, started_at, command=None):
        reply_mode_command_result = self.qq_gateway.handle_reply_mode_command(context, command=command)
        if isinstance(reply_mode_command_result, dict):
            return await self._reply_to_result(
                context, reply_mode_command_result, reason='qq_reply_mode_command', started_at=started_at,
                log_fields={'reply_mode': str(reply_mode_command_result.get('reply_mode') or '')},
                response_fields={'reply_mode': str(reply_mode_command_result.get('reply_mode') or '')},
            )

    async def _handle_thinking_mode(self, *, context, command_context, started_at, command=None):
        thinking_mode_command = command if command is not None else self.qq_gateway.parse_thinking_mode_command(context.clean_message)
        if isinstance(thinking_mode_command, dict):
            llm_runtime = getattr(self.engine, "llm", None)
            try:
                supports_thinking = bool(
                    llm_runtime is not None
                    and callable(getattr(llm_runtime, "chat_supports_deepseek_thinking", None))
                    and llm_runtime.chat_supports_deepseek_thinking(
                        chat_model_override=str(getattr(context, "chat_model_override", "") or "")
                    )
                )
            except Exception:
                supports_thinking = False
            thinking_mode_command_result = self.qq_gateway.handle_thinking_mode_command(
                command_context,
                command=thinking_mode_command,
                current_mode=str(
                    getattr(getattr(self.engine, "settings", None), "llm_thinking_mode", "disabled")
                    or "disabled"
                ),
                supported=supports_thinking,
                apply_mode=self.thinking_mode_setter,
            )
            if isinstance(thinking_mode_command_result, dict):
                return await self._reply_to_result(
                    context, thinking_mode_command_result, reason='qq_thinking_mode_command', started_at=started_at,
                    metrics_require_command_ok=True,
                    log_fields={'thinking_mode': str(thinking_mode_command_result.get('thinking_mode') or ''), 'supported': bool(thinking_mode_command_result.get('supported'))},
                    response_fields={'thinking_mode': str(thinking_mode_command_result.get('thinking_mode') or ''), 'supported': bool(thinking_mode_command_result.get('supported'))},
                )

    async def _handle_chat_model(self, *, context, command_context, started_at, command=None):
        chat_model_command = command if command is not None else self.qq_gateway.parse_chat_model_command(context.clean_message)
        if isinstance(chat_model_command, dict):
            available_models: list[str] | None = None
            list_error = ""
            if str(chat_model_command.get("action") or "") in {"list", "switch"}:
                try:
                    runtime_settings = getattr(self.engine, "settings", None)
                    settings = (
                        effective_settings_from_runtime_settings(runtime_settings)
                        if runtime_settings is not None
                        else effective_settings_from_config(self.config_module)
                    )
                    available_models = await asyncio.to_thread(probe_model_ids, settings)
                except Exception as exc:
                    list_error = redact_provider_error(exc)
                    available_models = []
            chat_model_command_result = self.qq_gateway.handle_chat_model_command(
                command_context,
                command=chat_model_command,
                default_model=str(
                    getattr(getattr(self.engine, "settings", None), "chat_model_name", "")
                    or getattr(self.config_module, "CHAT_MODEL_NAME", "")
                    or ""
                ),
                available_models=available_models,
                list_error=list_error,
            )
            if isinstance(chat_model_command_result, dict):
                if chat_model_command_result.get("ok") and chat_model_command_result.get("status") in {"switched", "default"}:
                    manager = getattr(self.engine, "memcore_manager", None)
                    update_model = getattr(manager, "set_compaction_chat_model_override", None)
                    if callable(update_model):
                        update_model(
                            profile_user_id=context.profile_user_id,
                            session_id=context.session_id,
                            character_pack_id=context.character_pack_id,
                            chat_model_override=str(chat_model_command_result.get("chat_model_override") or ""),
                        )
                return await self._reply_to_result(
                    context, chat_model_command_result, reason='qq_chat_model_command', started_at=started_at,
                    log_fields={'chat_model': str(chat_model_command_result.get('chat_model') or ''), 'has_model_override': bool(chat_model_command_result.get('chat_model_override'))},
                    response_fields={'chat_model': str(chat_model_command_result.get('chat_model') or ''), 'has_model_override': bool(chat_model_command_result.get('chat_model_override'))},
                )

    async def _handle_economy(self, *, context, command_context, event, started_at, outcome, command=None):
        _care_module_getter = getattr(self.engine, "get_care_module", None)
        _care_module = _care_module_getter() if callable(_care_module_getter) else None
        _char_resources = getattr(self.engine, "desktop_pet_character_resources", None)
        _shop_items = (
            load_character_care_shop_items(_char_resources, context.character_pack_id)
            if _care_module is not None and _care_module.enabled and _char_resources and context.character_pack_id
            else None
        )
        economy_command_result = self.qq_gateway.handle_economy_command(
            command_context,
            command=command,
            care_module=_care_module,
            shop_items=_shop_items,
            now_ms=int(time.time() * 1000),
        )
        if isinstance(economy_command_result, dict):
            if economy_command_result.get("_llm_passthrough"):
                # Economy action processed; hand off to LLM for the actual reply
                economy_note = str(economy_command_result.get("qq_action_note") or "").strip()
                outcome.action_note = "\n".join(part for part in [outcome.action_note, economy_note] if part)
                economy_turn_message = str(economy_command_result.get("turn_message") or "").strip()
                if economy_turn_message:
                    outcome.message_override = economy_turn_message
                return None
            else:
                reply = str(economy_command_result.get("reply") or "").strip()
                card_format, image_failure = "text", ""
                card_data = economy_command_result.get("care_card")
                commerce_data = economy_command_result.get("commerce_card")
                if economy_command_result.get("ok") and (isinstance(card_data, dict) or isinstance(commerce_data, dict)):
                    card_sender = event.get("sender") if isinstance(event.get("sender"), dict) else {}
                    card_sender_fn = self.cards.present_care if isinstance(card_data, dict) else self.cards.present_commerce
                    send_result, card_format, image_failure = await card_sender_fn(
                        context, card_data if isinstance(card_data, dict) else commerce_data, reply,
                        user_label=(str(context.sender_label or "").strip() if context.is_group
                                    else str(card_sender.get("nickname") or "").strip()),
                    )
                else:
                    send_result = (await self.send_reply(context, reply)
                                   if reply else {"ok": False, "reason": "empty_reply"})
                duration_ms = (time.perf_counter() - started_at) * 1000
                self.runtime_metrics.observe_request(
                    "qq_napcat_event",
                    duration_ms=duration_ms,
                    ok=bool(send_result.get("ok")),
                )
                self.log_event(
                    "qq_economy_command",
                    session_id=context.session_id,
                    profile_user_id=context.profile_user_id,
                    command_status=str(economy_command_result.get("status") or ""),
                    command_ok=bool(economy_command_result.get("ok")),
                    card_format=card_format,
                    image_failure_reason=image_failure,
                    sent=bool(send_result.get("ok")),
                    duration_ms=round(duration_ms, 1),
                )
                return JSONResponse(
                    {
                        "status": "ok" if send_result.get("ok") else "send_failed",
                        "reason": "qq_economy_command",
                        "command_status": str(economy_command_result.get("status") or ""),
                        "command_ok": bool(economy_command_result.get("ok")),
                        "card_format": card_format,
                        "image_failure_reason": image_failure,
                        "session_id": context.session_id,
                        "profile_user_id": context.profile_user_id,
                        "send_result": send_result,
                    }
                )
        reply = "养成系统处理失败，请稍后再试。"
        send_result = await self.send_reply(context, reply)
        return self._local_command_response(
            context, started_at=started_at, reason="qq_economy_command",
            command_status="unexpected_result", command_ok=False, send_result=send_result,
        )

    async def _handle_plugin(self, *, context, event, request, started_at, command_input):
        _plugin_command_broker = (
            self.plugin_command_broker_provider()
            if self.plugin_command_broker_provider is not None
            else getattr(request.app.state, "akane_plugin_command_broker", None)
        )
        if _plugin_command_broker is not None and (command_parts := split_qq_command(command_input)) is not None:
            _cmd_token, _cmd_args = "/" + command_parts[0], command_parts[1]
            if _plugin_command_broker.handles(_cmd_token):
                _source_event_id = str(event.get("message_id") or "").strip()
                if not _source_event_id:
                    _source_event_id = ":".join(
                        (
                            str(event.get("time") or ""),
                            str(context.user_id or 0),
                            str(context.group_id or 0),
                            _cmd_token,
                        )
                    )
                _cmd_result = await _plugin_command_broker.dispatch(
                    command=_cmd_token,
                    args=_cmd_args,
                    qq_number=int(context.user_id or 0),
                    group_id=int(context.group_id or 0),
                    is_group=bool(context.is_group),
                    idempotency_key=_source_event_id,
                    sender_role=_qq_sender_role(event),
                    profile_user_id=str(context.profile_user_id or ""),
                    session_id=str(context.session_id or ""),
                    character_pack_id=str(getattr(context, "character_pack_id", "") or ""),
                    conversation_ref=(
                        str(self.plugin_conversation_ref_issuer(
                            profile_user_id=context.profile_user_id,
                            session_id=context.session_id,
                            character_pack_id=context.character_pack_id,
                            user_id=context.user_id,
                            group_id=context.group_id,
                            actor_stable_id=f"qq:{context.user_id}" if context.is_group else "",
                            actor_profile_user_id=str(context.actor_profile_user_id or ""),
                        ) or "")
                        if self.plugin_conversation_ref_issuer is not None
                        else ""
                    ),
                )
                if _cmd_result.handled:
                    if _cmd_result.reply_text:
                        send_result = await self.send_replies(context, [_cmd_result.reply_text])
                    else:
                        send_result = {
                            "ok": True,
                            "status": "no_reply",
                            "reason": "plugin_requested_no_reply",
                            "count": 0,
                            "results": [],
                        }
                    command_ok = not bool(_cmd_result.reason)
                    duration_ms = (time.perf_counter() - started_at) * 1000
                    self.runtime_metrics.observe_request(
                        "qq_napcat_event",
                        duration_ms=duration_ms,
                        ok=bool(send_result.get("ok")) and command_ok,
                    )
                    self.log_event(
                        "qq_plugin_command",
                        session_id=context.session_id,
                        profile_user_id=context.profile_user_id,
                        command=_cmd_token,
                        command_ok=command_ok,
                        command_status=str(_cmd_result.reason or "ok"),
                        sent=bool(send_result.get("ok")) and bool(_cmd_result.reply_text),
                        duration_ms=round(duration_ms, 1),
                    )
                    return JSONResponse(
                        {
                            "status": "ok" if send_result.get("ok") else "send_failed",
                            "reason": "qq_plugin_command",
                            "command_status": str(_cmd_result.reason or "ok"),
                            "command_ok": command_ok,
                            "session_id": context.session_id,
                            "profile_user_id": context.profile_user_id,
                            "send_result": send_result,
                        }
                    )

    async def handle(self, *, context, command_context, event, request, started_at,
                     resolution: QQCommandResolution | None,
                     action_note="", message_override="") -> QQCommandOutcome:
        outcome = QQCommandOutcome(action_note=action_note, message_override=message_override)
        command_input = str(command_context.clean_message or "").strip()
        res = resolution
        if res is not None and not res.error:
            payload = dict(res.payload) if res.payload is not None else {}
            match res.definition.name:
                case "私人记忆":
                    outcome.response = await self._handle_private_memory(
                        context=context, command_context=command_context, started_at=started_at, command=payload,
                    )
                case "识图":
                    outcome.response = await self._handle_group_vision(
                        context=context, command_context=command_context, event=event, started_at=started_at, command=payload,
                    )
                case "表情":
                    outcome.response = await self._handle_group_emotion(
                        context=context, command_context=command_context, event=event, started_at=started_at, command=payload,
                    )
                case "搭话":
                    outcome.response = await self._handle_group_attention(
                        context=context, command_context=command_context, event=event, started_at=started_at, command=payload,
                    )
                case "权限":
                    outcome.response = await self._handle_access_permission(
                        context=context, command_context=command_context, started_at=started_at, command=payload,
                    )
                case "审批" | "批准" | "拒绝":
                    outcome.response = await self._handle_capability_approval(
                        context=context, command_context=command_context, event=event, started_at=started_at, command=payload,
                    )
                case "表情包":
                    outcome.response = await self._handle_mface(
                        context=context, event=event, started_at=started_at, command=payload,
                    )
                case "材料":
                    outcome.response = await self._handle_workspace(
                        context=context, event=event, started_at=started_at, command=payload,
                    )
                case "角色":
                    outcome.response = await self._handle_character(
                        context=context, command_context=command_context, started_at=started_at, command=payload,
                    )
                case "服装":
                    outcome.response = await self._handle_outfit(
                        context=context, command_context=command_context, started_at=started_at, outcome=outcome, command=payload,
                    )
                    return outcome
                case "回复":
                    outcome.response = await self._handle_reply_mode(
                        context=context, started_at=started_at, command=payload,
                    )
                case "思考":
                    outcome.response = await self._handle_thinking_mode(
                        context=context, command_context=command_context, started_at=started_at, command=payload,
                    )
                case "模型":
                    outcome.response = await self._handle_chat_model(
                        context=context, command_context=command_context, started_at=started_at, command=payload,
                    )
                case "签到" | "养成" | "商店" | "购买" | "背包" | "投喂" | "供奉" | "抽签" | "赛钱" | "赛钱箱" | "额度":
                    outcome.response = await self._handle_economy(
                        context=context, command_context=command_context, event=event, started_at=started_at, outcome=outcome, command=payload,
                    )
                    return outcome
                case "停止":
                    return outcome

            if outcome.response is None:
                send_result = await self.send_reply(context, f"/{res.definition.name} 处理失败，请稍后再试。")
                outcome.response = self._local_command_response(
                    context, started_at=started_at, reason="qq_command_error",
                    command_status="unexpected_result", command_ok=False, send_result=send_result,
                )
            return outcome

        if split_qq_command(command_input) is not None:
            plugin_response = await self._handle_plugin(
                context=context, event=event, request=request, started_at=started_at, command_input=command_input,
            )
            if plugin_response is not None:
                outcome.response = plugin_response
                return outcome
            reply = "未识别这条指令。发送 /help 查看用法。"
            send_result = await self.send_reply(context, reply)
            outcome.response = self._local_command_response(
                context, started_at=started_at, reason="qq_command_error",
                command_status="unknown_command", command_ok=False, send_result=send_result,
            )
            return outcome

        return outcome
