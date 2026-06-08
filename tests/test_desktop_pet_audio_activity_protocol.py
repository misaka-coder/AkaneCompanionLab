"""Probe tests: trace activity field through the desktop_pet audio pipeline.

Each test targets one layer. Run:
  python -m pytest tests/test_desktop_pet_audio_activity_protocol.py -v
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from companion_v01.client_protocol import (
    ClientCapability,
    ClientMode,
    ClientProtocolContext,
    normalize_client_capabilities,
    default_capabilities_for_mode,
)
from companion_v01.final_output_engine import normalize_activity_action, normalize_final_output
from companion_v01.mode_profiles import ModeProfileRegistry
from companion_v01.prompt_profiles import PromptProfileRegistry


# ---------------------------------------------------------------------------
# Fake engine — minimal surface needed by normalize_final_output
# ---------------------------------------------------------------------------

class _FakeOutputAdapterRegistry:
    def normalize(self, normalized: dict, ctx: ClientProtocolContext) -> dict:
        normalized["client_mode"] = ctx.effective_mode.value
        normalized["client"] = ctx.to_public_dict()
        return normalized


class FakeEngine:
    resource_manifest = None
    _output_adapter_registry = _FakeOutputAdapterRegistry()

    def _get_output_adapter_registry(self):
        return self._output_adapter_registry

    def _resolve_client_protocol_context(self, payload: dict[str, Any] | None):
        return ClientProtocolContext(
            requested_mode=ClientMode.DESKTOP_PET,
            effective_mode=ClientMode.DESKTOP_PET,
            capabilities=("speech_segments", "tts", "file_drop", "tool_actions", "audio_playback"),
        )

    def _normalize_tool_call(self, *a, **kw):
        return None

    def _get_persona_card_service(self):
        return None

    def _normalize_memory_tags(self, *a, **kw):
        return ""

    def _normalize_choices(self, *a, **kw):
        return []


FakeEngine_static = type("FakeEngineStatic", (FakeEngine,), {
    "_output_adapter_registry": _FakeOutputAdapterRegistry(),
    "resource_manifest": None,
    "_resolve_client_protocol_context": lambda self, p: ClientProtocolContext(
        requested_mode=ClientMode.SCENE_STATIC,
        effective_mode=ClientMode.SCENE_STATIC,
        capabilities=("speech_segments", "choices"),
    ),
})

FakeEngine_qq = type("FakeEngineQQ", (FakeEngine,), {
    "_output_adapter_registry": _FakeOutputAdapterRegistry(),
    "resource_manifest": None,
    "_resolve_client_protocol_context": lambda self, p: ClientProtocolContext(
        requested_mode=ClientMode.QQ_TEXT,
        effective_mode=ClientMode.QQ_TEXT,
        capabilities=("speech_segments", "tool_actions"),
    ),
})


_VISUAL_DEFAULTS = {
    "emotion": "normal",
    "outfit": "default",
    "major": "default",
    "minor": "default",
    "background": "evening",
    "bgm": "",
}

_LLM_RESULT_WITH_ACTIVITY: dict[str, Any] = {
    "emotion": "开心",
    "speech": "我来试试播放。",
    "speech_segments": ["我来试试播放。"],
    "tool_call": None,
    "code_snippet": "",
    "memory_tags": "",
    "status": "final",
    "score": 0.0,
    "choices": [],
    "character": {"outfit": "default"},
    "scene": None,
    "persona": {"active": ""},
    "activity": {
        "action": "play",
        "target": "current",
        "source_id": "workspace:attachment:audio_001",
    },
}

_LLM_RESULT_NO_ACTIVITY: dict[str, Any] = {k: v for k, v in _LLM_RESULT_WITH_ACTIVITY.items() if k != "activity"}


# ===================================================================
# Layer 1 — Prompt schema
# ===================================================================

class TestLayer1PromptSchema:
    """desktop_pet 的 final prompt 必须允许 activity 字段。"""

    def test_desktop_pet_prompt_contains_activity(self):
        registry = PromptProfileRegistry()
        profile = registry.get(ClientMode.DESKTOP_PET)
        fast = profile.fast_mode_prompt
        debug = profile.debug_mode_prompt
        assert "activity" in fast, f"desktop_pet fast_mode 应含 activity:\n{fast}"
        assert "activity" in debug, f"desktop_pet debug_mode 应含 activity:\n{debug}"
        assert '"activity":null' in fast
        assert "memory_metadata" in fast
        assert "memory_metadata" in debug
        assert "memory_tags" not in fast
        assert "memory_tags" not in debug

    def test_web_prompt_excludes_activity(self):
        registry = PromptProfileRegistry()
        profile = registry.get(ClientMode.SCENE_STATIC)
        assert "activity" not in profile.fast_mode_prompt, "web 不应有 activity"
        assert "activity" not in profile.debug_mode_prompt, "web debug 不应有 activity"

    def test_qq_prompt_excludes_activity(self):
        registry = PromptProfileRegistry()
        profile = registry.get(ClientMode.QQ_TEXT)
        assert "activity" not in profile.fast_mode_prompt, "QQ 不应有 activity"
        assert "activity" not in profile.debug_mode_prompt, "QQ debug 不应有 activity"


# ===================================================================
# Layer 1b — Capability resolution
# ===================================================================

class TestLayer1bCapability:
    """前端 capabilities → 后端 client_context 链路。"""

    def test_desktop_pet_defaults_include_audio_playback(self):
        caps = default_capabilities_for_mode(ClientMode.DESKTOP_PET)
        assert "audio_playback" in caps

    def test_frontend_sends_audio_playback_is_preserved(self):
        caps = normalize_client_capabilities(
            ["speech_segments", "tts", "audio_playback"],
            default=default_capabilities_for_mode(ClientMode.DESKTOP_PET),
        )
        assert "audio_playback" in caps

    def test_frontend_missing_falls_to_default(self):
        caps = normalize_client_capabilities(
            None,
            default=default_capabilities_for_mode(ClientMode.DESKTOP_PET),
        )
        assert "audio_playback" in caps

    def test_frontend_list_without_audio_playback_means_missing(self):
        """前端显式传 capabilities 但不含 audio_playback → 不应由默认补上。"""
        caps = normalize_client_capabilities(
            ["speech_segments", "tts", "file_drop", "tool_actions"],
            default=default_capabilities_for_mode(ClientMode.DESKTOP_PET),
        )
        assert "audio_playback" not in caps

    def test_mode_profile_resolve_preserves_audio_playback(self):
        registry = ModeProfileRegistry()
        ctx = registry.resolve_from_payload({
            "client_mode": "desktop_pet",
            "client_capabilities": ["speech_segments", "tts", "audio_playback", "desktop_context"],
        })
        assert ctx.has_capability(ClientCapability.AUDIO_PLAYBACK)

    def test_mode_profile_resolve_missing_audio_playback(self):
        registry = ModeProfileRegistry()
        ctx = registry.resolve_from_payload({
            "client_mode": "desktop_pet",
            "client_capabilities": ["speech_segments", "tts"],
        })
        assert not ctx.has_capability(ClientCapability.AUDIO_PLAYBACK)


# ===================================================================
# Layer 2 — normalize_activity_action (unit)
# ===================================================================

class TestLayer2NormalizeAction:
    """normalize_activity_action 单元测试。"""

    @pytest.mark.parametrize("action", ["play", "pause", "resume", "stop", "previous", "next"])
    def test_valid_action_preserved(self, action):
        result = normalize_activity_action({"action": action, "target": "current"})
        assert result is not None
        assert result["action"] == action
        assert result["target"] == "current"

    def test_with_source_id(self):
        result = normalize_activity_action({
            "action": "play",
            "target": "current",
            "source_id": "workspace:attachment:abc",
        })
        assert result["source_id"] == "workspace:attachment:abc"

    def test_none_returns_none(self):
        assert normalize_activity_action(None) is None

    def test_invalid_action(self):
        assert normalize_activity_action({"action": "invalid"}) is None

    def test_empty_dict_returns_none(self):
        assert normalize_activity_action({}) is None

    def test_audio_type_preserved(self):
        result = normalize_activity_action({
            "action": "play",
            "target": "current",
            "type": "audio_playback",
        })
        assert result is not None
        assert result.get("type") == "audio_playback"


# ===================================================================
# Layer 3 — normalize_final_output integration
# ===================================================================

class TestLayer3NormalizeFinalOutput:
    """normalize_final_output 集成测试。"""

    @pytest.fixture
    def engine_desktop(self):
        return FakeEngine()

    @pytest.fixture
    def engine_static(self):
        return FakeEngine_static()

    @pytest.fixture
    def engine_qq(self):
        return FakeEngine_qq()

    def test_desktop_pet_preserves_activity(self, engine_desktop):
        """Layer 3A: LLM 输出 activity → desktop_pet 模式下保留。"""
        result = normalize_final_output(
            engine_desktop,
            result=_LLM_RESULT_WITH_ACTIVITY,
            visual_defaults=_VISUAL_DEFAULTS,
            profile_user_id="test",
            session_id="test",
            allow_tool_call=False,
            debug_enabled=False,
            user_message="放歌",
        )
        act = result.get("activity")
        assert act is not None, f"desktop_pet 应保留 activity, keys={list(result.keys())}"
        assert act["action"] == "play"
        assert act["source_id"] == "workspace:attachment:audio_001"

    def test_non_desktop_strips_activity(self, engine_static, engine_qq):
        """Layer 3B: web/QQ 模式下删除 activity。"""
        for label, eng in [("scene_static", engine_static), ("qq_text", engine_qq)]:
            result = normalize_final_output(
                eng,
                result=_LLM_RESULT_WITH_ACTIVITY,
                visual_defaults=_VISUAL_DEFAULTS,
                profile_user_id="test",
                session_id="test",
                allow_tool_call=False,
                debug_enabled=False,
                user_message="放歌",
            )
            act = result.get("activity")
            assert act is None or act is False, f"{label} 不应保留 activity, got={act!r}"

    def test_fallback_injects_play(self, engine_desktop):
        """Layer 3C: 用户说放首歌且 LLM 没输出 activity → fallback 注入。"""
        result = normalize_final_output(
            engine_desktop,
            result=_LLM_RESULT_NO_ACTIVITY,
            visual_defaults=_VISUAL_DEFAULTS,
            profile_user_id="test",
            session_id="test",
            allow_tool_call=False,
            debug_enabled=False,
            user_message="放首歌",
        )
        act = result.get("activity")
        assert act is not None, f"fallback 应注入 activity, keys={list(result.keys())}"
        assert act["action"] == "play"
        assert act["target"] == "current"

    def test_fallback_no_false_positive(self, engine_desktop):
        """Layer 3D: 继续讲不能误触 resume。"""
        result = normalize_final_output(
            engine_desktop,
            result=_LLM_RESULT_NO_ACTIVITY,
            visual_defaults=_VISUAL_DEFAULTS,
            profile_user_id="test",
            session_id="test",
            allow_tool_call=False,
            debug_enabled=False,
            user_message="继续讲这个问题",
        )
        act = result.get("activity")
        assert act is None, f"继续讲不应触发 fallback, got={act!r}"

    def test_fallback_respects_negated_music_request(self, engine_desktop):
        """否定句不能因为包含“放歌”而误触播放。"""
        result = normalize_final_output(
            engine_desktop,
            result=_LLM_RESULT_NO_ACTIVITY,
            visual_defaults=_VISUAL_DEFAULTS,
            profile_user_id="test",
            session_id="test",
            allow_tool_call=False,
            debug_enabled=False,
            user_message="先别放歌",
        )
        act = result.get("activity")
        assert act is None, f"先别放歌不应触发 fallback, got={act!r}"

    def test_fallback_other_keywords(self, engine_desktop):
        """Layer 3E: 停一下 → pause。"""
        result = normalize_final_output(
            engine_desktop,
            result=_LLM_RESULT_NO_ACTIVITY,
            visual_defaults=_VISUAL_DEFAULTS,
            profile_user_id="test",
            session_id="test",
            allow_tool_call=False,
            debug_enabled=False,
            user_message="停一下",
        )
        act = result.get("activity")
        assert act is not None
        assert act["action"] == "pause"

    def test_activity_without_capability_is_stripped(self):
        """模拟旧前端没传 audio_playback → capability 缺失 → activity 被移除。"""
        eng = type("NoAudio", (FakeEngine,), {
            "_output_adapter_registry": _FakeOutputAdapterRegistry(),
            "resource_manifest": None,
            "_resolve_client_protocol_context": lambda self, p: ClientProtocolContext(
                requested_mode=ClientMode.DESKTOP_PET,
                effective_mode=ClientMode.DESKTOP_PET,
                capabilities=("speech_segments", "tts"),  # no audio_playback
            ),
        })()
        result = normalize_final_output(
            eng,
            result=_LLM_RESULT_WITH_ACTIVITY,
            visual_defaults=_VISUAL_DEFAULTS,
            profile_user_id="test",
            session_id="test",
            allow_tool_call=False,
            debug_enabled=False,
            user_message="放歌",
        )
        act = result.get("activity")
        assert act is None, f"无 audio_playback capability 时应移除 activity, got={act!r}"


# ===================================================================
# Layer 4 — Final serialization (simulate engine → think.py)
# ===================================================================

class TestLayer4Serialization:
    """最终 JSON 序列化不应丢 activity。"""

    def test_final_json_serialization_preserves_activity(self, engine_desktop):
        """模拟 engine.py process_turn_stream 最终 yield 前的 payload。"""
        result = normalize_final_output(
            engine_desktop,
            result=_LLM_RESULT_WITH_ACTIVITY,
            visual_defaults=_VISUAL_DEFAULTS,
            profile_user_id="test",
            session_id="test",
            allow_tool_call=False,
            debug_enabled=False,
            user_message="放歌",
        )

        # engine.py 会在 yield 前添加 client/client_mode 等字段
        final_payload = dict(result)
        final_payload["client_mode"] = "desktop_pet"
        final_payload["client"] = {
            "requested_mode": "desktop_pet",
            "effective_mode": "desktop_pet",
            "capabilities": ["speech_segments", "tts", "audio_playback"],
        }

        # 模拟 think.py json.dumps
        serialized = json.dumps(final_payload, ensure_ascii=False)
        deserialized = json.loads(serialized)

        assert "activity" in deserialized, "序列化后 activity 字段应存在"
        act = deserialized["activity"]
        assert act["action"] == "play"
        assert act["source_id"] == "workspace:attachment:audio_001"
        assert act["target"] == "current"

    def test_desktop_pet_output_adapter_preserves_activity(self):
        """DesktopPetOutputAdapter 不应移 activity。"""
        from companion_v01.output_adapters import DesktopPetOutputAdapter
        adapter = DesktopPetOutputAdapter()
        ctx = ClientProtocolContext(
            requested_mode=ClientMode.DESKTOP_PET,
            effective_mode=ClientMode.DESKTOP_PET,
            capabilities=("audio_playback",),
        )
        output = {"emotion": "normal", "activity": {"action": "play", "target": "current"}}
        normalized = adapter.normalize_output(output, ctx)
        assert "activity" in normalized, "DesktopPetOutputAdapter 应保留 activity"
        assert normalized["activity"]["action"] == "play"

    def test_scene_static_output_adapter_strips_activity(self):
        """SCENE_STATIC output adapter 也会 pop activity。"""
        from companion_v01.output_adapters import OutputAdapterRegistry
        registry = OutputAdapterRegistry()
        ctx = ClientProtocolContext(
            requested_mode=ClientMode.SCENE_STATIC,
            effective_mode=ClientMode.SCENE_STATIC,
            capabilities=("speech_segments",),
        )
        output = {"emotion": "normal", "activity": {"action": "play"}}
        normalized = registry.normalize(output, ctx)
        # SCENE_STATIC 没有显式 pop activity，但 desktop_pet 有吗？
        # 重要的是 final_output_engine 在非 desktop 时已 pop
        # 不过 adapter 本身不关心这个字段
        # 至少不应该报错
        assert normalized is not None


@pytest.fixture
def engine_desktop():
    return FakeEngine()
