"""Prompt building, persona synthesis, history rendering, and context preparation services."""
from __future__ import annotations

import logging
from typing import Any, Callable, Generator, Mapping

import config

from ..client_protocol import ClientCapability, ClientMode, ClientProtocolContext
from ..mode_profiles import ModeProfileRegistry
from ..output_adapters import OutputAdapterRegistry
from ..persona_config import PERSONA
from ..persona_system import PersonaCardService
from ..prompt_builder import PromptBuilder
from ..prompt_profiles import PromptModule, PromptProfileRegistry
from ..resource_manifest import ResourceManifest
from ..text_utils import (
    detect_time_of_day_from_text,
    extract_semantic_tags,
    normalize_text,
    parse_joined_tags,
    render_chat_line,
)
from ..vector_entry_builder import build_raw_vector_entry
from .. import desktop_context_engine, visual_context_engine

logger = logging.getLogger("akane.engine")


class PromptContextMixin:
    """Mixin providing prompt synthesis, persona configuration, timeline history rendering, and visual context."""

    def _get_prompt_builder(self) -> PromptBuilder:
        prompt_builder = getattr(self, "prompt_builder", None)
        if prompt_builder is None:
            prompt_builder = PromptBuilder(
                PERSONA,
                stable_system_blocks_provider=getattr(
                    self,
                    "stable_system_blocks_provider",
                    None,
                ),
            )
            self.prompt_builder = prompt_builder
        return prompt_builder

    def _get_mode_profile_registry(self) -> ModeProfileRegistry:
        registry = getattr(self, "mode_profile_registry", None)
        if registry is None:
            registry = ModeProfileRegistry()
            self.mode_profile_registry = registry
        return registry

    def _get_prompt_profile_registry(self) -> PromptProfileRegistry:
        registry = getattr(self, "prompt_profile_registry", None)
        if registry is None:
            registry = PromptProfileRegistry()
            self.prompt_profile_registry = registry
        return registry

    def _get_output_adapter_registry(self) -> OutputAdapterRegistry:
        registry = getattr(self, "output_adapters", None)
        if registry is None:
            registry = OutputAdapterRegistry()
            self.output_adapters = registry
        return registry

    def _resolve_client_protocol_context(self, payload: dict[str, Any] | None) -> ClientProtocolContext:
        return self._get_mode_profile_registry().resolve_from_payload(payload)

    def _resolve_resource_manifest_for_client(
        self,
        *,
        client_mode: str = "",
        character_pack_id: str = "",
    ) -> ResourceManifest | None:
        raw_mode = client_mode.value if isinstance(client_mode, ClientMode) else str(client_mode or "").strip()
        character_pack_mode = raw_mode in {
            ClientMode.DESKTOP_PET.value,
            ClientMode.QQ_TEXT.value,
        }
        if not character_pack_mode:
            return self.resource_manifest

        service = getattr(self, "desktop_pet_character_resources", None)
        if service is None:
            return None if raw_mode == ClientMode.QQ_TEXT.value else self.resource_manifest
        manifest = service.get_manifest(character_pack_id) if character_pack_id else None
        if raw_mode == ClientMode.QQ_TEXT.value:
            return manifest
        return manifest or self.resource_manifest

    def _resolve_turn_resource_manifest(
        self,
        payload: dict[str, Any],
        client_context: ClientProtocolContext,
    ) -> ResourceManifest | None:
        from ..dialogue_targets import is_service_domain
        if is_service_domain(self._resolve_payload_character_pack_id(payload)):
            return None
        if client_context.effective_mode not in {
            ClientMode.DESKTOP_PET,
            ClientMode.QQ_TEXT,
        }:
            return self.resource_manifest
        return self._resolve_resource_manifest_for_client(
            client_mode=client_context.effective_mode.value,
            character_pack_id=self._resolve_payload_character_pack_id(payload),
        )

    @staticmethod
    def _resolve_payload_character_pack_id(payload: dict[str, Any]) -> str:
        from .turn_context import resolve_payload_character_pack_id as _fn

        return _fn(payload)

    @staticmethod
    def _resolve_turn_actor(payload: dict[str, Any]) -> tuple[str, str]:
        from .turn_context import resolve_turn_actor as _fn

        return _fn(payload)

    @staticmethod
    def _resolve_turn_domain_profile(payload: dict[str, Any]) -> str:
        from ..domain_profiles import DomainProfileRegistry

        return DomainProfileRegistry().get(payload.get("domain_profile")).id

    def _resolve_turn_speaker_identity(
        self,
        client_context: ClientProtocolContext,
        character_pack_id: str,
    ) -> dict[str, str]:
        """Resolve display speaker identity for the current turn.

        DesktopPet / QQ text modes with a valid character pack  →  character pack identity.
        Other modes  →  persona_profiles.toml defaults (PERSONA).
        """
        from ..dialogue_targets import is_service_domain, registry_for
        if is_service_domain(character_pack_id):
            registry = registry_for(self)
            target = registry.from_domain(character_pack_id)
            registry.validate(target)
            name = registry.service_persona(target).name
            return {"character_id": target.speaker_id, "assistant_name": name,
                    "user_label": "用户", "app_name": name, "pack_id": ""}
        if client_context is not None and client_context.effective_mode in {ClientMode.DESKTOP_PET, ClientMode.QQ_TEXT}:
            service = getattr(self, "desktop_pet_character_resources", None)
            if service is not None and character_pack_id:
                identity_builder = getattr(service, "build_character_identity", None)
                if identity_builder is not None:
                    try:
                        identity = identity_builder(character_pack_id)
                    except Exception:
                        identity = {}
                    if identity:
                        return {
                            "character_id": str(identity.get("character_id") or identity.get("pack_id") or ""),
                            "assistant_name": str(identity.get("assistant_name") or ""),
                            "user_label": str(identity.get("user_label") or ""),
                            "app_name": str(identity.get("app_name") or ""),
                            "pack_id": str(identity.get("pack_id") or ""),
                        }

        return {
            "character_id": "",
            "assistant_name": PERSONA.assistant_name,
            "user_label": PERSONA.user_label,
            "app_name": PERSONA.assistant_name,
            "pack_id": "",
        }

    def _build_desktop_pet_character_pack_prompt_context(
        self,
        *,
        character_pack_id: str,
        resource_manifest: ResourceManifest | None = None,
        client_mode: str = ClientMode.DESKTOP_PET.value,
        preferred_outfit: str = "",
        extra_character_outfits: list[dict[str, Any]] | None = None,
    ) -> dict[str, str]:
        from ..dialogue_targets import is_service_domain, registry_for
        if is_service_domain(character_pack_id):
            return registry_for(self).persona_context(character_pack_id)
        service = getattr(self, "desktop_pet_character_resources", None)
        if service is None or not character_pack_id:
            return {
                "system_context": "",
                "reference_context": "",
                "resource_context": "",
                "active_id": "",
            }
        builder = getattr(service, "build_persona_prompt_context", None)
        if builder is None:
            return {
                "system_context": "",
                "reference_context": "",
                "resource_context": "",
                "active_id": "",
            }
        try:
            context = builder(
                character_pack_id,
                resource_manifest=resource_manifest,
                client_mode=client_mode,
                preferred_outfit=preferred_outfit,
                extra_character_outfits=extra_character_outfits,
            )
        except Exception as exc:
            logger.warning("desktop pet character pack prompt context failed: %s", exc)
            return {
                "system_context": "",
                "reference_context": "",
                "resource_context": "",
                "active_id": "",
            }
        if not isinstance(context, dict):
            return {"system_context": "", "reference_context": "", "resource_context": "", "active_id": ""}
        from ..desktop_pet_character_resources import build_character_care_preference_context
        preference_context = build_character_care_preference_context(service, character_pack_id)
        return {**context, "system_context": "\n\n".join(part for part in (
            str(context.get("system_context") or ""), preference_context,
        ) if part)}

    @staticmethod
    def _merge_prompt_persona_contexts(*contexts: dict[str, Any]) -> dict[str, str]:
        system_parts: list[str] = []
        reference_parts: list[str] = []
        resource_parts: list[str] = []
        active_id = ""
        for context in contexts:
            if not isinstance(context, dict):
                continue
            system_context = str(context.get("system_context") or "").strip()
            reference_context = str(context.get("reference_context") or "").strip()
            resource_context = str(context.get("resource_context") or "").strip()
            current_active_id = str(context.get("active_id") or "").strip()
            if system_context:
                system_parts.append(system_context)
            if reference_context:
                reference_parts.append(reference_context)
            if resource_context:
                resource_parts.append(resource_context)
            if current_active_id:
                active_id = current_active_id
        return {
            "system_context": "\n\n".join(system_parts),
            "reference_context": "\n\n".join(reference_parts),
            "resource_context": "\n\n".join(resource_parts),
            "active_id": active_id,
        }

    def _get_persona_card_service(self) -> PersonaCardService | None:
        service = getattr(self, "persona_card_service", None)
        if service is not None:
            return service
        store = getattr(self, "store", None)
        if store is None:
            return None
        service = PersonaCardService(store=store)
        self.persona_card_service = service
        return service

    def _build_memory_compaction_persona_context(
        self,
        *,
        profile_user_id: str = "",
        session_id: str = "",
        character_pack_id: str = "",
    ) -> dict[str, str]:
        contexts: list[dict[str, Any]] = []
        if character_pack_id:
            contexts.append(
                self._build_desktop_pet_character_pack_prompt_context(
                    character_pack_id=character_pack_id,
                    resource_manifest=None,
                    client_mode="memory",
                )
            )
        return self._merge_prompt_persona_contexts(*contexts)


    def _normalize_memory_tags(self, value: Any) -> list[str]:
        raw_items: list[str] = []
        if isinstance(value, list):
            raw_items = [str(item).strip() for item in value if str(item).strip()]
        elif isinstance(value, str):
            normalized = (
                str(value).replace("，", ",").replace("、", ",").replace("；", ",").replace(";", ",").replace("|", ",")
            )
            raw_items = parse_joined_tags(normalized)

        normalized_items: list[str] = []
        seen: set[str] = set()
        for item in raw_items:
            compact = normalize_text(item).strip("[](){}\"' ")
            if not compact or len(compact) > 16:
                continue
            dedupe_key = compact.lower()
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            normalized_items.append(compact)
            if len(normalized_items) >= 4:
                break
        return normalized_items

    def _apply_memory_tags_to_user_record(
        self,
        *,
        user_record: dict[str, Any],
        memory_tags: list[str],
    ) -> dict[str, Any]:
        merged_tags: list[str] = []
        seen: set[str] = set()
        for item in [*memory_tags, *list(user_record.get("semantic_tags") or [])]:
            tag = normalize_text(item)
            if not tag:
                continue
            dedupe_key = tag.lower()
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            merged_tags.append(tag)

        user_record["semantic_tags"] = merged_tags
        self.store.update_message_semantic_tags(user_record["source_id"], merged_tags)
        self._upsert_raw_record(user_record)
        return user_record

    def _apply_memory_metadata_to_user_record(
        self,
        *,
        user_record: dict[str, Any],
        memory_metadata: dict[str, Any],
    ) -> dict[str, Any]:
        metadata = dict(memory_metadata or {})
        user_record["memory_metadata"] = metadata
        self.store.update_message_memory_metadata(user_record["source_id"], metadata)
        self._upsert_raw_record(user_record)
        return user_record

    def _build_assistant_timeline_metadata(
        self,
        final_output: dict[str, Any],
    ) -> dict[str, Any]:
        output = final_output if isinstance(final_output, dict) else {}
        memory_metadata = output.get("memory_metadata")
        mood_tags = list(memory_metadata.get("mood_tags") or []) if isinstance(memory_metadata, dict) else []
        character = output.get("character")
        outfit = str(character.get("outfit") or "").strip() if isinstance(character, dict) else ""
        return {
            "response_emotion": str(output.get("emotion") or "").strip(),
            "response_outfit": outfit,
            "mood_tags": mood_tags,
        }

    def _build_memory_relationship_context(
        self,
        *,
        profile_user_id: str,
        character_pack_id: str,
        now_ts: int,
    ) -> str:
        manager = self._memcore_manager_if_enabled()
        if manager is not None:
            result = manager.acquaintance_note(
                profile_user_id=profile_user_id,
                session_id=profile_user_id,
                character_pack_id=character_pack_id,
                now_ts=now_ts,
            )
            if result:
                return result
        service = getattr(self, "memory_timeline_service", None)
        if service is None:
            return ""
        try:
            return str(
                service.build_acquaintance_prompt(
                    profile_user_id=profile_user_id,
                    character_pack_id=character_pack_id,
                    now_ts=now_ts,
                )
                or ""
            ).strip()
        except Exception as exc:
            logger.warning("memory relationship prompt failed: %s", exc)
            return ""

    def _normalize_choices(self, value: Any) -> list[dict[str, str]]:
        if not isinstance(value, list):
            return []

        normalized: list[dict[str, str]] = []
        seen_texts: set[str] = set()
        for index, item in enumerate(value, start=1):
            if isinstance(item, dict):
                text = str(item.get("text") or item.get("label") or "").strip()
                choice_id = str(item.get("id") or "").strip()
            else:
                text = str(item or "").strip()
                choice_id = ""

            if not text:
                continue
            dedupe_key = normalize_text(text).lower()
            if dedupe_key in seen_texts:
                continue
            seen_texts.add(dedupe_key)
            normalized.append(
                {
                    "id": choice_id or f"choice_{index}",
                    "text": text[:40],
                }
            )
            if len(normalized) >= 4:
                break
        return normalized


    def _build_turn_extra_user_context(
        self,
        payload: dict[str, Any] | None,
        client_context: ClientProtocolContext | None,
    ) -> str:
        return desktop_context_engine.build_turn_extra_user_context(
            self,
            payload,
            client_context,
        )

    def _merge_extra_user_context(self, *parts: Any) -> str:
        return desktop_context_engine.merge_extra_user_context(*parts)

    def _build_desktop_context_prompt(
        self,
        desktop_context: Any,
        client_context: ClientProtocolContext | None,
    ) -> str:
        return desktop_context_engine.build_desktop_context_prompt(desktop_context, client_context)

    def _sanitize_desktop_context_text(self, value: Any, limit: int) -> str:
        return desktop_context_engine.sanitize_desktop_context_text(value, limit)

    def _build_desktop_activity_prompt(
        self,
        activity: Any,
        client_context: ClientProtocolContext | None,
        *,
        profile_user_id: str = "",
        session_id: str = "",
    ) -> str:
        return desktop_context_engine.build_desktop_activity_prompt(
            self,
            activity,
            client_context,
            profile_user_id=profile_user_id,
            session_id=session_id,
        )

    def _build_desktop_music_timeline_prompt(
        self,
        activity: dict[str, Any],
        *,
        profile_user_id: str = "",
        session_id: str = "",
    ) -> str:
        return desktop_context_engine.build_desktop_music_timeline_prompt(
            self,
            activity,
            profile_user_id=profile_user_id,
            session_id=session_id,
        )

    def _safe_activity_seconds(self, value: Any) -> float:
        return desktop_context_engine.safe_activity_seconds(value)

    def _format_activity_time(self, value: Any) -> str:
        return desktop_context_engine.format_activity_time(value)


    def _build_current_visual_context(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        current_visual_payload: Any,
        visual_payload: dict[str, Any] | None = None,
        runtime_projection: dict[str, Any] | None = None,
        character_only: bool = False,
        resource_manifest: ResourceManifest | None = None,
    ) -> str:
        return visual_context_engine.build_current_visual_context(
            self,
            profile_user_id=profile_user_id,
            session_id=session_id,
            current_visual_payload=current_visual_payload,
            visual_payload=visual_payload,
            runtime_projection=runtime_projection,
            character_only=character_only,
            resource_manifest=resource_manifest,
        )

    def _schedule_visual_observations_for_payload(
        self,
        *,
        payload: dict[str, Any] | None,
        profile_user_id: str,
        session_id: str,
    ) -> None:
        visual_context_engine.schedule_visual_observations_for_payload(
            self,
            payload=payload,
            profile_user_id=profile_user_id,
            session_id=session_id,
        )

    def _handle_ready_visual_observation(
        self,
        target,
        observation: dict[str, Any],
    ) -> None:
        visual_context_engine.handle_ready_visual_observation(self, target, observation)

    def _get_user_runtime_projection(self, profile_user_id: str) -> dict[str, Any]:
        return visual_context_engine.get_user_runtime_projection(self, profile_user_id)

    def _get_user_bgm_tracks(self, profile_user_id: str) -> list[dict[str, Any]]:
        return visual_context_engine.get_user_bgm_tracks(self, profile_user_id)

    def _resolve_current_visual_payload(self, *, session_id: str, current_visual_payload: Any) -> dict[str, Any] | None:
        return visual_context_engine.resolve_current_visual_payload(
            self,
            session_id=session_id,
            current_visual_payload=current_visual_payload,
        )

    def _coerce_visual_payload(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        return visual_context_engine.coerce_visual_payload(payload)

    @staticmethod
    def _build_extra_context_audit_sections(candidates: list[tuple[str, Any]]) -> list[dict[str, str]]:
        sections: list[dict[str, str]] = []
        for name, text in candidates:
            rendered_name = str(name or "").strip()
            rendered_text = str(text or "").strip()
            if rendered_name and rendered_text:
                sections.append({"name": rendered_name, "text": rendered_text})
        return sections

    @staticmethod
    def _split_history_records(
        *,
        recent_raw: list[dict[str, Any]],
        user_message: str,
        now_ts: int,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        records = list(recent_raw or [])
        current_record: dict[str, Any] = {
            "role": "user",
            "content": user_message,
            "timestamp": now_ts,
        }
        if not records:
            return [], current_record

        last_record = records[-1]
        last_content = normalize_text(str(last_record.get("content", "") or ""))
        current_content = normalize_text(user_message)
        last_role = str(last_record.get("role", "") or "").strip().lower()
        last_kind = str(last_record.get("kind", "") or "").strip().lower()
        current_stimulus = any(
            candidate == "user"
            or candidate == "message.user"
            or candidate.startswith("message.user.")
            or candidate.startswith("event.")
            for candidate in (last_role, last_kind)
        )
        if current_stimulus and last_content == current_content:
            return records[:-1], last_record
        return records, current_record

    @staticmethod
    def _build_history_turns(records: list[dict[str, Any]]) -> list[dict[str, str]]:
        turns: list[dict[str, str]] = []
        for rec in records:
            raw_role = str(rec.get("role", "") or "").strip()
            role = raw_role.lower()
            content = str(rec.get("content", "") or "").strip()
            if not content:
                continue
            rendered_content = PromptContextMixin._render_memory_record_for_prompt(rec)
            if role == "assistant" or role.startswith("assistant."):
                turns.append({"role": "assistant", "content": rendered_content})
            elif role.startswith("npc:"):
                # npc: downgrade to user to avoid unsupported role in API
                turns.append({"role": "user", "content": rendered_content})
            else:
                turns.append({"role": "user", "content": rendered_content})
        return turns

    def _render_current_message_line(
        self,
        *,
        current_user_record: dict[str, Any],
    ) -> str:
        return self._render_memory_record_for_prompt(current_user_record)

    @staticmethod
    def _render_memory_record_for_prompt(record: dict[str, Any]) -> str:
        role = str(record.get("role") or "user").strip()
        if role.lower().startswith("event."):
            try:
                from memcore.rendering import render_prompt_message

                timezone = str(getattr(config, "MEMCORE_TIMEZONE", "") or "Asia/Shanghai").strip() or "Asia/Shanghai"
                return render_prompt_message(dict(record), tz=timezone)
            except Exception:
                pass
        return render_chat_line(
            role=role,
            content=str(record.get("content") or ""),
            timestamp=record.get("timestamp"),
        )

    def _upsert_raw_record(self, record: dict[str, Any]) -> None:
        if not bool(record.get("index_in_vector", True)):
            return
        if self._memcore_owns_legacy_vector_index():
            record["index_in_vector"] = False
            source_id = str(record.get("source_id") or "").strip()
            update_index_flag = getattr(getattr(self, "store", None), "update_message_index_in_vector", None)
            if source_id and callable(update_index_flag):
                update_index_flag(source_id, False)
            return
        vector_store = getattr(self, "vector_store", None)
        if vector_store is not None:
            vector_store.upsert_entries([build_raw_vector_entry(record)])
