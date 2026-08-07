"""Deterministic multimodal chat-model routing tests.

Covers the per-turn ModelExecutionTarget resolution in LLMRuntime and the
engine-side one-way tool-image upgrade.  No real provider is contacted; the
tests build the same SimpleNamespace clients the rest of the suite uses.
"""

from __future__ import annotations

import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from companion_v01.engine import AkaneMemoryEngine
from companion_v01.llm_runtime import LLMRuntime, ModelBundle, ModelExecutionTarget
from companion_v01.runtime_settings import BotSettingsView


def _chat_client(host: str = "chat.example", protocol: str = "openai") -> SimpleNamespace:
    return SimpleNamespace(_akane_protocol=protocol, protocol=protocol, base_url=f"https://{host}/v1")


def _vision_client(host: str = "vision.example", protocol: str = "openai") -> SimpleNamespace:
    return SimpleNamespace(_akane_protocol=protocol, protocol=protocol, base_url=f"https://{host}/v1")


def _runtime(
    *,
    vision_enabled: bool = True,
    vision_base_url: str = "https://vision.example/v1",
    vision_model_name: str = "gemini-2.5-flash",
    vision_api_key: str = "vision-key",
    vision_protocol: str = "openai",
    chat_base_url: str = "https://chat.example/v1",
    chat_model_name: str = "deepseek-v4-flash",
    chat_api_key: str = "chat-key",
    chat_protocol: str = "openai",
    chat_supports_images: bool = False,
    chat_max_output_tokens: int = 0,
) -> LLMRuntime:
    runtime = LLMRuntime.__new__(LLMRuntime)
    runtime._config_module = __import__("config")
    runtime._bundle_lock = threading.RLock()
    runtime._metrics_lock = threading.RLock()
    runtime._last_error_lock = threading.RLock()
    runtime._last_error = {}
    runtime.settings = BotSettingsView(
        vision_enabled=vision_enabled,
        vision_api_key=vision_api_key,
        vision_base_url=vision_base_url,
        vision_model_name=vision_model_name,
        vision_api_protocol=vision_protocol,
        vision_request_timeout=30.0,
        chat_api_key=chat_api_key,
        chat_base_url=chat_base_url,
        chat_model_name=chat_model_name,
        chat_api_protocol=chat_protocol,
        chat_supports_images=chat_supports_images,
        llm_chat_max_output_tokens=chat_max_output_tokens,
    )
    chat_client = _chat_client(protocol=chat_protocol)
    chat_client._akane_bundle_role = "chat"
    runtime.chat = ModelBundle(client=chat_client, model=chat_model_name)
    if vision_enabled and vision_api_key and vision_base_url and vision_model_name:
        vision_client = _vision_client(protocol=vision_protocol)
        vision_client._akane_bundle_role = "vision"
        runtime.vision = ModelBundle(
            client=vision_client,
            model=vision_model_name,
        )
    else:
        runtime.vision = None
    return runtime


class ModelExecutionTargetResolutionTests(unittest.TestCase):
    def test_text_only_routes_to_chat_target(self) -> None:
        runtime = _runtime()
        target = runtime.resolve_turn_execution_target(has_real_images=False)
        self.assertIsInstance(target, ModelExecutionTarget)
        self.assertEqual(target.role, "chat")
        self.assertEqual(target.model, "deepseek-v4-flash")
        self.assertEqual(target.reason, "text_only")

    def test_text_only_honors_legacy_chat_override(self) -> None:
        runtime = _runtime()
        target = runtime.resolve_turn_execution_target(has_real_images=False, chat_model_override="deepseek-v4-pro")
        self.assertIsInstance(target, ModelExecutionTarget)
        self.assertEqual(target.role, "chat")
        self.assertEqual(target.model, "deepseek-v4-pro")

    def test_image_with_vision_bundle_routes_to_vision(self) -> None:
        runtime = _runtime()
        target = runtime.resolve_turn_execution_target(has_real_images=True)
        self.assertIsInstance(target, ModelExecutionTarget)
        self.assertEqual(target.role, "vision")
        self.assertEqual(target.model, "gemini-2.5-flash")
        self.assertEqual(target.reason, "native_image_present")
        # Must NOT reuse the chat client: independent endpoint.
        self.assertIsNot(target.bundle.client, runtime.chat.client)
        self.assertNotIn("chat.example", target.bundle.client.base_url)

    def test_image_with_same_vision_and_chat_route_reuses_chat_bundle(self) -> None:
        runtime = _runtime(
            vision_api_key="chat-key",
            vision_base_url="https://chat.example/v1",
            vision_model_name="deepseek-v4-flash",
            vision_protocol="openai",
        )
        # _build_vision_bundle must detect the identical route and reuse the
        # chat bundle instead of opening a duplicate provider handle.
        built: list[dict] = []
        with patch("companion_v01.llm_runtime.build_llm_client", side_effect=lambda **kw: built.append(kw) or _vision_client()):
            bundle = runtime._build_vision_bundle()
        self.assertIs(bundle, runtime.chat)
        self.assertEqual(built, [])

    def test_same_route_reload_reuses_new_chat_bundle(self) -> None:
        runtime = _runtime(
            vision_api_key="chat-key",
            vision_base_url="https://chat.example/v1",
            vision_model_name="deepseek-v4-flash",
            vision_protocol="openai",
        )
        new_client = _chat_client(host="chat.example", protocol="openai")
        new_client._akane_bundle_role = "chat"
        new_chat = ModelBundle(client=new_client, model="deepseek-v4-flash")
        with patch("companion_v01.llm_runtime.build_llm_client") as builder:
            bundle = runtime._build_vision_bundle(chat_bundle=new_chat)
        self.assertIs(bundle, new_chat)
        builder.assert_not_called()

    def test_build_vision_bundle_uses_independent_endpoint(self) -> None:
        runtime = _runtime()
        built: list[dict] = []
        with patch(
            "companion_v01.llm_runtime.build_llm_client",
            side_effect=lambda **kw: built.append(kw) or _vision_client("vision.example"),
        ):
            bundle = runtime._build_vision_bundle()
        self.assertIsNotNone(bundle)
        self.assertEqual(bundle.model, "gemini-2.5-flash")
        self.assertEqual(len(built), 1)
        self.assertEqual(built[0]["api_key"], "vision-key")
        self.assertEqual(built[0]["base_url"], "https://vision.example/v1")
        self.assertEqual(built[0]["protocol"], "openai")

    def test_build_vision_bundle_none_when_not_configured(self) -> None:
        runtime = _runtime(vision_api_key="", vision_base_url="", vision_model_name="")
        self.assertIsNone(runtime._build_vision_bundle())

    def test_image_without_vision_bundle_uses_image_capable_chat(self) -> None:
        runtime = _runtime(vision_api_key="", vision_base_url="", vision_model_name="", chat_supports_images=True)
        target = runtime.resolve_turn_execution_target(has_real_images=True)
        self.assertIsInstance(target, ModelExecutionTarget)
        self.assertEqual(target.role, "chat")
        self.assertEqual(target.reason, "chat_supports_images")

    def test_vision_master_switch_blocks_image_capable_chat(self) -> None:
        runtime = _runtime(
            vision_enabled=False,
            vision_api_key="",
            vision_base_url="",
            vision_model_name="",
            chat_supports_images=True,
        )
        target = runtime.resolve_turn_execution_target(has_real_images=True)
        self.assertIsInstance(target, dict)
        self.assertEqual(target.get("status"), "unavailable")
        self.assertEqual(target.get("reason"), "vision_disabled")

    def test_image_with_no_image_model_returns_structured_unavailable(self) -> None:
        runtime = _runtime(vision_api_key="", vision_base_url="", vision_model_name="", chat_supports_images=False)
        result = runtime.resolve_turn_execution_target(has_real_images=True)
        self.assertIsInstance(result, dict)
        self.assertEqual(result.get("status"), "unavailable")
        self.assertEqual(result.get("reason"), "multimodal_model_unavailable")
        self.assertEqual(result.get("required_modalities"), ["image"])

    def test_tool_upgrade_uses_vision_when_bundle_present(self) -> None:
        runtime = _runtime()
        target = runtime.resolve_turn_execution_target(has_real_images=False, tool_image_upgrade=True)
        self.assertIsInstance(target, ModelExecutionTarget)
        self.assertEqual(target.role, "vision")
        self.assertEqual(target.reason, "tool_image_upgrade")

    def test_tool_upgrade_unavailable_without_any_image_model(self) -> None:
        runtime = _runtime(vision_api_key="", vision_base_url="", vision_model_name="", chat_supports_images=False)
        result = runtime.resolve_turn_execution_target(has_real_images=False, tool_image_upgrade=True)
        self.assertIsInstance(result, dict)
        self.assertEqual(result.get("status"), "unavailable")

    def test_concurrent_text_and_image_turns_do_not_share_targets(self) -> None:
        runtime = _runtime()
        text_target = runtime.resolve_turn_execution_target(has_real_images=False)
        image_target = runtime.resolve_turn_execution_target(has_real_images=True)
        self.assertIsInstance(text_target, ModelExecutionTarget)
        self.assertIsInstance(image_target, ModelExecutionTarget)
        self.assertEqual(text_target.role, "chat")
        self.assertEqual(image_target.role, "vision")
        # Both are frozen, immutable snapshots of one runtime.
        self.assertEqual(text_target.to_public()["role"], "chat")
        self.assertEqual(image_target.to_public()["role"], "vision")


class ExecutionTargetThreadingTests(unittest.TestCase):
    def test_call_chat_json_result_uses_vision_bundle_when_target_present(self) -> None:
        runtime = _runtime()
        used_bundles: list[ModelBundle] = []
        runtime._call_json_result = lambda **kwargs: used_bundles.append(kwargs["bundle"]) or SimpleNamespace(
            parsed={"speech": "ok", "tool_call": None},
            raw_text="ok",
            error="",
            fallback_used=False,
            metadata_status="missing",
            metadata_present=False,
        )
        vision_target = runtime.resolve_turn_execution_target(has_real_images=True)
        runtime.call_chat_json_result(
            system_prompt="s",
            user_prompt="u",
            fallback={"speech": "f"},
            execution_target=vision_target,
        )
        self.assertEqual(len(used_bundles), 1)
        self.assertEqual(used_bundles[0].model, "gemini-2.5-flash")
        self.assertIs(used_bundles[0].client, vision_target.bundle.client)

    def test_stream_chat_json_uses_vision_bundle_when_target_present(self) -> None:
        runtime = _runtime()
        used_bundles: list[ModelBundle] = []
        runtime._stream_chat_json = lambda **kwargs: used_bundles.append(kwargs["bundle"]) or iter([])
        vision_target = runtime.resolve_turn_execution_target(has_real_images=True)
        generator = runtime.stream_chat_json(
            system_prompt="s",
            user_prompt="u",
            fallback={"speech": "f"},
            execution_target=vision_target,
        )
        self.assertEqual(len(used_bundles), 1)
        self.assertEqual(used_bundles[0].model, "gemini-2.5-flash")

    def test_text_request_payload_unchanged_by_target_routing(self) -> None:
        """The text-only path must produce an identical provider payload whether
        routed explicitly through a chat target or through the legacy path."""
        runtime = _runtime()
        bundle = ModelBundle(
            client=SimpleNamespace(_akane_protocol="openai", base_url="https://chat.example/v1"),
            model="deepseek-v4-flash",
        )
        kwargs = dict(
            system_prompt="stable persona",
            user_prompt="current text",
            temperature=0.7,
            json_mode=True,
            history_turns=[{"role": "assistant", "content": "earlier"}],
        )
        legacy_payload = runtime._build_completion_kwargs(bundle=bundle, **kwargs)
        text_target = runtime.resolve_turn_execution_target(has_real_images=False)
        routed_payload = runtime._build_completion_kwargs(bundle=text_target.bundle, **kwargs)
        self.assertEqual(legacy_payload, routed_payload)
        self.assertEqual(routed_payload["model"], "deepseek-v4-flash")

    def test_image_route_never_requests_the_text_chat_model(self) -> None:
        runtime = _runtime()
        used_models: list[str] = []
        runtime._call_json_result = lambda **kwargs: used_models.append(kwargs["bundle"].model) or SimpleNamespace(
            parsed={"speech": "ok", "tool_call": None},
            raw_text="ok",
            error="",
            fallback_used=False,
            metadata_status="missing",
            metadata_present=False,
        )
        vision_target = runtime.resolve_turn_execution_target(has_real_images=True)
        runtime.call_chat_json_result(
            system_prompt="s",
            user_prompt="u",
            fallback={"speech": "f"},
            execution_target=vision_target,
        )
        self.assertEqual(used_models, ["gemini-2.5-flash"])

    def test_vision_request_keeps_final_chat_output_limit(self) -> None:
        runtime = _runtime(chat_max_output_tokens=4096)
        target = runtime.resolve_turn_execution_target(has_real_images=True)
        payload = runtime._build_completion_kwargs(
            bundle=target.bundle,
            system_prompt="stable persona",
            user_prompt="describe this image",
            temperature=0.7,
            json_mode=True,
            user_images=[{"data_url": "data:image/png;base64,AAAA"}],
        )
        self.assertEqual(payload.get("max_tokens"), 4096)


class EngineOneWayUpgradeTests(unittest.TestCase):
    def _engine(self, *, vision_enabled: bool = True) -> AkaneMemoryEngine:
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        runtime = _runtime(vision_enabled=vision_enabled)
        engine.llm = runtime
        return engine

    @staticmethod
    def _text_result() -> SimpleNamespace:
        return SimpleNamespace(followup_context="", tool_type="read_attachment", model_image_inputs=[])

    @staticmethod
    def _image_result() -> SimpleNamespace:
        return SimpleNamespace(
            followup_context="已加载图片。",
            tool_type="load_material",
            model_image_inputs=[
                {
                    "attachment_id": "a-1",
                    "attachment_handle": "img_001",
                    "data_url": "data:image/png;base64,AAAA",
                }
            ],
        )

    def test_text_tool_result_does_not_upgrade(self) -> None:
        engine = self._engine()
        current = engine.llm.resolve_turn_execution_target(has_real_images=False)
        recomputed = engine._recompute_turn_execution_target(
            current_target=current,
            tool_results=[self._text_result()],
            chat_model_override="",
        )
        self.assertIsInstance(recomputed, ModelExecutionTarget)
        self.assertEqual(recomputed.role, "chat")

    def test_real_tool_image_upgrades_chat_to_vision(self) -> None:
        engine = self._engine()
        current = engine.llm.resolve_turn_execution_target(has_real_images=False)
        recomputed = engine._recompute_turn_execution_target(
            current_target=current,
            tool_results=[self._image_result()],
            chat_model_override="",
        )
        self.assertIsInstance(recomputed, ModelExecutionTarget)
        self.assertEqual(recomputed.role, "vision")
        self.assertEqual(recomputed.reason, "tool_image_upgrade")

    def test_vision_target_never_downgrades_on_later_text_results(self) -> None:
        engine = self._engine()
        current = engine.llm.resolve_turn_execution_target(has_real_images=True)
        self.assertEqual(current.role, "vision")
        recomputed = engine._recompute_turn_execution_target(
            current_target=current,
            tool_results=[self._text_result()],
            chat_model_override="",
        )
        self.assertIs(recomputed, current)
        self.assertEqual(recomputed.role, "vision")

    def test_tool_image_without_any_image_model_returns_structured_unavailable(self) -> None:
        engine = self._engine(vision_enabled=False)
        runtime = engine.llm
        runtime.vision = None
        runtime.chat_supports_images = lambda: False
        current = runtime.resolve_turn_execution_target(has_real_images=False)
        recomputed = engine._recompute_turn_execution_target(
            current_target=current,
            tool_results=[self._image_result()],
            chat_model_override="",
        )
        self.assertIsInstance(recomputed, dict)
        self.assertEqual(recomputed.get("status"), "unavailable")

    def test_tool_history_projection_uses_next_target_protocol(self) -> None:
        class ProjectionManager:
            def __init__(self) -> None:
                self.profiles: list[str] = []

            def build_context_projection(self, **kwargs):
                self.profiles.append(str(kwargs.get("provider_profile") or ""))
                return {
                    "ok": True,
                    "provider_profile": str(kwargs.get("provider_profile") or ""),
                    "messages": [
                        {
                            "turn_id": "",
                            "source_ids": ["tool-action"],
                            "payload": {
                                "role": "assistant",
                                "tool_calls": [
                                    {
                                        "id": "call-1",
                                        "type": "function",
                                        "function": {"name": "load_material", "arguments": "{}"},
                                    }
                                ],
                            },
                        },
                        {
                            "turn_id": "",
                            "source_ids": ["tool-observation"],
                            "payload": {
                                "role": "tool",
                                "tool_call_id": "call-1",
                                "content": "loaded",
                            },
                        },
                    ],
                }

        engine = self._engine()
        manager = ProjectionManager()
        engine.memcore_manager = manager
        turns: list[dict] = []
        result = engine._append_tool_history_batch(
            tool_history_turns=turns,
            items=[
                (
                    {"type": "load_material", "_tool_source": "native_anthropic"},
                    self._image_result(),
                    "loaded",
                    "",
                )
            ],
            trace_source_ids=["tool-action", "tool-observation"],
            provider_profile="gemini",
            profile_user_id="u",
            session_id="s",
            character_pack_id="c",
        )
        self.assertTrue(result.get("ok"))
        self.assertEqual(manager.profiles, ["gemini"])
        self.assertEqual(turns[0].get("role"), "assistant")
        self.assertEqual(turns[1].get("role"), "tool")


class EngineUnavailableShortCircuitTests(unittest.TestCase):
    def test_build_final_response_short_circuits_on_unavailable_target(self) -> None:
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        result = engine._build_final_response(
            session_id="s",
            profile_user_id="u",
            user_message="看看这张图",
            recent_raw=[],
            recent_episodic_summaries=[],
            recent_semantic_summaries=[],
            confirmed_snippets=[],
            now_ts=1,
            user_images=[{"data_url": "data:image/png;base64,AAAA"}],
            execution_target={"status": "unavailable", "reason": "multimodal_model_unavailable"},
        )
        self.assertEqual(result.get("speech", "").strip(), "我这边暂时没有可用的识图模型，直接看不了这张图。你可以先用文字描述一下，或者等识图模型配置好后再发一次。")
        self.assertIsNotNone(result.get("_multimodal_unavailable"))
        self.assertEqual(result["_multimodal_unavailable"]["status"], "unavailable")
        self.assertEqual(result["_multimodal_unavailable"]["reason"], "multimodal_model_unavailable")
        self.assertEqual(result.get("tool_call"), None)

    def test_stream_final_response_short_circuits_on_unavailable_target(self) -> None:
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        generator = engine._stream_final_response(
            session_id="s",
            profile_user_id="u",
            user_message="看看这张图",
            recent_raw=[],
            recent_episodic_summaries=[],
            recent_semantic_summaries=[],
            confirmed_snippets=[],
            now_ts=1,
            user_images=[{"data_url": "data:image/png;base64,AAAA"}],
            execution_target={"status": "unavailable", "reason": "multimodal_model_unavailable"},
        )
        result = None
        try:
            while True:
                next(generator)
        except StopIteration as stop:
            result = stop.value
        self.assertIsNotNone(result)
        self.assertEqual(result.get("tool_call"), None)
        self.assertIn("_multimodal_unavailable", result)

    def test_unavailable_reason_stays_out_of_user_speech(self) -> None:
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        result = engine._multimodal_unavailable_output("multimodal_model_unavailable")
        self.assertNotIn("multimodal_model_unavailable", result.get("speech", ""))
        self.assertIn("识图", result.get("speech", ""))


if __name__ == "__main__":
    unittest.main()
