from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable, Mapping

from channelcore_onebot import EventAdmissionConfig, MentionRef, OneBotEventAdmission, OutboundAction

import config
from .care_runtime import CareModulePort, DEFAULT_CHECKIN_COINS
from .deployment_security import QQChannelRuntimeConfig
from .onebot_transport import OneBotActionTransport
from .plugin_api import PluginHookEnvelope
from .qq_poke_reactor import PokeEventReactor, PokeOutcome
from .qq_miniapp import MiniappCardStore
from .qq_commands import resolve_qq_command
from .qq_gateway_runtime.context import QQMessageContext
from .qq_gateway_runtime.outbound import QQOutboundRuntime
from .qq_gateway_runtime.messages import QQMessageDelivery
from .qq_gateway_runtime.media import QQMediaDelivery
from .qq_gateway_runtime.emotions import QQEmotionDelivery
from .qq_gateway_runtime.emotions import (
    _normalize_mface_payload,
    _normalize_emotion_mface_config,
    _resolve_emotion_mface,
    _mface_fingerprint,
)
from .qq_gateway_runtime.identity import QQIdentityRuntime
from .qq_gateway_runtime.evidence import QQEvidenceResolver
from .qq_gateway_runtime.inbound import QQInboundRuntime
from .qq_gateway_runtime.care import QQCareCommands
from .qq_gateway_runtime.care_results import (
    _item_usable_in_qq,
    _poke_actor_label,
    _poke_scope_keys,
    _build_poke_outcome,
    _render_poke_mutation_outcome,
    _render_poke_effects,
    _poke_seasonal_note,
    _signed_number,
    _item_usable_as_offering,
    _commerce_listing,
    _merge_shop_items,
    _parse_item_and_quantity,
    _find_shop_item,
    _format_effects_summary,
)
from .qq_gateway_runtime.characters import QQCharacterCommands
from .qq_gateway_runtime.models import QQModelCommands
from .qq_gateway_runtime.administration import QQAdministrationCommands
from .qq_gateway_runtime.preferences import QQSessionPreferences
from .qq_gateway_runtime.definitions import (
    QQ_TEXT_CAPABILITIES,
    QQ_MUSIC_PLATFORM_TO_ONEBOT,
    QQ_CHARACTER_PACK_ID_RE,
    QQ_CHARACTER_BUILTIN_IDS,
    QQ_INLINE_IMAGE_MAX_BYTES,
    QQ_INLINE_FILE_MAX_BYTES,
    QQ_FILE_NAME_MAX_UTF8_BYTES,
    QQ_REPLY_MODES,
    QQ_REPLY_MODE_LABELS,
    QQ_GATEWAY_STATE_SCHEMA_VERSION,
    _QTY_RE,
    VALID_USABLE_IN,
)
from .qq_gateway_runtime.validation import (
    _safe_qq_file_name,
    _safe_character_pack_id,
    _safe_qq_session_key,
    _safe_outfit_id,
    _safe_chat_model_id,
    _chat_model_route_prefix,
    _chat_model_base_id,
    _outfit_lookup_key,
    _safe_reply_mode,
)


class NapCatQQGateway:
    def __init__(
        self,
        *,
        state_path: str | Path | None = None,
        channel_config: QQChannelRuntimeConfig | None = None,
        default_character_pack_id: str = "",
        wake_words: tuple[str, ...] | list[str] | None = None,
        poke_reactor: PokeEventReactor | None = None,
    ) -> None:
        self._channel_config = channel_config
        transport_config = channel_config or QQChannelRuntimeConfig(
            enabled=bool(getattr(config, "QQ_BRIDGE_ENABLED", False)),
            profile_ref="",
            bot_id=str(getattr(config, "QQ_BOT_QQ", "") or "").strip(),
            onebot_http_url=str(getattr(config, "QQ_ONEBOT_HTTP_URL", "http://127.0.0.1:3001") or "").strip(),
            webhook_secret="",
            onebot_access_token="",
            require_webhook_auth=False,
            require_self_id=False,
        )
        self._onebot_transport = OneBotActionTransport(transport_config)
        self._bound_default_character_pack_id = _safe_character_pack_id(default_character_pack_id)
        # Legacy config supplies optional command prefixes, never admission.
        self.poke_reactor = poke_reactor or PokeEventReactor()
        require_self_id = self._channel_config.require_self_id if self._channel_config is not None else False
        self._event_admission = OneBotEventAdmission(
            EventAdmissionConfig(
                bot_account_id=self.bot_qq,
                require_self_id=require_self_id,
            )
        )
        self._preferences = QQSessionPreferences(
            state_path=state_path,
            default_character_pack_id=lambda: self.default_character_pack_id,
            default_reply_mode=lambda: self.default_reply_mode,
        )
        self._state_path = self._preferences._state_path
        self.character_pack_overrides = self._preferences.character_pack_overrides
        self._character_pack_lock = self._preferences._character_pack_lock
        self.outfit_overrides = self._preferences.outfit_overrides
        self._outfit_lock = self._preferences._outfit_lock
        self.reply_mode_overrides = self._preferences.reply_mode_overrides
        self._reply_mode_lock = self._preferences._reply_mode_lock
        self.chat_model_overrides = self._preferences.chat_model_overrides
        self._chat_model_lock = self._preferences._chat_model_lock
        self.group_vision_overrides = self._preferences.group_vision_overrides
        self._group_vision_lock = self._preferences._group_vision_lock
        self.group_emotion_overrides = self._preferences.group_emotion_overrides
        self._group_emotion_lock = self._preferences._group_emotion_lock
        self.group_attention_mode_overrides = self._preferences.group_attention_mode_overrides
        self._group_attention_mode_lock = self._preferences._group_attention_mode_lock
        self._care = QQCareCommands(gateway=self, poke_reactor=self.poke_reactor)
        self._identity = QQIdentityRuntime(gateway=self)
        self.sender_label_cache = self._identity.sender_label_cache
        self._bot_group_labels = self._identity._bot_group_labels
        self._bot_nickname_lock = self._identity._bot_nickname_lock
        self._delivery_notes = self._identity._delivery_notes
        self._delivery_notes_lock = self._identity._delivery_notes_lock
        self._evidence = QQEvidenceResolver(gateway=self)
        self._inbound = QQInboundRuntime(gateway=self, admission=self._event_admission)
        self.attachment_debounce_state = self._inbound.attachment_debounce_state
        self._attachment_debounce_lock = self._inbound._attachment_debounce_lock
        self._outbound = QQOutboundRuntime(gateway=self)
        self._recent_bot_message_ids = self._outbound._recent_bot_message_ids
        self._recent_bot_message_lock = self._outbound._recent_bot_message_lock
        self._messages = QQMessageDelivery(gateway=self)
        self._reply_reference_ledger = self._messages._reply_reference_ledger
        self._media = QQMediaDelivery(gateway=self)
        self._emotions = QQEmotionDelivery(gateway=self)
        self.emotion_mface_state = self._emotions.emotion_mface_state
        self._emotion_mface_lock = self._emotions._emotion_mface_lock
        self.emotion_image_state = self._emotions.emotion_image_state
        self._emotion_image_lock = self._emotions._emotion_image_lock
        self._load_persisted_state()
        self._characters = QQCharacterCommands(gateway=self, preferences=self._preferences)
        self._models = QQModelCommands(gateway=self, preferences=self._preferences)
        self._administration = QQAdministrationCommands(gateway=self)

    @property
    def _bot_nickname(self) -> str:
        return self._identity._bot_nickname

    @_bot_nickname.setter
    def _bot_nickname(self, value: str) -> None:
        self._identity._bot_nickname = value

    @property
    def _bot_nickname_status(self) -> str:
        return self._identity._bot_nickname_status

    @_bot_nickname_status.setter
    def _bot_nickname_status(self, value: str) -> None:
        self._identity._bot_nickname_status = value

    @property
    def _bot_nickname_refresh_at(self) -> float:
        return self._identity._bot_nickname_refresh_at

    @_bot_nickname_refresh_at.setter
    def _bot_nickname_refresh_at(self, value: float) -> None:
        self._identity._bot_nickname_refresh_at = value

    @property
    def _miniapp_cards(self) -> MiniappCardStore:
        return self._outbound._miniapp_cards

    @_miniapp_cards.setter
    def _miniapp_cards(self, value: MiniappCardStore) -> None:
        self._outbound._miniapp_cards = value

    @property
    def _plugin_hook_broker(self) -> Any:
        return self._outbound._plugin_hook_broker

    @_plugin_hook_broker.setter
    def _plugin_hook_broker(self, value: Any) -> None:
        self._outbound._plugin_hook_broker = value

    @property
    def _state_error(self) -> str:
        return self._preferences._state_error

    def bind_plugin_hook_broker(self, broker: Any | None) -> None:
        return self._outbound.bind_plugin_hook_broker(broker)

    def _load_persisted_state(self) -> None:
        return self._preferences._load_persisted_state()

    def _persist_gateway_state(self) -> bool:
        return self._preferences._persist_gateway_state()

    def _persist_character_pack_overrides(self) -> bool:
        return self._preferences._persist_gateway_state()

    def _persist_outfit_overrides(self) -> bool:
        return self._preferences._persist_gateway_state()

    def _persist_chat_model_overrides(self) -> bool:
        return self._preferences._persist_gateway_state()

    def _persist_group_vision_overrides(self) -> bool:
        return self._preferences._persist_gateway_state()

    def _persist_group_emotion_overrides(self) -> bool:
        return self._preferences._persist_gateway_state()

    def _persist_group_attention_mode_overrides(self) -> bool:
        return self._preferences._persist_gateway_state()

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.bridge_enabled,
            "onebot_http_url": self.onebot_http_url,
            "master_qq": str(getattr(config, "MASTER_QQ", "") or ""),
            "bot_qq": self.bot_qq,
            "group_plaintext_enabled": bool(getattr(config, "QQ_GROUP_PLAINTEXT_ENABLED", False)),
            "event_max_age_seconds": int(getattr(config, "QQ_EVENT_MAX_AGE_SECONDS", 300) or 0),
            "allow_stale_events": bool(getattr(config, "QQ_ALLOW_STALE_EVENTS", False)),
            "character_pack_id": self.character_pack_id,
            "default_character_pack_id": self.default_character_pack_id,
            **self._preferences.status(),
            "reply_mode": self.default_reply_mode,
            "default_reply_mode": self.default_reply_mode,
            "active_emotion_mface_session_count": len(self.emotion_mface_state),
            "active_emotion_image_session_count": len(self.emotion_image_state),
            "active_attachment_debounce_count": len(self.attachment_debounce_state),
            "active_event_fingerprint_count": self._event_admission.active_fingerprint_count,
        }

    def self_check(self) -> dict[str, Any]:
        return self._outbound.self_check()

    _self_check_failure = staticmethod(QQOutboundRuntime._self_check_failure)

    @property
    def onebot_http_url(self) -> str:
        if self._channel_config is not None:
            return self._channel_config.onebot_http_url
        return (
            str(getattr(config, "QQ_ONEBOT_HTTP_URL", "http://127.0.0.1:3001") or "").strip().rstrip("/")
            or "http://127.0.0.1:3001"
        )

    @property
    def bot_qq(self) -> str:
        if self._channel_config is not None:
            return self._channel_config.bot_id
        return str(getattr(config, "QQ_BOT_QQ", "") or "").strip()

    @property
    def bridge_enabled(self) -> bool:
        if self._channel_config is not None:
            return self._channel_config.enabled
        return bool(getattr(config, "QQ_BRIDGE_ENABLED", False))

    @property
    def onebot_headers(self) -> dict[str, str]:
        if self._channel_config is None:
            return {}
        return self._channel_config.onebot_headers()

    @property
    def character_pack_id(self) -> str:
        return self.default_character_pack_id

    @property
    def default_character_pack_id(self) -> str:
        if self._bound_default_character_pack_id:
            return self._bound_default_character_pack_id
        return _safe_character_pack_id(getattr(config, "QQ_CHARACTER_PACK_ID", ""))

    @property
    def default_reply_mode(self) -> str:
        return _safe_reply_mode(getattr(config, "QQ_REPLY_MODE", "text"), default="text")

    @property
    def master_qq(self) -> str:
        value = str(getattr(config, "MASTER_QQ", "") or "").strip()
        return value if value.isdigit() else ""

    def is_group_vision_enabled(self, group_id: Any) -> bool:
        return self._preferences.is_group_vision_enabled(group_id)

    def handle_private_memory_command(
        self,
        context: QQMessageContext,
        *,
        command: dict[str, Any] | None = None,
        available: bool,
        queue_status=None,
    ) -> dict[str, Any] | None:
        return self._administration.handle_private_memory_command(context, command=command, available=available, queue_status=queue_status)

    def set_group_vision_enabled(self, group_id: Any, enabled: bool) -> bool:
        return self._preferences.set_group_vision_enabled(group_id, enabled)

    def parse_group_vision_command(self, message: str) -> dict[str, str] | None:
        return self._parse_qq_command(message, "识图")

    def handle_group_vision_command(
        self,
        context: QQMessageContext,
        *,
        command: dict[str, Any] | None = None,
        sender_role: str = "",
    ) -> dict[str, Any] | None:
        return self._administration.handle_group_vision_command(context, command=command, sender_role=sender_role)

    def is_group_emotion_enabled(self, group_id: Any) -> bool:
        return self._preferences.is_group_emotion_enabled(group_id)

    def set_group_emotion_enabled(self, group_id: Any, enabled: bool) -> bool:
        return self._preferences.set_group_emotion_enabled(group_id, enabled)

    def parse_group_emotion_command(self, message: str) -> dict[str, str] | None:
        return self._parse_qq_command(message, "表情")

    def handle_group_emotion_command(
        self,
        context: QQMessageContext,
        *,
        command: dict[str, Any] | None = None,
        sender_role: str = "",
    ) -> dict[str, Any] | None:
        return self._administration.handle_group_emotion_command(context, command=command, sender_role=sender_role)

    def resolve_group_attention_mode(self, group_id: Any, *, default: str = "engaged") -> str:
        return self._preferences.resolve_group_attention_mode(group_id, default=default)

    def set_group_attention_mode(self, group_id: Any, mode: str, *, default: str = "engaged") -> bool:
        return self._preferences.set_group_attention_mode(group_id, mode, default=default)

    def parse_group_attention_command(self, message: str) -> dict[str, str] | None:
        return self._parse_qq_command(message, "搭话")

    def handle_group_attention_command(
        self,
        context: QQMessageContext,
        *,
        command: dict[str, Any] | None = None,
        sender_role: str = "",
        default_mode: str = "engaged",
    ) -> dict[str, Any] | None:
        return self._administration.handle_group_attention_command(context, command=command, sender_role=sender_role, default_mode=default_mode)

    def build_message_context(self, event: dict[str, Any]) -> QQMessageContext:
        return self._inbound.build_message_context(event)

    def build_notice_context(self, event: dict[str, Any]) -> QQMessageContext:
        return self._inbound.build_notice_context(event)

    def restore_admitted_passive_context(
        self,
        event: dict[str, Any],
        delivery_context: dict[str, Any],
    ) -> QQMessageContext:
        return self._inbound.restore_admitted_passive_context(event, delivery_context)

    def context_from_delivery_context(self, value: dict[str, Any]) -> QQMessageContext | None:
        return self._inbound.context_from_delivery_context(value)

    def resolve_character_pack_id(self, session_id: str) -> str:
        return self._preferences.resolve_character_pack_id(session_id)

    def resolve_session_outfit_id(self, session_id: str) -> str:
        return self._preferences.resolve_session_outfit_id(session_id)

    def set_session_outfit_id(self, session_id: str, outfit_id: str) -> bool:
        return self._preferences.set_session_outfit_id(session_id, outfit_id)

    def clear_session_outfit_override(self, session_id: str) -> bool:
        return self._preferences.clear_session_outfit_override(session_id)

    def handle_character_command(
        self,
        context: QQMessageContext,
        *,
        command: dict[str, Any] | None = None,
        character_resource_service: Any = None,
    ) -> dict[str, Any] | None:
        return self._characters.handle_character_command(context, command=command, character_resource_service=character_resource_service)

    def handle_outfit_command(
        self,
        context: QQMessageContext,
        *,
        command: dict[str, Any] | None = None,
        resource_manifest_builder: Any = None,
    ) -> dict[str, Any] | None:
        return self._characters.handle_outfit_command(context, command=command, resource_manifest_builder=resource_manifest_builder)

    def parse_character_command(self, message: str) -> dict[str, str] | None:
        return self._parse_qq_command(message, "角色")

    def parse_outfit_command(self, message: str) -> dict[str, str] | None:
        return self._parse_qq_command(message, "服装")

    def parse_chat_model_command(self, message: str) -> dict[str, str] | None:
        return self._parse_qq_command(message, "模型")

    def parse_thinking_mode_command(self, message: str) -> dict[str, str] | None:
        return self._parse_qq_command(message, "思考")

    def parse_access_permission_command(self, message: str) -> dict[str, str] | None:
        return self._parse_qq_command(message, "权限")

    def handle_access_permission_command(
        self,
        context: QQMessageContext,
        *,
        command: dict[str, str] | None = None,
        current_modes: Mapping[str, str] | None = None,
        apply_modes: Callable[[dict[str, str]], Mapping[str, str]] | None = None,
    ) -> dict[str, Any] | None:
        return self._administration.handle_access_permission_command(context, command=command, current_modes=current_modes, apply_modes=apply_modes)

    def parse_capability_approval_command(self, message: str) -> dict[str, str] | None:
        return self._parse_qq_command(message, "审批", "批准", "拒绝")

    def handle_capability_approval_command(
        self,
        context: QQMessageContext,
        *,
        command: dict[str, str] | None = None,
        approval_store: Any = None,
    ) -> dict[str, Any] | None:
        return self._administration.handle_capability_approval_command(context, command=command, approval_store=approval_store)

    def handle_thinking_mode_command(
        self,
        context: QQMessageContext,
        *,
        command: dict[str, str] | None = None,
        current_mode: str = "disabled",
        supported: bool = True,
        apply_mode: Callable[[str], str] | None = None,
    ) -> dict[str, Any] | None:
        return self._models.handle_thinking_mode_command(context, command=command, current_mode=current_mode, supported=supported, apply_mode=apply_mode)

    def set_session_character_pack_id(self, session_id: str, character_pack_id: str) -> bool:
        return self._preferences.set_session_character_pack_id(session_id, character_pack_id)

    def clear_session_character_override(self, session_id: str) -> bool:
        return self._preferences.clear_session_character_override(session_id)

    def resolve_chat_model_override(self, session_id: str) -> str:
        return self._preferences.resolve_chat_model_override(session_id)

    def resolve_chat_model(self, session_id: str, *, default_model: str = "") -> str:
        return self._preferences.resolve_chat_model(session_id, default_model=default_model)

    def set_session_chat_model_override(self, session_id: str, model: str) -> bool:
        return self._preferences.set_session_chat_model_override(session_id, model)

    def clear_session_chat_model_override(self, session_id: str) -> bool:
        return self._preferences.clear_session_chat_model_override(session_id)

    def clear_all_chat_model_overrides(self) -> bool:
        return self._preferences.clear_all_chat_model_overrides()

    def handle_chat_model_command(
        self,
        context: QQMessageContext,
        *,
        command: dict[str, str] | None = None,
        default_model: str = "",
        available_models: list[str] | None = None,
        list_error: str = "",
    ) -> dict[str, Any] | None:
        return self._models.handle_chat_model_command(context, command=command, default_model=default_model, available_models=available_models, list_error=list_error)

    def resolve_reply_mode(self, session_id: str) -> str:
        return self._preferences.resolve_reply_mode(session_id)

    def handle_reply_mode_command(
        self,
        context: QQMessageContext,
        *,
        command: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        return self._models.handle_reply_mode_command(context, command=command)

    def parse_reply_mode_command(self, message: str) -> dict[str, str] | None:
        return self._parse_qq_command(message, "回复")

    def set_session_reply_mode(self, session_id: str, reply_mode: str) -> bool:
        return self._preferences.set_session_reply_mode(session_id, reply_mode)

    def clear_session_reply_mode_override(self, session_id: str) -> bool:
        return self._preferences.clear_session_reply_mode_override(session_id)

    def handle_mface_config_command(
        self,
        context: QQMessageContext,
        event: dict[str, Any],
        *,
        command: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        return self._emotions.handle_mface_config_command(context, event, command=command)

    def parse_mface_config_command(self, message: str) -> str | None:
        command = self._parse_qq_command(message, "表情包")
        return str(command["emotion"]) if command is not None else None

    extract_mface_payloads = staticmethod(QQEmotionDelivery.extract_mface_payloads)

    def _build_current_reply_mode_reply(self, active_mode: str, *, session_id: str = "") -> str:
        return self._models._build_current_reply_mode_reply(active_mode, session_id=session_id)

    def _build_current_chat_model_reply(self, active_model: str, *, has_override: bool, default_model: str = "") -> str:
        return self._models._build_current_chat_model_reply(active_model, has_override=has_override, default_model=default_model)

    def _ordered_chat_model_ids(
        self,
        available_models: list[str] | None,
        *,
        preferred_model: str = "",
    ) -> list[str]:
        return self._models._ordered_chat_model_ids(available_models, preferred_model=preferred_model)

    def _resolve_chat_model_choice(
        self,
        requested_model: str,
        *,
        available_models: list[str] | None,
        preferred_model: str = "",
    ) -> tuple[str, str, list[str]]:
        return self._models._resolve_chat_model_choice(requested_model, available_models=available_models, preferred_model=preferred_model)

    def _build_numbered_model_reply(
        self,
        labels: list[str],
        *,
        instruction: str,
        overflow_count: int = 0,
    ) -> str:
        return self._models._build_numbered_model_reply(labels, instruction=instruction, overflow_count=overflow_count)

    def _format_reply_mode_label(self, reply_mode: str) -> str:
        return self._models._format_reply_mode_label(reply_mode)

    @staticmethod
    def _parse_qq_command(message: str, *names: str) -> dict[str, Any] | None:
        command = resolve_qq_command(message)
        if command is None or command.error or command.definition.name not in names:
            return None
        return dict(command.payload) if command.payload is not None else None


    def _list_character_packs(self, character_resource_service: Any = None) -> list[dict[str, str]]:
        return self._characters._list_character_packs(character_resource_service)

    def _list_manifest_outfits(
        self,
        character_pack_id: str,
        *,
        resource_manifest_builder: Any = None,
    ) -> list[dict[str, Any]]:
        return self._characters._list_manifest_outfits(character_pack_id, resource_manifest_builder=resource_manifest_builder)

    def _build_outfit_manifest(
        self,
        character_pack_id: str,
        *,
        resource_manifest_builder: Any = None,
    ) -> dict[str, Any]:
        return self._characters._build_outfit_manifest(character_pack_id, resource_manifest_builder=resource_manifest_builder)

    def _manifest_default_outfit(
        self,
        character_pack_id: str,
        *,
        resource_manifest_builder: Any = None,
        outfits: list[dict[str, Any]] | None = None,
    ) -> str:
        return self._characters._manifest_default_outfit(character_pack_id, resource_manifest_builder=resource_manifest_builder, outfits=outfits)

    def _find_outfit(self, outfits: list[dict[str, Any]], requested: str) -> dict[str, Any] | None:
        return self._characters._find_outfit(outfits, requested)

    def _build_multiline_option_reply(
        self,
        title: str,
        labels: list[str],
        *,
        instruction: str,
        overflow_count: int = 0,
    ) -> str:
        return self._characters._build_multiline_option_reply(title, labels, instruction=instruction, overflow_count=overflow_count)

    def _build_multiline_available_hint(self, labels: list[str], *, empty_message: str) -> str:
        return self._characters._build_multiline_available_hint(labels, empty_message=empty_message)

    def _format_outfit_label(self, item: dict[str, Any]) -> str:
        return self._characters._format_outfit_label(item)

    def _resolve_pack_identity(
        self,
        pack_id: str,
        *,
        character_resource_service: Any = None,
    ) -> dict[str, str]:
        return self._characters._resolve_pack_identity(pack_id, character_resource_service=character_resource_service)

    def _format_pack_label(self, item: dict[str, Any]) -> str:
        return self._characters._format_pack_label(item)

    def _format_identity_label(self, identity: dict[str, Any]) -> str:
        return self._characters._format_identity_label(identity)

    def _append_state_persistence_warning(self, reply: str, state_persisted: bool) -> str:
        return self._characters._append_state_persistence_warning(reply, state_persisted)

    def _build_current_character_reply(
        self,
        active_pack_id: str,
        *,
        character_resource_service: Any = None,
        session_id: str = "",
    ) -> str:
        return self._characters._build_current_character_reply(active_pack_id, character_resource_service=character_resource_service, session_id=session_id)

    def _build_default_character_reply(
        self,
        active_pack_id: str,
        *,
        character_resource_service: Any = None,
    ) -> str:
        return self._characters._build_default_character_reply(active_pack_id, character_resource_service=character_resource_service)

    def _build_current_outfit_reply(
        self,
        active_outfit_id: str,
        *,
        default_outfit: str,
        outfits: list[dict[str, Any]],
        session_id: str = "",
    ) -> str:
        return self._characters._build_current_outfit_reply(active_outfit_id, default_outfit=default_outfit, outfits=outfits, session_id=session_id)

    def _build_outfit_event_turn_message(self, context: QQMessageContext, action_text: str) -> str:
        return self._characters._build_outfit_event_turn_message(context, action_text)

    def resolve_quoted_message_evidence(
        self,
        event: dict[str, Any],
        *,
        context: QQMessageContext,
    ) -> dict[str, Any]:
        return self._evidence.resolve_quoted_message_evidence(event, context=context)

    def resolve_quoted_attachments(
        self,
        event: dict[str, Any],
        *,
        context: QQMessageContext,
    ) -> dict[str, Any]:
        return self._evidence.resolve_quoted_attachments(event, context=context)

    def resolve_forward_message_evidence(
        self,
        event: dict[str, Any],
        *,
        context: QQMessageContext,
    ) -> dict[str, Any]:
        return self._evidence.resolve_forward_message_evidence(event, context=context)

    _inline_forward_nodes = staticmethod(QQEvidenceResolver._inline_forward_nodes)

    def _call_onebot_action(
        self,
        action: str,
        params: dict[str, object],
        *,
        timeout_seconds: float,
    ) -> object:
        return self._outbound._call_onebot_action(action, params, timeout_seconds=timeout_seconds)

    def call_model_onebot_action(
        self,
        context: QQMessageContext,
        *,
        action: str,
        params: dict[str, Any],
        message_selector: dict[str, Any] | None = None,
        timeout_seconds: float = 20.0,
    ) -> dict[str, Any]:
        return self._outbound.call_model_onebot_action(context, action=action, params=params, message_selector=message_selector, timeout_seconds=timeout_seconds)

    _miniapp_card_scope = staticmethod(QQOutboundRuntime._miniapp_card_scope)

    _onebot_conversation_key = staticmethod(QQOutboundRuntime._onebot_conversation_key)

    def _recent_bot_message_id(self, context: QQMessageContext, *, position: int) -> str:
        return self._outbound._recent_bot_message_id(context, position=position)

    def _remember_successful_outbound(self, action: str, params: dict[str, Any], payload: dict[str, Any]) -> None:
        return self._outbound._remember_successful_outbound(action, params, payload)

    def _call_and_track_outbound(
        self,
        action: str,
        params: dict[str, Any],
        *,
        timeout: float,
    ) -> dict[str, Any]:
        return self._outbound._call_and_track_outbound(action, params, timeout=timeout)

    _is_user_visible_outbound_action = staticmethod(QQOutboundRuntime._is_user_visible_outbound_action)

    def _plugin_hook_observes(self, hook_type: str) -> bool:
        return self._outbound._plugin_hook_observes(hook_type)

    def _dispatch_plugin_hook(self, hook: PluginHookEnvelope) -> Any:
        return self._outbound._dispatch_plugin_hook(hook)

    _plugin_outbound_plan_snapshot = staticmethod(QQOutboundRuntime._plugin_outbound_plan_snapshot)

    _plugin_outbound_subject = staticmethod(QQOutboundRuntime._plugin_outbound_subject)

    _apply_plugin_outbound_decorations = staticmethod(QQOutboundRuntime._apply_plugin_outbound_decorations)

    _legacy_attachments = staticmethod(QQEvidenceResolver._legacy_attachments)

    def message_mentions_bot(self, event: dict[str, Any], raw_message: str) -> bool:
        return self._identity.message_mentions_bot(event, raw_message)

    def resolve_identity(self, *, user_id: int, group_id: int = 0) -> tuple[str, str]:
        return self._identity.resolve_identity(user_id=user_id, group_id=group_id)

    def resolve_actor_profile_user_id(self, *, user_id: int) -> str:
        return self._identity.resolve_actor_profile_user_id(user_id=user_id)

    def resolve_sender_label(self, *, event: dict[str, Any], user_id: int) -> str:
        return self._identity.resolve_sender_label(event=event, user_id=user_id)

    def resolve_mention_labels(
        self,
        *,
        mentions: tuple[MentionRef, ...],
        group_id: int,
    ) -> tuple[MentionRef, ...]:
        return self._identity.resolve_mention_labels(mentions=mentions, group_id=group_id)

    def _resolve_bot_group_label(self, group_id: int) -> str:
        return self._identity._resolve_bot_group_label(group_id)

    def _resolve_bot_nickname(self, *, login_result: Any = None) -> str:
        return self._identity._resolve_bot_nickname(login_result=login_result)


    def build_group_identity_context(self, group_id: int) -> str:
        return self._identity.build_group_identity_context(group_id)

    _project_mention_evidence = staticmethod(QQIdentityRuntime._project_mention_evidence)

    def _sender_label_cache_key(self, *, group_id: int, user_id: int) -> str:
        return self._identity._sender_label_cache_key(group_id=group_id, user_id=user_id)

    def lookup_group_member_label(self, *, group_id: int, user_id: int) -> str:
        return self._identity.lookup_group_member_label(group_id=group_id, user_id=user_id)

    def add_delivery_note(self, session_id: str, note: str) -> None:
        return self._identity.add_delivery_note(session_id, note)

    def consume_delivery_notes(self, session_id: str) -> list[str]:
        return self._identity.consume_delivery_notes(session_id)

    def build_extra_context(
        self,
        *,
        event: dict[str, Any],
        is_group: bool,
        user_id: int,
        group_id: int,
        sender_label: str = "",
        reply_mode: str = "",
        chat_model_override: str = "",
        session_id: str = "",
    ) -> str:
        return self._identity.build_extra_context(event=event, is_group=is_group, user_id=user_id, group_id=group_id, sender_label=sender_label, reply_mode=reply_mode, chat_model_override=chat_model_override, session_id=session_id)

    def render_reply_text(self, frame: dict[str, Any]) -> str:
        return self._messages.render_reply_text(frame)

    _trim_segment_ending = staticmethod(QQMessageDelivery._trim_segment_ending)

    def render_reply_messages(self, frame: dict[str, Any]) -> list[str]:
        return self._messages.render_reply_messages(frame)

    def send_replies(self, context: QQMessageContext, messages: list[str]) -> dict[str, Any]:
        return self._messages.send_replies(context, messages)

    def send_generated_files(
        self, context: QQMessageContext, tool_events: list[dict[str, Any]] | None,
        *, target_allowed: Callable | None = None,
    ) -> dict[str, Any]:
        return self._messages.send_generated_files(context, tool_events, target_allowed=target_allowed)

    _qq_file_delivery_target_identity = staticmethod(QQMessageDelivery._qq_file_delivery_target_identity)

    def _send_generated_file_targets(
        self,
        context: QQMessageContext,
        targets: list[dict[str, Any]],
        *, target_allowed: Callable | None = None,
    ) -> dict[str, Any]:
        return self._messages._send_generated_file_targets(context, targets, target_allowed=target_allowed)

    def _event_allows_qq_file_delivery(self, event: dict[str, Any]) -> bool:
        return self._messages._event_allows_qq_file_delivery(event)

    def send_stickers(self, context: QQMessageContext, tool_events: list[dict[str, Any]] | None) -> dict[str, Any]:
        return self._messages.send_stickers(context, tool_events)

    def send_music_card(
        self,
        context: QQMessageContext,
        *,
        platform: str,
        track_id: str,
    ) -> dict[str, Any]:
        return self._messages.send_music_card(context, platform=platform, track_id=track_id)

    def parse_economy_command(self, message: str) -> dict[str, Any] | None:
        command = self._parse_qq_command(
            message, "签到", "养成", "商店", "购买", "背包", "投喂",
            "供奉", "抽签", "赛钱", "赛钱箱", "额度",
        )
        if command is not None and "item_text" in command:
            item_name, quantity = _parse_item_and_quantity(command.pop("item_text"))
            command.update(item_name=item_name, quantity=quantity)
        return command

    def handle_poke_event(
        self,
        context: "QQMessageContext",
        event: dict[str, Any],
        *,
        care_module: CareModulePort | None = None,
        shop_items: list[dict[str, Any]] | None = None,
        now_ms: int | None = None,
    ) -> PokeOutcome | None:
        return self._care.handle_poke_event(context, event, care_module=care_module, shop_items=shop_items, now_ms=now_ms)

    poke_event_id = staticmethod(QQCareCommands.poke_event_id)

    def handle_economy_command(
        self,
        context: "QQMessageContext",
        *,
        command: dict[str, Any] | None = None,
        care_module: CareModulePort | None = None,
        shop_items: list[dict[str, Any]] | None = None,
        checkin_coins: int = DEFAULT_CHECKIN_COINS,
        now_ms: int | None = None,
    ) -> dict[str, Any] | None:
        return self._care.handle_economy_command(context, command=command, care_module=care_module, shop_items=shop_items, checkin_coins=checkin_coins, now_ms=now_ms)

    def send_reply(
        self,
        context: QQMessageContext,
        message: str,
        *,
        include_reply: bool = True,
    ) -> dict[str, Any]:
        return self._messages.send_reply(context, message, include_reply=include_reply)

    def send_mface(self, context: QQMessageContext, *, mface: dict[str, Any]) -> dict[str, Any]:
        return self._messages.send_mface(context, mface=mface)

    def send_emotion_mface(
        self,
        context: QQMessageContext,
        frame: dict[str, Any],
        *,
        qq_delivery_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._emotions.send_emotion_mface(context, frame, qq_delivery_config=qq_delivery_config)

    def send_emotion_image(
        self,
        context: QQMessageContext,
        frame: dict[str, Any],
        *,
        image: dict[str, Any] | None = None,
        min_interval_seconds: int = 20,
    ) -> dict[str, Any]:
        return self._emotions.send_emotion_image(context, frame, image=image, min_interval_seconds=min_interval_seconds)

    _frame_has_artifact_delivery_activity = staticmethod(QQEmotionDelivery._frame_has_artifact_delivery_activity)

    def send_image(self, context: QQMessageContext, *, image_path: str, name: str = "") -> dict[str, Any]:
        return self._media.send_image(context, image_path=image_path, name=name)

    def send_voice(
        self,
        context: QQMessageContext,
        *,
        audio_path: str,
        name: str = "",
    ) -> dict[str, Any]:
        return self._media.send_voice(context, audio_path=audio_path, name=name)

    def send_voice_url(self, context: QQMessageContext, *, audio_url: str, name: str = "") -> dict[str, Any]:
        return self._media.send_voice_url(context, audio_url=audio_url, name=name)

    def send_file(self, context: QQMessageContext, *, file_path: str, name: str = "") -> dict[str, Any]:
        return self._media.send_file(context, file_path=file_path, name=name)

    def _onebot_file_path(self, path: Path) -> str:
        return self._media._onebot_file_path(path)

    _outbound_target = staticmethod(QQMessageDelivery._outbound_target)

    def _reply_reference_for_content(
        self,
        context: QQMessageContext,
        content_types: tuple[str, ...],
        *,
        include_reply: bool = True,
    ) -> str:
        return self._messages._reply_reference_for_content(context, content_types, include_reply=include_reply)

    def _send_outbound_plan(self, plan: OutboundAction, *, timeout: float) -> dict[str, Any]:
        return self._messages._send_outbound_plan(plan, timeout=timeout)

    _outbound_plan_failure = staticmethod(QQMessageDelivery._outbound_plan_failure)

    def register_attachment_debounce(
        self,
        context: QQMessageContext,
        *,
        attachment_ids: list[str],
    ) -> dict[str, Any]:
        return self._inbound.register_attachment_debounce(context, attachment_ids=attachment_ids)

    def consume_attachment_debounce(self, token: dict[str, Any]) -> dict[str, Any]:
        return self._inbound.consume_attachment_debounce(token)

    def _attachment_debounce_key(self, context: QQMessageContext) -> str:
        # Group memory is shared, but burst merging should stay tied to the
        # sender who is currently feeding Akane attachments.
        return self._inbound._attachment_debounce_key(context)

    def _prune_attachment_debounce_locked(self, *, now_ts: float) -> None:
        return self._inbound._prune_attachment_debounce_locked(now_ts=now_ts)

    def _admit_event(self, event: dict[str, Any]) -> QQMessageContext | None:
        return self._inbound._admit_event(event)

    @staticmethod
    def _safe_int(value: Any) -> int:
        try:
            return int(value or 0)
        except Exception:
            return 0
