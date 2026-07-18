"""Immutable per-Bot runtime settings snapshots.

The process-level ``config`` module remains the boot/default source during the
Slice 2 migration.  A BotRuntime captures the effective model settings once so
the LLM client no longer reads mutable module globals during a request.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class BotSettingsView:
    """Effective model settings for one BotRuntime.

    Secret fields are deliberately excluded from repr/public snapshots.  The
    view is immutable so a second Bot cannot be affected by another Bot's
    settings update or request-time provider selection.
    """

    text_api_key: str = field(default="", repr=False)
    text_base_url: str = ""
    text_model_name: str = "deepseek-chat"
    text_api_protocol: str = "auto"
    aux_api_key: str = field(default="", repr=False)
    aux_base_url: str = ""
    aux_model_name: str = "deepseek-chat"
    aux_api_protocol: str = "auto"
    chat_api_key: str = field(default="", repr=False)
    chat_base_url: str = ""
    chat_model_name: str = ""
    chat_api_protocol: str = "auto"

    @classmethod
    def from_config(cls, config_module: Any) -> "BotSettingsView":
        return cls(
            text_api_key=_text(getattr(config_module, "TEXT_API_KEY", "")),
            text_base_url=_text(getattr(config_module, "TEXT_BASE_URL", "")),
            text_model_name=_text(getattr(config_module, "TEXT_MODEL_NAME", "deepseek-chat")) or "deepseek-chat",
            text_api_protocol=_text(getattr(config_module, "TEXT_API_PROTOCOL", "auto")) or "auto",
            aux_api_key=_text(getattr(config_module, "AUX_API_KEY", "")),
            aux_base_url=_text(getattr(config_module, "AUX_BASE_URL", "")),
            aux_model_name=_text(getattr(config_module, "AUX_MODEL_NAME", "deepseek-chat")) or "deepseek-chat",
            aux_api_protocol=_text(getattr(config_module, "AUX_API_PROTOCOL", "auto")) or "auto",
            chat_api_key=_text(getattr(config_module, "CHAT_API_KEY", "")),
            chat_base_url=_text(getattr(config_module, "CHAT_BASE_URL", "")),
            chat_model_name=_text(getattr(config_module, "CHAT_MODEL_NAME", "")),
            chat_api_protocol=_text(getattr(config_module, "CHAT_API_PROTOCOL", "auto")) or "auto",
        )

    def overlay(self, overrides: Mapping[str, Any] | None = None) -> "BotSettingsView":
        if not overrides:
            return self
        allowed = {
            "text_api_key",
            "text_base_url",
            "text_model_name",
            "text_api_protocol",
            "aux_api_key",
            "aux_base_url",
            "aux_model_name",
            "aux_api_protocol",
            "chat_api_key",
            "chat_base_url",
            "chat_model_name",
            "chat_api_protocol",
        }
        unknown = sorted(str(key) for key in overrides if key not in allowed)
        if unknown:
            raise ValueError(f"bot_settings_unknown_field:{unknown[0]}")
        values = {key: _text(value) for key, value in overrides.items()}
        return replace(self, **values)

    def public_snapshot(self) -> dict[str, Any]:
        return {
            "text": {
                "base_url": self.text_base_url,
                "model": self.text_model_name,
                "protocol": self.text_api_protocol,
                "configured": _configured(
                    self.text_api_key,
                    self.text_base_url,
                    self.text_model_name,
                    self.text_api_protocol,
                ),
            },
            "aux": {
                "base_url": self.aux_base_url,
                "model": self.aux_model_name,
                "protocol": self.aux_api_protocol,
                "configured": _configured(
                    self.aux_api_key,
                    self.aux_base_url,
                    self.aux_model_name,
                    self.aux_api_protocol,
                ),
            },
            "chat": {
                "base_url": self.chat_base_url,
                "model": self.chat_model_name,
                "protocol": self.chat_api_protocol,
                "configured": _configured(
                    self.chat_api_key,
                    self.chat_base_url,
                    self.chat_model_name,
                    self.chat_api_protocol,
                ),
            },
        }


def _text(value: Any) -> str:
    return str(value or "").strip()


def _configured(api_key: str, base_url: str, model: str, protocol: str) -> bool:
    return bool(base_url and model and (protocol == "ollama" or api_key))


__all__ = ["BotSettingsView"]
