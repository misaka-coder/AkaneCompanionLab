"""Desktop Care runtime, Desktop Pet endpoints, artifact/gift API, and passive timeline ingest."""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Callable, Generator, Mapping

from ..care_runtime import CareModulePort, normalize_desktop_care_config
from ..owner_private_session import current_private_scope, desktop_care_scope
from ..client_protocol import ClientCapability, ClientMode, ClientProtocolContext
from ..desktop_pet_character_resources import load_character_care_config
from ..store import MemoryStore, normalize_character_pack_id
from ..text_utils import (
    detect_time_of_day_from_text,
    extract_semantic_tags,
    infer_time_of_day,
    timestamp_to_date_label,
)
from .. import desktop_pet_engine, gift_engine
from ..qq_session_work import _passive_record_source_id

logger = logging.getLogger("akane.engine")


class DesktopCareAndPetMixin:
    """Mixin providing desktop care, pet workspace, gift inventory, and passive timeline ingestion."""

    def list_gift_assets(
        self, *, profile_user_id: str, media_kind: str = "all", limit: int = 50
    ) -> list[dict[str, Any]]:
        return gift_engine.list_gift_assets(
            self,
            profile_user_id=profile_user_id,
            media_kind=media_kind,
            limit=limit,
        )

    def upload_gift_asset(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        filename: str,
        content_type: str,
        content: bytes,
        now_ts: int | None = None,
    ) -> dict[str, Any]:
        return gift_engine.upload_gift_asset(
            self,
            profile_user_id=profile_user_id,
            session_id=session_id,
            filename=filename,
            content_type=content_type,
            content=content,
            now_ts=now_ts,
        )

    def apply_gift_action(
        self,
        *,
        profile_user_id: str,
        session_id: str | None = None,
        asset_id: str,
        action: str,
        timestamp: int | None = None,
    ) -> dict[str, Any] | None:
        return gift_engine.apply_gift_action(
            self,
            profile_user_id=profile_user_id,
            session_id=session_id,
            asset_id=asset_id,
            action=action,
            timestamp=timestamp,
        )

    def observe_gift_image_once(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        asset_id: str,
        timestamp: int | None = None,
    ) -> dict[str, Any] | None:
        return gift_engine.observe_gift_image_once(
            self,
            profile_user_id=profile_user_id,
            session_id=session_id,
            asset_id=asset_id,
            timestamp=timestamp,
        )

    def list_gift_inventory(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        scope: str = "pending_recent",
        limit: int = 5,
    ) -> dict[str, Any]:
        return gift_engine.list_gift_inventory(
            self,
            profile_user_id=profile_user_id,
            session_id=session_id,
            scope=scope,
            limit=limit,
        )

    def list_artifact_containers(
        self,
        *,
        profile_user_id: str,
        preview_limit: int = 3,
        include_empty: bool = True,
    ) -> list[dict[str, Any]]:
        return gift_engine.list_artifact_containers(
            self,
            profile_user_id=profile_user_id,
            preview_limit=preview_limit,
            include_empty=include_empty,
        )

    def list_artifacts_in_container(
        self,
        *,
        profile_user_id: str,
        container_type: str,
        container_key: str = "",
        limit: int = 50,
    ) -> dict[str, Any]:
        return gift_engine.list_artifacts_in_container(
            self,
            profile_user_id=profile_user_id,
            container_type=container_type,
            container_key=container_key,
            limit=limit,
        )


    def _coerce_bool(self, value: Any) -> bool | None:
        from .turn_context import coerce_bool as _fn

        return _fn(value)

    def get_care_module(self) -> CareModulePort:
        module = getattr(self, "care_module", None)
        return module if isinstance(module, CareModulePort) else CareModulePort.disabled("not_configured")

    def care_feature_status(self) -> dict[str, Any]:
        return self.get_care_module().status_payload()

    def _load_desktop_care_config(self, character_pack_id: str) -> dict[str, Any]:
        return load_character_care_config(
            getattr(self, "desktop_pet_character_resources", None),
            character_pack_id,
        )

    def care_enabled_for_context(
        self,
        *,
        character_pack_id: str,
        client_context: ClientProtocolContext | None,
    ) -> bool:
        from ..dialogue_targets import is_service_domain
        if is_service_domain(character_pack_id):
            return False
        if not self.get_care_module().enabled:
            return False
        if client_context is not None and client_context.effective_mode == ClientMode.DESKTOP_PET:
            return bool(normalize_desktop_care_config(self._load_desktop_care_config(character_pack_id))["enabled"])
        return True

    @desktop_care_scope
    def build_desktop_care_snapshot(
        self,
        *,
        profile_user_id: str,
        character_pack_id: str,
        session_id: str = "",
        legacy_state: Any = None,
        now_ms: int | None = None,
    ) -> dict[str, Any]:
        module = self.get_care_module()
        runtime = module.runtime
        if runtime is None:
            return module.disabled_result()
        raw_config = self._load_desktop_care_config(character_pack_id)
        config = normalize_desktop_care_config(raw_config)
        if not config["enabled"]:
            return {
                "ok": False,
                "status": "disabled",
                "reason": "character_care_disabled",
            }
        return runtime.snapshot_for_desktop(
            profile_user_id=profile_user_id,
            character_pack_id=character_pack_id,
            care_config=raw_config,
            conversation_id=self._care_conversation_id(profile_user_id, session_id, desktop=True),
            **self._desktop_shared_account(character_pack_id),
            legacy_state=legacy_state,
            now_ms=now_ms,
        )

    @desktop_care_scope
    def manage_desktop_care_action(
        self,
        *,
        profile_user_id: str,
        character_pack_id: str,
        action: str,
        item_id: str = "",
        session_id: str = "",
        request_id: str = "",
        now_ms: int | None = None,
    ) -> dict[str, Any]:
        module = self.get_care_module()
        runtime = module.runtime
        if runtime is None:
            return module.disabled_result()
        raw_config = self._load_desktop_care_config(character_pack_id)
        config = normalize_desktop_care_config(raw_config)
        if not config["enabled"]:
            return {
                "ok": False,
                "status": "disabled",
                "reason": "character_care_disabled",
            }
        return runtime.perform_desktop_action(
            profile_user_id=profile_user_id,
            character_pack_id=character_pack_id,
            care_config=raw_config,
            action=action,
            item_id=item_id,
            request_id=request_id,
            conversation_id=self._care_conversation_id(profile_user_id, session_id, desktop=True),
            **self._desktop_shared_account(character_pack_id),
            now_ms=now_ms,
        )

    def _desktop_shared_account(self, character_pack_id: str) -> dict[str, Any]:
        scope = current_private_scope(self)
        if scope and scope.shared and scope.character == character_pack_id:
            return {"shared_account": True, "relation_user_id": f"qq:{scope.policy.master_qq}"}
        return {}

    def _care_conversation_id(self, profile_user_id: str, session_id: str, *, desktop: bool) -> str:
        scope = current_private_scope(self)
        if scope and scope.shared and (scope.profile, scope.session) == (profile_user_id, session_id):
            return "master"
        if desktop:
            return "desktop:" + json.dumps([profile_user_id, session_id or profile_user_id], ensure_ascii=False, separators=(",", ":"))
        return session_id or profile_user_id

    def _prepare_care_context_for_turn(
        self,
        payload: dict[str, Any],
        client_context: ClientProtocolContext,
        *,
        profile_user_id: str,
        character_pack_id: str = "",
        now_ts: int,
    ) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return payload
        from ..dialogue_targets import is_service_domain
        if is_service_domain(character_pack_id):
            sanitized_payload = dict(payload)
            sanitized_payload.pop("desktop_care", None)
            sanitized_payload.pop("care_state", None)
            return sanitized_payload
        care_module = self.get_care_module()
        care_runtime = care_module.runtime
        if care_runtime is None:
            sanitized_payload = dict(payload)
            sanitized_payload.pop("desktop_care", None)
            sanitized_payload.pop("care_state", None)
            return sanitized_payload
        client_mode = client_context.effective_mode.value if client_context is not None else ""
        relation_user_id = self._resolve_care_relation_user_id(
            payload,
            client_context,
            profile_user_id=profile_user_id,
        )
        now_ms = int(max(1, now_ts) * 1000)
        desktop_care = payload.get("desktop_care")
        try:
            is_desktop = client_context is not None and client_context.effective_mode == ClientMode.DESKTOP_PET
            if is_desktop:
                result = self.build_desktop_care_snapshot(
                    profile_user_id=profile_user_id,
                    session_id=str(payload.get("user_id") or payload.get("session_id") or profile_user_id),
                    character_pack_id=character_pack_id,
                    legacy_state=desktop_care,
                    now_ms=now_ms,
                )
                snapshot = result.get("snapshot") if isinstance(result, dict) else None
                if isinstance(snapshot, dict):
                    enriched_payload = dict(payload)
                    enriched_payload["desktop_care"] = snapshot
                    return enriched_payload
                sanitized_payload = dict(payload)
                sanitized_payload.pop("desktop_care", None)
                sanitized_payload.pop("care_state", None)
                return sanitized_payload
            if isinstance(desktop_care, dict):
                care_runtime.sync_from_client(
                    profile_user_id=profile_user_id,
                    conversation_id=str(payload.get("user_id") or payload.get("session_id") or profile_user_id),
                    character_pack_id=character_pack_id,
                    client_mode=client_mode,
                    care_payload=desktop_care,
                    relation_user_id=relation_user_id,
                    now_ms=now_ms,
                )
            if client_context is not None and client_context.effective_mode == ClientMode.QQ_TEXT:
                enriched_payload = dict(payload)
                enriched_payload["desktop_care"] = care_runtime.snapshot_for_client(
                    profile_user_id=profile_user_id,
                    conversation_id=str(payload.get("user_id") or payload.get("session_id") or profile_user_id),
                    character_pack_id=character_pack_id,
                    client_mode=ClientMode.QQ_TEXT.value,
                    relation_user_id=relation_user_id,
                    now_ms=now_ms,
                )
                return enriched_payload
        except Exception as exc:
            logger.warning("care runtime context failed: %s", exc)
        return payload

    def _resolve_care_relation_user_id(
        self,
        payload: dict[str, Any],
        client_context: ClientProtocolContext | None,
        *,
        profile_user_id: str,
    ) -> str:
        scope = current_private_scope(self)
        if scope and scope.shared:
            return f"qq:{scope.policy.master_qq}"
        if client_context is not None and client_context.effective_mode == ClientMode.QQ_TEXT:
            delivery = payload.get("qq_delivery_context") if isinstance(payload, dict) else {}
            if isinstance(delivery, dict):
                qq_user_id = str(delivery.get("user_id") or "").strip()
                if qq_user_id and qq_user_id != "0":
                    return f"qq:{qq_user_id}"
            return f"qq:{profile_user_id}"
        return str(profile_user_id or "master")

    def _apply_care_state_request(
        self,
        final_output: dict[str, Any],
        client_context: ClientProtocolContext,
        *,
        profile_user_id: str,
        character_pack_id: str = "",
        payload: dict[str, Any] | None = None,
        now_ts: int,
        event_id: str = "",
    ) -> None:
        if not isinstance(final_output, dict):
            return
        if any(final_output.get(key) for key in ("_transient_final_failure", "_deliberate_silence", "_notification_suppressed")):
            return
        from ..dialogue_targets import is_service_domain
        if is_service_domain(character_pack_id):
            return
        care_module = self.get_care_module()
        if not care_module.enabled:
            final_output.pop("state_request", None)
            final_output.pop("care_state", None)
            return
        if client_context is None or client_context.effective_mode not in {
            ClientMode.DESKTOP_PET,
            ClientMode.QQ_TEXT,
        }:
            return
        is_desktop = client_context.effective_mode == ClientMode.DESKTOP_PET
        if is_desktop and not self.care_enabled_for_context(
            character_pack_id=character_pack_id,
            client_context=client_context,
        ):
            final_output.pop("state_request", None)
            final_output.pop("care_state", None)
            return
        care_runtime = care_module.runtime
        if care_runtime is None:
            return
        now_ms = int(max(1, now_ts) * 1000)
        try:
            relation_user_id = self._resolve_care_relation_user_id(
                payload or {},
                client_context,
                profile_user_id=profile_user_id,
            )
        except Exception as exc:
            logger.warning("care runtime relation_user_id resolve failed: %s", exc)
            relation_user_id = ""
        raw_config = self._load_desktop_care_config(character_pack_id) if is_desktop else {}
        desktop_config = normalize_desktop_care_config(raw_config) if is_desktop else {}
        turn_kind = str((payload or {}).get("turn_kind") or "").strip().lower()
        if is_desktop:
            energy_cost = (
                0
                if turn_kind == "desktop_pet_care_feed"
                else int(
                    desktop_config["decay"][
                        "energy_per_proactive" if turn_kind == "desktop_pet_proactive" else "energy_per_reply"
                    ]
                )
            )
            coin_reward = 0
        else:
            energy_cost = 1
            coin_reward = 1
        state_request = final_output.get("state_request")
        try:
            delta = int(state_request.get("affinity", 0)) if isinstance(state_request, dict) else 0
        except (TypeError, ValueError):
            delta = 0
        session_id = str((payload or {}).get("user_id") or (payload or {}).get("session_id") or profile_user_id)
        conversation_id = self._care_conversation_id(profile_user_id, session_id, desktop=is_desktop)
        prior_snapshot = (payload or {}).get("desktop_care")
        consumed = prior_snapshot.get("pending_tier_event") if isinstance(prior_snapshot, dict) else None
        try:
            final_output["care_state"] = care_runtime.settle_turn(
                profile_user_id=profile_user_id, character_pack_id=character_pack_id,
                conversation_id=conversation_id, relation_user_id=relation_user_id,
                client_mode=client_context.effective_mode.value,
                shared_body=bool(current_private_scope(self) and current_private_scope(self).shared),
                energy_cost=energy_cost, coin_reward=coin_reward, affinity_delta=delta,
                event_id=event_id, consumed_tier_event=consumed, now_ms=now_ms,
            )
        except Exception as exc:
            logger.warning("care runtime turn settlement failed: %s", type(exc).__name__)
            final_output["care_state_error"] = "care_settlement_failed"

    def _run_summary_cycle(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
    ) -> None:
        manager = self._memcore_manager_if_enabled()
        if manager is not None:
            manager.compact_due_sync(
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=character_pack_id,
                provider_profile=self._chat_provider_protocol_for_memcore(),
            )

    def ingest_qq_attachments(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        attachments: list[dict[str, Any]],
        character_pack_id: str = "",
        timestamp: int | None = None,
        observe_images: bool = True,
    ) -> list[dict[str, Any]]:
        service = self._get_attachment_ingest_service()
        if service is None:
            return []
        return service.ingest_qq_attachments(
            profile_user_id=profile_user_id,
            session_id=session_id,
            attachments=attachments,
            character_pack_id=character_pack_id,
            timestamp=timestamp,
            observe_images=observe_images,
        )

    def ingest_desktop_pet_audio_attachment(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        source_path: Path | str,
        origin_name: str = "",
        mime_type: str = "",
        character_pack_id: str = "",
        timestamp: int | None = None,
    ) -> dict[str, Any]:
        return desktop_pet_engine.ingest_desktop_pet_audio_attachment(
            self,
            profile_user_id=profile_user_id,
            session_id=session_id,
            source_path=source_path,
            origin_name=origin_name,
            mime_type=mime_type,
            character_pack_id=character_pack_id,
            timestamp=timestamp,
        )

    def import_desktop_pet_local_paths(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        paths: list[Any] | tuple[Any, ...] | set[Any] | str,
        recursive: bool = False,
        max_files: int = 40,
        character_pack_id: str = "",
        timestamp: int | None = None,
    ) -> dict[str, Any]:
        return desktop_pet_engine.import_desktop_pet_local_paths(
            self,
            profile_user_id=profile_user_id,
            session_id=session_id,
            paths=paths,
            recursive=recursive,
            max_files=max_files,
            character_pack_id=character_pack_id,
            timestamp=timestamp,
        )

    def resolve_desktop_pet_audio_attachment(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        target: str,
    ) -> tuple[dict[str, Any], Path] | None:
        return desktop_pet_engine.resolve_desktop_pet_audio_attachment(
            self,
            profile_user_id=profile_user_id,
            session_id=session_id,
            target=target,
        )

    def resolve_desktop_pet_generated_audio(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        target: str,
    ) -> tuple[dict[str, Any], Path] | None:
        return desktop_pet_engine.resolve_desktop_pet_generated_audio(
            self,
            profile_user_id=profile_user_id,
            session_id=session_id,
            target=target,
        )

    def resolve_desktop_pet_attachment_file(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        target: str,
    ) -> tuple[dict[str, Any], Path] | None:
        return desktop_pet_engine.resolve_desktop_pet_attachment_file(
            self,
            profile_user_id=profile_user_id,
            session_id=session_id,
            target=target,
        )

    def resolve_desktop_pet_generated_file(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        target: str,
    ) -> tuple[dict[str, Any], Path] | None:
        return desktop_pet_engine.resolve_desktop_pet_generated_file(
            self,
            profile_user_id=profile_user_id,
            session_id=session_id,
            target=target,
        )

    def build_desktop_pet_workspace_panel(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        limit: int = 24,
    ) -> dict[str, Any]:
        return desktop_pet_engine.build_desktop_pet_workspace_panel(
            self,
            profile_user_id=profile_user_id,
            session_id=session_id,
            limit=limit,
        )

    def manage_desktop_pet_workspace_panel(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
        action: str,
        item_type: str = "",
        target: str = "",
    ) -> dict[str, Any]:
        return desktop_pet_engine.manage_desktop_pet_workspace_panel(
            self,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
            action=action,
            item_type=item_type,
            target=target,
        )

    def prepare_desktop_music_timeline(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        activity: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return desktop_pet_engine.prepare_desktop_music_timeline(
            self,
            profile_user_id=profile_user_id,
            session_id=session_id,
            activity=activity,
        )


    def _is_transient_user_turn(self, payload: dict[str, Any]) -> bool:
        from .turn_context import is_transient_user_turn as _fn

        return _fn(payload)

    @staticmethod
    def _should_persist_assistant_turn(payload: dict[str, Any]) -> bool:
        return not bool(payload.get("transient_assistant_message"))

    @staticmethod
    def _should_persist_completed_assistant(
        persist_requested: bool,
        final_output: dict[str, Any],
    ) -> bool:
        return (
            bool(persist_requested)
            and not bool(final_output.get("_transient_final_failure"))
            and not bool(final_output.get("_deliberate_silence"))
            and not bool(final_output.get("_notification_suppressed"))
        )

    @staticmethod
    def _pop_user_memory_source_id(
        payload: dict[str, Any],
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str,
        memory_role: str = "user",
    ) -> str:
        raw_idempotency_key = payload.pop("memory_idempotency_key", "")
        if not isinstance(raw_idempotency_key, str) or not raw_idempotency_key.strip():
            return ""
        source_material = json.dumps(
            {
                "profile_user_id": str(profile_user_id),
                "session_id": str(session_id),
                "character_pack_id": normalize_character_pack_id(character_pack_id),
                "memory_idempotency_key": raw_idempotency_key.strip(),
                "role": str(memory_role or "user").strip() or "user",
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(source_material.encode("utf-8", errors="ignore")).hexdigest()
        return f"plugin-event:{digest}"

    @staticmethod
    def _pop_plugin_external_event(
        payload: dict[str, Any],
        *,
        event_allowed: bool,
    ) -> dict[str, Any] | None:
        raw = payload.pop("plugin_external_event", None)
        if not event_allowed or not isinstance(raw, dict) or "data" not in raw:
            return None
        event_type = str(raw.get("event_type") or "").strip().lower()
        source = str(raw.get("source") or "").strip()
        if re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,119}", event_type) is None or not source:
            return None
        try:
            data = json.loads(json.dumps(raw["data"], ensure_ascii=False, allow_nan=False))
        except (TypeError, ValueError, OverflowError, RecursionError):
            return None
        return {"event_type": event_type, "source": source, "data": data}

    @staticmethod
    def _external_event_memory_metadata(event: dict[str, Any]) -> dict[str, Any]:
        _ = event
        return {}

    @staticmethod
    def _render_external_event_memory_content(event: dict[str, Any]) -> str:
        """Return the stable stored identity of one structured external event.

        ``message`` on a plugin turn request is a turn instruction and may
        improve when the plugin is upgraded. It must therefore not be the
        immutable content behind ``memory_idempotency_key``. The structured
        event is the durable stimulus; render only those facts here so the same
        event can be retried with better instructions without colliding, while
        changed event facts still fail the store's normal idempotency check.
        """

        return json.dumps(event, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2)

    def _build_transient_user_record(
        self,
        *,
        user_message: str,
        now_ts: int,
        date_label: str,
        time_of_day: str,
    ) -> dict[str, Any]:
        from .turn_context import build_transient_user_record as _fn

        return _fn(user_message=user_message, now_ts=now_ts, date_label=date_label, time_of_day=time_of_day)

    @staticmethod
    def _normalize_message_addressing(
        payload: dict[str, Any],
        *,
        fallback_mode: str,
    ) -> dict[str, Any]:
        raw = payload.get("message_addressing")
        if not isinstance(raw, dict):
            return (
                {
                    "mode": "observed",
                    "trigger": "passive",
                    "addressed_to_assistant": False,
                    "explicit_assistant_mention": False,
                    "primary_target": {},
                    "mentions": [],
                }
                if fallback_mode == "observed"
                else {}
            )
        mode = str(raw.get("mode") or fallback_mode).strip().lower()
        if mode not in {"current_request", "observed"}:
            mode = fallback_mode
        trigger = str(raw.get("trigger") or "").strip()[:80]
        mentions: list[dict[str, Any]] = []
        for item in list(raw.get("mentions") or [])[:16]:
            if not isinstance(item, dict):
                continue
            actor_id = str(item.get("actor_id") or "").strip()[:160]
            if not actor_id:
                continue
            mentions.append(
                {
                    "actor_id": actor_id,
                    "display_name": str(item.get("display_name") or "").strip()[:160],
                    "is_assistant": bool(item.get("is_assistant")),
                }
            )
        addressed_to_assistant = bool(raw.get("addressed_to_assistant"))
        reply_raw = raw.get("reply_reference")
        reply_reference: dict[str, Any] = {}
        if isinstance(reply_raw, dict):
            reply_actor_id = str(reply_raw.get("actor_id") or "").strip()[:160]
            reply_actor_name = str(reply_raw.get("actor_display_name") or "").strip()[:160]
            reply_message_id = str(reply_raw.get("message_id") or "").strip()[:160]
            reply_excerpt = str(reply_raw.get("excerpt") or "").strip()[:1000]
            try:
                reply_timestamp = int(float(reply_raw.get("timestamp") or 0))
            except (TypeError, ValueError):
                reply_timestamp = 0
            reply_conversation_kind = str(reply_raw.get("conversation_kind") or "").strip()[:32]
            reply_conversation_id = str(reply_raw.get("conversation_id") or "").strip()[:160]
            try:
                reply_attachment_count = max(0, int(reply_raw.get("attachment_count") or 0))
            except (TypeError, ValueError):
                reply_attachment_count = 0
            reply_mentions: list[dict[str, Any]] = []
            seen_reply_mentions: set[str] = set()
            for item in list(reply_raw.get("mentions") or [])[:16]:
                if not isinstance(item, dict):
                    continue
                mention_id = str(item.get("actor_id") or "").strip()[:160]
                if not mention_id or mention_id in seen_reply_mentions:
                    continue
                seen_reply_mentions.add(mention_id)
                reply_mentions.append(
                    {
                        "actor_id": mention_id,
                        "display_name": str(item.get("display_name") or "").strip()[:160],
                        "is_assistant": bool(item.get("is_assistant")),
                    }
                )
            if reply_actor_id or reply_message_id or reply_excerpt:
                reply_reference = {
                    "actor_id": reply_actor_id,
                    "actor_display_name": reply_actor_name,
                    "message_id": reply_message_id,
                    "excerpt": reply_excerpt,
                }
                if reply_timestamp > 0:
                    reply_reference["timestamp"] = reply_timestamp
                if reply_conversation_kind:
                    reply_reference["conversation_kind"] = reply_conversation_kind
                if reply_conversation_id:
                    reply_reference["conversation_id"] = reply_conversation_id
                if reply_attachment_count > 0:
                    reply_reference["attachment_count"] = reply_attachment_count
                if reply_mentions:
                    reply_reference["mentions"] = reply_mentions
        primary_raw = raw.get("primary_target")
        primary = dict(primary_raw) if isinstance(primary_raw, dict) else {}
        target_id = str(primary.get("actor_id") or "").strip()[:160]
        target_name = str(primary.get("display_name") or "").strip()[:160]
        if addressed_to_assistant:
            target_id = "assistant"
            target_name = ""
        normalized = {
            "mode": mode,
            "trigger": trigger,
            "addressed_to_assistant": addressed_to_assistant,
            "explicit_assistant_mention": bool(raw.get("explicit_assistant_mention")),
            "primary_target": {
                "actor_id": target_id,
                "display_name": target_name,
            }
            if target_id
            else {},
            "mentions": mentions,
        }
        if reply_reference:
            normalized["reply_reference"] = reply_reference
        return normalized

    @staticmethod
    def _apply_message_addressing(
        record: dict[str, Any],
        addressing: dict[str, Any],
    ) -> tuple[str, str]:
        if not addressing:
            return "", ""
        record["message_addressing"] = dict(addressing)
        if str(addressing.get("mode") or "") == "observed":
            record["kind"] = "message.user.observed"
        target = addressing.get("primary_target")
        if not isinstance(target, dict):
            return "", ""
        target_id = str(target.get("actor_id") or "").strip()
        target_name = str(target.get("display_name") or "").strip()
        if target_id:
            record["target_actor_id"] = target_id
            record["target_actor_display_name"] = target_name
        return target_id, target_name

    @staticmethod
    def _normalize_forward_references(payload: dict[str, Any]) -> list[dict[str, Any]]:
        raw_references = payload.get("forward_references")
        if not isinstance(raw_references, (list, tuple)):
            return []
        references: list[dict[str, Any]] = []
        for raw in raw_references:
            if not isinstance(raw, dict):
                continue
            nodes: list[dict[str, Any]] = []
            for raw_node in list(raw.get("nodes") or []):
                if not isinstance(raw_node, dict):
                    continue
                try:
                    index = max(0, int(raw_node.get("index") or 0))
                except (TypeError, ValueError):
                    index = 0
                try:
                    timestamp = int(float(raw_node.get("timestamp") or 0))
                except (TypeError, ValueError):
                    timestamp = 0
                try:
                    attachment_count = max(0, int(raw_node.get("attachment_count") or 0))
                except (TypeError, ValueError):
                    attachment_count = 0
                node = {
                    "index": index,
                    "actor_id": str(raw_node.get("actor_id") or "").strip(),
                    "actor_display_name": str(
                        raw_node.get("actor_display_name") or raw_node.get("actor_label") or ""
                    ).strip(),
                    "text": str(raw_node.get("text") or ""),
                }
                mentions: list[dict[str, Any]] = []
                seen_mentions: set[str] = set()
                for raw_mention in list(raw_node.get("mentions") or [])[:16]:
                    if not isinstance(raw_mention, dict):
                        continue
                    mention_id = str(raw_mention.get("actor_id") or "").strip()[:160]
                    if not mention_id or mention_id in seen_mentions:
                        continue
                    seen_mentions.add(mention_id)
                    mentions.append(
                        {
                            "actor_id": mention_id,
                            "display_name": str(raw_mention.get("display_name") or "").strip()[:160],
                            "is_assistant": bool(raw_mention.get("is_assistant")),
                        }
                    )
                if mentions:
                    node["mentions"] = mentions
                message_id = str(raw_node.get("message_id") or "").strip()
                if timestamp > 0:
                    node["timestamp"] = timestamp
                if message_id:
                    node["message_id"] = message_id
                if attachment_count > 0:
                    node["attachment_count"] = attachment_count
                nodes.append(node)
            entry: dict[str, Any] = {
                "source_part_id": str(raw.get("source_part_id") or "").strip(),
                "forward_id": str(raw.get("forward_id") or "").strip(),
                "status": str(raw.get("status") or "unavailable").strip().lower(),
                "nodes": nodes,
            }
            if "ok" in raw:
                entry["ok"] = bool(raw.get("ok"))
            reason = str(raw.get("reason") or "").strip()
            if reason:
                entry["reason"] = reason
            entry["node_count"] = len(nodes)
            if entry["forward_id"] or entry["source_part_id"]:
                references.append(entry)
        return references

    def record_passive_qq_message(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._record_passive_qq_message(payload, schedule_maintenance=True)

    def record_plugin_timeline_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Persist a typed plugin event without opening a model-response turn."""

        manager = self._memcore_manager_if_enabled()
        if manager is None:
            return {"ok": False, "status": "unavailable", "reason": "memcore_not_enabled"}
        source_id = str(payload.get("source_id") or "").strip()
        session_id = str(payload.get("user_id") or payload.get("session_id") or "default_session")
        profile_user_id = str(payload.get("real_user_id") or payload.get("profile_user_id") or session_id)
        character_pack_id = self._resolve_payload_character_pack_id(payload)
        event = payload.get("event") if isinstance(payload.get("event"), dict) else None
        if not source_id or event is None:
            return {"ok": False, "status": "invalid_event", "reason": "event_contract_invalid"}
        result = manager.append_standalone_event(
            event,
            source_id=source_id,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
            timestamp=int(payload.get("timestamp") or time.time()),
        )
        if bool(result.get("ok")) and self._memcore_owns_compaction():
            result = {
                **result,
                "compaction": self._schedule_memcore_compaction(
                    profile_user_id=profile_user_id,
                    session_id=session_id,
                    character_pack_id=character_pack_id,
                ),
            }
        return result

    def record_passive_qq_messages(self, payloads: list[dict[str, Any]]) -> dict[str, Any]:
        """Record one ordered passive-message batch and schedule maintenance once."""

        normalized = [dict(item) for item in list(payloads or []) if isinstance(item, dict)]
        if not normalized:
            return {"ok": False, "status": "empty_batch", "results": []}
        identities = {
            (
                str(item.get("real_user_id") or item.get("profile_user_id") or "").strip(),
                str(item.get("user_id") or item.get("session_id") or "").strip(),
                str(self._resolve_payload_character_pack_id(item) or "").strip(),
            )
            for item in normalized
        }
        if len(identities) != 1:
            return {
                "ok": False,
                "status": "mixed_batch_scope",
                "reason": "passive_batch_requires_one_conversation",
                "results": [],
            }
        from ..local_timings import LocalTimings
        stages = LocalTimings()
        results: list[dict[str, Any]] = []
        for item in normalized:
            try:
                result = self._record_passive_qq_message(item, schedule_maintenance=False)
            except Exception as exc:
                result = {
                    "ok": False,
                    "status": "record_failed",
                    "reason": exc.__class__.__name__,
                }
            results.append(dict(result or {}))
        stages.mark("memory_record_ms")
        successful = [item for item in results if bool(item.get("ok"))]
        maintenance: dict[str, Any] = {}
        if successful:
            sample = successful[0]
            if self._memcore_owns_compaction():
                maintenance = self._schedule_memcore_compaction(
                    profile_user_id=str(sample.get("profile_user_id") or ""),
                    session_id=str(sample.get("session_id") or ""),
                    character_pack_id=str(sample.get("character_pack_id") or ""),
                )
        failed_count = len(results) - len(successful)
        stages.mark("maintenance_schedule_ms")
        return {
            "ok": failed_count == 0,
            "status": "recorded" if failed_count == 0 else "partially_recorded",
            "count": len(results),
            "recorded_count": len(successful),
            "failed_count": failed_count,
            "results": results,
            "maintenance": maintenance,
            "local_stages": stages.snapshot(),
        }

    def _record_passive_qq_message(
        self,
        payload: dict[str, Any],
        *,
        schedule_maintenance: bool,
    ) -> dict[str, Any]:
        turn_character_pack_id = self._resolve_payload_character_pack_id(payload)
        actor_stable_id, actor_display_name = self._resolve_turn_actor(payload)
        from ..dialogue_targets import is_service_domain, registry_for, trusted_target
        dialogue_target = trusted_target(payload)
        if is_service_domain(turn_character_pack_id) and dialogue_target is None:
            raise ValueError("dialogue_target_requires_host_context")
        if dialogue_target is not None:
            registry_for(self).validate(dialogue_target)
        session_id = str(payload.get("user_id") or payload.get("session_id") or "default_session")
        profile_user_id = str(payload.get("real_user_id") or session_id)
        user_message = str(payload.get("message") or "").strip()
        memory_user_message = str(payload.get("memory_message") or user_message).strip()
        if not user_message:
            return {
                "ok": False,
                "status": "empty_message",
                "session_id": session_id,
                "profile_user_id": profile_user_id,
                "character_pack_id": turn_character_pack_id,
            }
        now_ts = int(payload.get("timestamp") or time.time())
        date_label = timestamp_to_date_label(now_ts)
        time_of_day = detect_time_of_day_from_text(user_message) or infer_time_of_day(now_ts)
        addressing = self._normalize_message_addressing(
            payload,
            fallback_mode="observed",
        )
        forward_references = self._normalize_forward_references(payload)
        memory_metadata = {
            "source": "qq_group_passive",
            "client_mode": str(payload.get("client_mode") or "qq_text"),
            "passive": True,
            "message_addressing": addressing,
        }
        if forward_references:
            memory_metadata["forward_references"] = forward_references
        user_record = self.store.add_message(
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=turn_character_pack_id,
            role="user",
            content=memory_user_message,
            timestamp=now_ts,
            date_label=date_label,
            time_of_day=time_of_day,
            semantic_tags=extract_semantic_tags(memory_user_message),
            memory_metadata=memory_metadata,
            index_in_vector=False,
            # A replayed queued message must reuse the same record instead of
            # appending a second copy of the same group history.
            source_id=_passive_record_source_id(
                payload,
                profile_user_id=profile_user_id,
                character_pack_id=turn_character_pack_id,
            ),
        )
        source_message_id = str(payload.get("source_message_id") or "").strip()
        if source_message_id:
            user_record["source_message_id"] = source_message_id
        target_actor_id, target_actor_display_name = self._apply_message_addressing(
            user_record,
            addressing,
        )
        if forward_references:
            user_record["forward_references"] = forward_references
        memcore_result = self._append_memcore_passive_message(
            user_record=user_record,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=turn_character_pack_id,
            actor_stable_id=actor_stable_id,
            actor_display_name=actor_display_name,
            target_actor_id=target_actor_id,
            target_actor_display_name=target_actor_display_name,
        )
        # MemCore owns the durable conversation. An empty result means MemCore
        # holds no authority for this record, but an explicit failure must reach
        # the caller instead of a success receipt that hides a lost message.
        memcore_failure = ""
        if memcore_result and not bool(memcore_result.get("ok")):
            memcore_failure = str(memcore_result.get("reason") or "") or "memcore_write_rejected"
        compaction_result = (
            self._schedule_memcore_compaction(
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=turn_character_pack_id,
            )
            if schedule_maintenance and not memcore_failure and self._memcore_owns_compaction()
            else {}
        )
        return {
            "ok": not memcore_failure,
            "status": "recorded" if not memcore_failure else "record_failed",
            "reason": memcore_failure,
            "source_id": str(user_record.get("source_id") or ""),
            "session_id": session_id,
            "profile_user_id": profile_user_id,
            "character_pack_id": turn_character_pack_id,
            "memcore": memcore_result,
            "compaction": compaction_result,
        }
