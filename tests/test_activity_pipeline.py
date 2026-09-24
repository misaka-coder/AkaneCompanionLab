"""Debug script: trace activity field through the normalize pipeline.

Tests the core activity normalization logic directly without
depending on the full engine interface.

Run:
  python -m pytest tests/test_activity_pipeline.py -x -v -s
"""

import json
from typing import Any

import pytest


# --- Import the functions we want to test ---
from companion_v01.final_output_engine import normalize_activity_action
from companion_v01.client_protocol import (
    ClientMode,
    ClientCapability,
    ClientProtocolContext,
    normalize_client_capabilities,
    default_capabilities_for_mode,
)
from companion_v01.mode_profiles import ModeProfileRegistry
from companion_v01.prompt_profiles import PromptProfileRegistry


class TestNormalizeActivityAction:
    """直接测试 normalize_activity_action 函数。"""

    def test_valid_actions(self):
        for action in ["play", "pause", "resume", "stop", "previous", "next"]:
            result = normalize_activity_action({"action": action, "target": "current"})
            assert result is not None, f"{action} should be valid"
            assert result["action"] == action

    def test_with_source_id(self):
        result = normalize_activity_action({
            "action": "play",
            "target": "current",
            "source_id": "workspace:attachment:abc123",
        })
        assert result is not None
        assert result["source_id"] == "workspace:attachment:abc123"

    def test_null_returns_none(self):
        assert normalize_activity_action(None) is None

    def test_invalid_action_returns_none(self):
        assert normalize_activity_action({"action": "fly", "target": "current"}) is None

    def test_unknown_type_stripped(self):
        """非 audio_playback 的 type 应该被移除。"""
        result = normalize_activity_action({
            "action": "play",
            "target": "current",
            "type": "invalid_type",
        })
        assert result is not None
        assert "type" not in result


class TestNoImplicitActivity:
    """媒体动作必须来自显式 activity/tool，不从自然语言猜测。"""

    def test_activity_normalization_does_not_define_keyword_fallback(self):
        assert normalize_activity_action({"action": "下一首"}) is None


class TestCapabilityResolution:
    """测试 capabilities 注册/传递链路。"""

    def test_desktop_pet_defaults_include_audio_playback(self):
        caps = default_capabilities_for_mode(ClientMode.DESKTOP_PET)
        assert "audio_playback" in caps, f"desktop_pet 默认应有 audio_playback: {caps}"

    def test_frontend_caps_preserved(self):
        caps = normalize_client_capabilities(
            ["speech_segments", "tts", "file_drop", "tool_actions", "audio_playback"],
            default=default_capabilities_for_mode(ClientMode.DESKTOP_PET),
        )
        assert "audio_playback" in caps

    def test_frontend_missing_caps_falls_back_to_default(self):
        caps = normalize_client_capabilities(
            None,
            default=default_capabilities_for_mode(ClientMode.DESKTOP_PET),
        )
        assert "audio_playback" in caps

    def test_client_context_has_capability(self):
        ctx = ClientProtocolContext(
            requested_mode=ClientMode.DESKTOP_PET,
            effective_mode=ClientMode.DESKTOP_PET,
            capabilities=("speech_segments", "tts", "audio_playback"),
        )
        assert ctx.has_capability(ClientCapability.AUDIO_PLAYBACK)

    def test_scene_static_no_audio_playback(self):
        ctx = ClientProtocolContext(
            requested_mode=ClientMode.SCENE_STATIC,
            effective_mode=ClientMode.SCENE_STATIC,
            capabilities=("speech_segments",),
        )
        assert not ctx.has_capability(ClientCapability.AUDIO_PLAYBACK)

    def test_frontend_sends_caps_without_audio_playback(self):
        """模拟旧版前端没传 audio_playback 的情况。"""
        caps = normalize_client_capabilities(
            ["speech_segments", "tts", "file_drop", "tool_actions"],
            default=default_capabilities_for_mode(ClientMode.DESKTOP_PET),
        )
        # 前端显式传了列表，不使用默认值
        assert "audio_playback" not in caps, "前端没传 audio_playback 时应缺失"


class TestModeProfileResolution:
    """测试 mode_profile → client_protocol_context 链路。"""

    def test_desktop_pet_resolve_from_payload(self):
        registry = ModeProfileRegistry()
        ctx = registry.resolve_from_payload({
            "client_mode": "desktop_pet",
            "client_capabilities": ["speech_segments", "tts", "audio_playback", "desktop_context"],
        })
        assert ctx is not None
        assert ctx.effective_mode == ClientMode.DESKTOP_PET
        assert ctx.has_capability(ClientCapability.AUDIO_PLAYBACK)

    def test_desktop_pet_resolve_missing_capability(self):
        """前端没传 audio_playback → 应该缺失。"""
        registry = ModeProfileRegistry()
        ctx = registry.resolve_from_payload({
            "client_mode": "desktop_pet",
            "client_capabilities": ["speech_segments", "tts"],
        })
        assert not ctx.has_capability(ClientCapability.AUDIO_PLAYBACK)


class TestPromptSchema:
    """验证 prompt schema 正确包含/排除 activity。"""

    def test_desktop_pet_schema_has_activity(self):
        registry = PromptProfileRegistry()
        profile = registry.get(ClientMode.DESKTOP_PET)
        assert "activity" in profile.fast_mode_prompt, "desktop_pet schema 应有 activity"
        assert "activity" in profile.debug_mode_prompt, "desktop_pet debug schema 应有 activity"

    def test_qq_text_schema_no_activity(self):
        registry = PromptProfileRegistry()
        profile = registry.get(ClientMode.QQ_TEXT)
        assert "activity" not in profile.fast_mode_prompt, "QQ 不应有 activity"
        assert "activity" not in profile.debug_mode_prompt, "QQ debug 不应有 activity"

    def test_scene_static_schema_no_activity(self):
        registry = PromptProfileRegistry()
        profile = registry.get(ClientMode.SCENE_STATIC)
        assert "activity" not in profile.fast_mode_prompt, "web 不应有 activity"
        assert "activity" not in profile.debug_mode_prompt, "web debug 不应有 activity"
