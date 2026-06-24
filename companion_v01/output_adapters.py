from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from .client_protocol import (
    CharacterState,
    ClientMode,
    ClientProtocolContext,
    Live2DState,
    PetState,
    RuntimeState,
    SceneState,
)


class OutputAdapter(ABC):
    @abstractmethod
    def normalize_output(self, output: dict[str, Any], context: ClientProtocolContext) -> dict[str, Any]:
        raise NotImplementedError

    def _annotate(self, output: dict[str, Any], context: ClientProtocolContext) -> dict[str, Any]:
        normalized = dict(output or {})
        runtime_state = self._build_runtime_state(normalized, context)
        normalized["client_mode"] = context.effective_mode.value
        normalized["client"] = context.to_public_dict()
        normalized["_runtime_state"] = runtime_state.to_public_dict()
        return normalized

    def _build_runtime_state(self, output: dict[str, Any], context: ClientProtocolContext) -> RuntimeState:
        emotion = str(output.get("emotion") or "normal").strip() or "normal"
        scene_payload = output.get("scene") if isinstance(output.get("scene"), dict) else {}
        character_payload = output.get("character") if isinstance(output.get("character"), dict) else {}
        live2d_payload = output.get("live2d") if isinstance(output.get("live2d"), dict) else {}
        pet_payload = output.get("pet") if isinstance(output.get("pet"), dict) else {}

        scene_state = None
        if scene_payload:
            scene_state = SceneState(
                major=str(scene_payload.get("major") or ""),
                minor=str(scene_payload.get("minor") or ""),
                background_id=str(scene_payload.get("background") or scene_payload.get("background_id") or ""),
                bgm_id=str(scene_payload.get("bgm") or scene_payload.get("bgm_id") or ""),
                atmosphere_tags=[
                    str(item).strip()
                    for item in list(scene_payload.get("atmosphere") or scene_payload.get("atmosphere_tags") or [])
                    if str(item).strip()
                ][:8],
            )

        character_state = None
        if character_payload:
            outfit = str(character_payload.get("outfit") or character_payload.get("outfit_id") or "")
            expression = str(character_payload.get("expression") or output.get("emotion") or "")
            character_state = CharacterState(
                outfit_id=outfit,
                expression_id=expression,
                sprite_id=str(character_payload.get("sprite") or character_payload.get("sprite_id") or ""),
            )

        live2d_state = None
        if live2d_payload:
            live2d_state = Live2DState(
                model_id=str(live2d_payload.get("model") or live2d_payload.get("model_id") or ""),
                expression_id=str(live2d_payload.get("expression") or output.get("emotion") or ""),
                motion_id=str(live2d_payload.get("motion") or live2d_payload.get("motion_id") or ""),
                attention=str(live2d_payload.get("attention") or ""),
            )

        pet_state = None
        if pet_payload:
            pet_state = PetState(
                expression_id=str(pet_payload.get("expression") or output.get("emotion") or ""),
                motion_id=str(pet_payload.get("motion") or pet_payload.get("motion_id") or ""),
                attention=str(pet_payload.get("attention") or ""),
                bubble_priority=str(pet_payload.get("bubble_priority") or ""),
            )

        return RuntimeState(
            last_scene=scene_state,
            last_character=character_state,
            last_live2d=live2d_state,
            last_pet=pet_state,
            last_client_mode=context.effective_mode,
            last_emotion=emotion,
        )


class SceneStaticOutputAdapter(OutputAdapter):
    def normalize_output(self, output: dict[str, Any], context: ClientProtocolContext) -> dict[str, Any]:
        return self._annotate(output, context)


class QQTextOutputAdapter(OutputAdapter):
    def normalize_output(self, output: dict[str, Any], context: ClientProtocolContext) -> dict[str, Any]:
        normalized = dict(output or {})
        normalized.pop("scene", None)
        normalized.pop("character", None)
        normalized.pop("live2d", None)
        normalized.pop("pet", None)
        return self._annotate(normalized, context)


class DesktopPetOutputAdapter(OutputAdapter):
    def normalize_output(self, output: dict[str, Any], context: ClientProtocolContext) -> dict[str, Any]:
        normalized = dict(output or {})
        normalized.pop("scene", None)
        normalized.pop("live2d", None)
        normalized.pop("pet", None)

        character = normalized.get("character")
        if isinstance(character, dict):
            outfit = str(character.get("outfit") or character.get("outfit_id") or "").strip()
            normalized["character"] = {"outfit": outfit} if outfit else {}
        else:
            normalized.pop("character", None)
        return self._annotate(normalized, context)


class OutputAdapterRegistry:
    def __init__(self) -> None:
        self._adapters: dict[ClientMode, OutputAdapter] = {
            ClientMode.SCENE_STATIC: SceneStaticOutputAdapter(),
            ClientMode.QQ_TEXT: QQTextOutputAdapter(),
            ClientMode.DESKTOP_PET: DesktopPetOutputAdapter(),
        }

    def normalize(self, output: dict[str, Any], context: ClientProtocolContext) -> dict[str, Any]:
        adapter = self._adapters.get(context.effective_mode) or self._adapters[ClientMode.SCENE_STATIC]
        return adapter.normalize_output(output, context)
