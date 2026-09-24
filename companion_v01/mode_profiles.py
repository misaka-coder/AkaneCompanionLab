from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .client_protocol import (
    ClientCapability,
    ClientMode,
    ClientProtocolContext,
    default_capabilities_for_mode,
    normalize_client_capabilities,
    normalize_client_mode,
)


@dataclass(frozen=True)
class ModeProfile:
    mode: ClientMode
    output_profile: str
    renderer_profile: str
    implemented: bool = False
    fallback_mode: ClientMode = ClientMode.SCENE_STATIC
    required_capabilities: tuple[str, ...] = field(default_factory=tuple)


class ModeProfileRegistry:
    def __init__(self) -> None:
        self._profiles: dict[ClientMode, ModeProfile] = {
            ClientMode.SCENE_STATIC: ModeProfile(
                mode=ClientMode.SCENE_STATIC,
                output_profile=ClientMode.SCENE_STATIC.value,
                renderer_profile=ClientMode.SCENE_STATIC.value,
                implemented=True,
                required_capabilities=(
                    ClientCapability.SPEECH_SEGMENTS.value,
                    ClientCapability.BACKGROUND.value,
                    ClientCapability.STATIC_SPRITE.value,
                ),
            ),
            ClientMode.SCENE_LIVE2D: ModeProfile(
                mode=ClientMode.SCENE_LIVE2D,
                output_profile=ClientMode.SCENE_LIVE2D.value,
                renderer_profile=ClientMode.SCENE_LIVE2D.value,
                implemented=False,
                required_capabilities=(
                    ClientCapability.SPEECH_SEGMENTS.value,
                    ClientCapability.BACKGROUND.value,
                    ClientCapability.LIVE2D.value,
                ),
            ),
            ClientMode.DESKTOP_PET: ModeProfile(
                mode=ClientMode.DESKTOP_PET,
                output_profile=ClientMode.DESKTOP_PET.value,
                renderer_profile=ClientMode.DESKTOP_PET.value,
                implemented=True,
                required_capabilities=(ClientCapability.SPEECH_SEGMENTS.value,),
            ),
            ClientMode.QQ_TEXT: ModeProfile(
                mode=ClientMode.QQ_TEXT,
                output_profile=ClientMode.QQ_TEXT.value,
                renderer_profile=ClientMode.QQ_TEXT.value,
                implemented=True,
                required_capabilities=(ClientCapability.SPEECH_SEGMENTS.value,),
            ),
        }

    def resolve_from_payload(self, payload: dict[str, Any] | None) -> ClientProtocolContext:
        source = payload if isinstance(payload, dict) else {}
        raw_mode = source.get("client_mode")
        if raw_mode is None and str(source.get("mode") or "").strip().lower() in {mode.value for mode in ClientMode}:
            raw_mode = source.get("mode")
        requested_mode = normalize_client_mode(raw_mode)
        capabilities = normalize_client_capabilities(
            source.get("client_capabilities"),
            default=default_capabilities_for_mode(requested_mode),
        )
        return self.resolve(requested_mode=requested_mode, capabilities=capabilities)

    def resolve(self, *, requested_mode: ClientMode, capabilities: tuple[str, ...]) -> ClientProtocolContext:
        requested_profile = self._profiles.get(requested_mode) or self._profiles[ClientMode.SCENE_STATIC]
        effective_profile = requested_profile
        degraded_from = ""
        degrade_reason = ""

        missing_required = [
            capability for capability in requested_profile.required_capabilities if capability not in set(capabilities)
        ]
        if not requested_profile.implemented:
            effective_profile = self._profiles[requested_profile.fallback_mode]
            degraded_from = requested_profile.mode.value
            degrade_reason = "profile_not_implemented"
        elif missing_required:
            effective_profile = self._profiles[requested_profile.fallback_mode]
            degraded_from = requested_profile.mode.value
            degrade_reason = f"missing_capability:{','.join(missing_required)}"

        return ClientProtocolContext(
            requested_mode=requested_profile.mode,
            effective_mode=effective_profile.mode,
            capabilities=tuple(capabilities),
            output_profile=effective_profile.output_profile,
            renderer_profile=effective_profile.renderer_profile,
            degraded_from=degraded_from,
            degrade_reason=degrade_reason,
        )
