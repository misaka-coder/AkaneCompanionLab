"""Real prompt/provider boundaries across tools, control changes and compaction."""

from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import json
import unittest
from unittest.mock import patch

import config
from companion_v01.client_protocol import ClientMode, ClientProtocolContext, default_capabilities_for_mode
from companion_v01.desktop_pet_character_resources import DesktopPetCharacterResourceService
from companion_v01.engine import AkaneMemoryEngine
from companion_v01.engine_services import response_builder
from companion_v01.memcore_integration.history_notice import build_history_projection_notice
from companion_v01.engine_services.tool_rounds import resolve_capability_selection
from companion_v01.llm_runtime import LLMRuntime
from companion_v01.persona_config import load_persona_config
from companion_v01.prompt_builder import PromptBuilder
from companion_v01.prompt_profiles import PromptProfileRegistry
from tests.test_capability_exposure import CountingTool
from tests.test_memcore_integration import _PromptContextEngine, _PromptContextMemcoreManager


def _record(message, source_id):
    return {"payload": message, "source_ids": [source_id], "turn_id": "active"}


def _tool_pair(number, protocol="openai"):
    if protocol == "anthropic":
        return [
            {"role": "assistant", "content": [{"type": "tool_use", "id": f"call-{number}",
                "name": "demo_alpha", "input": {"step": number}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": f"call-{number}",
                "content": f"result-{number}"}]},
        ]
    return [
        {"role": "assistant", "tool_calls": [{"id": f"call-{number}", "type": "function",
            "function": {"name": "demo_alpha", "arguments": json.dumps({"step": number})}}]},
        {"role": "tool", "tool_call_id": f"call-{number}", "content": f"result-{number}"},
    ]


class _SnapshotEngine(_PromptContextEngine, AkaneMemoryEngine):
    def _resolve_capability_selection(self, **kwargs):
        kwargs.pop("client_context", None)
        return resolve_capability_selection(self, client_context=None, **kwargs)

    def _get_prompt_profile_registry(self):
        return PromptProfileRegistry()

    def _build_current_visual_context(self, **_kwargs):
        return self.dynamic["visual"]

    def _build_tool_prompt_context_sections(self, **_kwargs):
        return {"execution_context": self.dynamic["cwd"], "execution_stable_context": "stable host rules",
            "catalog_status": "skipped", "catalog_context": "", "round_context": ""}

    def _build_desktop_pet_character_pack_prompt_context(self, **_kwargs):
        return {"system_context": self.dynamic["persona"], "reference_context": "", "active_id": "cecilia"}


class ToolTurnPromptSnapshotTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        character = root / "cecilia" / "character.json"
        character.parent.mkdir()
        character.write_text(json.dumps({"voice": {"provider": "irodori"},
            "speech_output": {"enabled": True, "language": "日语"}}), encoding="utf-8")
        self.history_notice = build_history_projection_notice({"language": "日语", "style_mode": "description"})
        self.manager = _PromptContextMemcoreManager({}, projection_payload={
            "ok": True, "provider_profile": "openai_chat", "compaction_generation": 1,
            "messages": [
                _record({"role": "assistant", "content": '{"utterances":[{"text":"旧话","say":"[hidden]"}]}'}, "old"),
                _record({"role": "user", "content": self.history_notice}, "history-notice"),
                _record({"role": "user", "content": "帮我查资料"}, "current"),
            ],
        })
        self.engine = _SnapshotEngine(memcore_manager=self.manager)
        self.engine.capability_config_base_dir = root
        self.engine.tool_handlers = {"demo.alpha": CountingTool("demo.alpha")}
        self.engine.llm.chat_supports_native_tools = lambda **_kwargs: True
        self.engine.desktop_pet_character_resources = DesktopPetCharacterResourceService(characters_dir=root)
        self.dynamic = self.engine.dynamic = {"visual": "emotion: normal", "cwd": "workspace: first",
            "persona": "energy: 80", "observation": "hunger: 20"}
        self.blocks = ["Stable plugin rule"]
        self.engine.prompt_builder = PromptBuilder(load_persona_config(),
            stable_system_blocks_provider=lambda: tuple(self.blocks))
        self.observation_calls = 0

        def observe(**_kwargs):
            self.observation_calls += 1
            return self.dynamic["observation"]

        self.engine.plugin_observations_provider = observe
        self.state = {}
        for key, value in {"MEMORY_BACKEND": "memcore", "LLM_AUTO_COMPACT_TOKEN_LIMIT": 0}.items():
            patcher = patch.object(config, key, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def prepare(self, **overrides):
        mode = overrides.pop("mode", ClientMode.DESKTOP_PET)
        values = dict(session_id="session", profile_user_id="owner", character_pack_id="cecilia",
            user_message="帮我查资料", current_user_source_id="current", recent_raw=[],
            recent_episodic_summaries=[], recent_semantic_summaries=[], confirmed_snippets=[], now_ts=100,
            enable_native_tools=True, request_projection_state=self.state,
            client_context=ClientProtocolContext(requested_mode=mode, effective_mode=mode,
                capabilities=(*default_capabilities_for_mode(mode), "speech_units_v1")))
        values.update(overrides)
        result = response_builder.prepare_context(self.engine, **values)
        self.assertNotIn("memcore_projection_failure", result)
        return result

    def add_tools(self, turns):
        self.manager.projection_payload["messages"].extend(
            _record(deepcopy(turn), f"tool-{len(self.manager.projection_payload['messages'])}-{index}")
            for index, turn in enumerate(turns)
        )

    def wire(self, context, protocol, stream=False):
        runtime = LLMRuntime.__new__(LLMRuntime)
        bundle = SimpleNamespace(client=SimpleNamespace(_akane_protocol=protocol, base_url="https://example.test/v1"), model="test")
        payload = runtime._build_completion_kwargs(bundle=bundle, temperature=0.1, stream=stream,
            **{key: context[key] for key in ("system_prompt", "user_prompt", "history_turns", "ephemeral_turns", "post_user_turns")},
            native_tools=context["native_tools"], native_tool_choice=context["native_tool_choice"],
            system_extra_blocks=context["system_extra_blocks"])
        persistent = runtime._persistent_turn_messages_from_payload(payload=payload, bundle=bundle,
            **{key: context[key] for key in ("history_turns", "ephemeral_turns", "post_user_turns")})
        return payload["messages"], persistent

    def test_normal_tools_keep_exact_input_and_output_prefix_in_all_wire_modes(self):
        for mode in (ClientMode.DESKTOP_PET, ClientMode.QQ_TEXT):
            for protocol in ("openai", "gemini", "responses", "anthropic"):
                with self.subTest(mode=mode, protocol=protocol):
                    self.state = {}
                    self.engine.llm.chat_provider_protocol = lambda **_kwargs: protocol
                    self.manager.projection_payload["provider_profile"] = "anthropic_messages" if protocol == "anthropic" else "openai_chat"
                    self.manager.projection_payload["messages"] = self.manager.projection_payload["messages"][:3]
                    contexts = [self.prepare(mode=mode)]
                    self.assertEqual(contexts[0]["native_tool_choice"], "auto")
                    self.assertTrue(contexts[0]["native_tools"])
                    for number in (1, 2):
                        self.dynamic.update(visual=f"emotion: changed-{number}", cwd=f"workspace: new-{number}",
                            persona=f"energy: {number}", observation=f"hunger: {number}")
                        self.add_tools(_tool_pair(number, protocol))
                        contexts.append(self.prepare(mode=mode, post_user_turns=[turn for n in range(1, number + 1) for turn in _tool_pair(n, protocol)]))
                    for stream in (False, True):
                        messages = [self.wire(context, protocol, stream)[0] for context in contexts]
                        for wire_messages in messages:
                            self.assertEqual(sum(message.get("content") == self.history_notice
                                for message in wire_messages), 1)
                        self.assertFalse(any(self.history_notice in str(context["ephemeral_turns"])
                            for context in contexts))
                        for index in (0, 1):
                            self.assertEqual(messages[index + 1][:len(messages[index])], messages[index])
                            self.assertEqual(messages[index + 1][len(messages[index])], _tool_pair(index + 1, protocol)[0])
                        self.assertFalse(any(section.get("name") in
                            {"user.speech_format_tail", "user.final_reply_format_reminder"}
                            for section in contexts[-1].get("prompt_audit_sections", [])))
                        self.assertEqual(messages[-1][-1], _tool_pair(2, protocol)[-1])
                        _, persistent = self.wire(contexts[-1], protocol, stream)
                        self.assertEqual(persistent, [{"role": "user", "content": "帮我查资料"}, *_tool_pair(1, protocol), *_tool_pair(2, protocol)])
                    self.assertEqual(contexts[0]["ephemeral_turns"], contexts[-1]["ephemeral_turns"])
                    self.assertEqual(contexts[0]["history_turns"], contexts[-1]["history_turns"])
        self.assertEqual(self.observation_calls, 8)

    def test_new_task_and_control_changes_refresh_without_cross_task_state(self):
        first = self.prepare()
        self.dynamic["observation"] = "fresh status"
        self.assertNotIn("fresh status", str(self.prepare()["ephemeral_turns"]))
        other_state = {}
        other = self.prepare(request_projection_state=other_state, session_id="other")
        self.assertIn("fresh status", str(other["ephemeral_turns"]))
        self.assertEqual(first["ephemeral_turns"], self.prepare()["ephemeral_turns"])
        self.assertIn("stop now", str(self.prepare(extra_user_context="stop now", allow_tool_call=False)["ephemeral_turns"]))
        self.assertEqual(self.prepare(extra_user_context="stop now", allow_tool_call=False)["native_tool_choice"], "none")
        self.assertIn("fresh status", str(self.prepare(request_projection_state={})["ephemeral_turns"]))

    def test_snapshot_does_not_mask_projection_failures_or_rule_revocation(self):
        self.prepare()
        self.blocks.clear()
        self.dynamic["observation"] = "after revoke"
        revoked = self.prepare()
        self.assertNotIn("Stable plugin rule", revoked["system_extra_blocks"])
        self.assertIn("after revoke", str(revoked["ephemeral_turns"]))
        self.manager.projection_payload = {"ok": False, "reason": "unavailable"}
        with patch.object(config, "MEMCORE_SHADOW_COMPARE", False):
            result = response_builder.prepare_context(self.engine, session_id="session", profile_user_id="owner",
                character_pack_id="cecilia", user_message="帮我查资料", recent_raw=[], recent_episodic_summaries=[],
                recent_semantic_summaries=[], confirmed_snippets=[], now_ts=100, request_projection_state=self.state)
        self.assertIn("memcore_projection_failure", result)

    def test_budget_compaction_refreshes_snapshot_and_keeps_tool_results(self):
        self.prepare()
        self.add_tools(_tool_pair(1))
        self.manager.projection_payload["messages"].insert(0, _record({"role": "user", "content": "long history " * 2000}, "long"))

        def compact(**_kwargs):
            self.dynamic["observation"] = "after compaction"
            self.dynamic["cwd"] = "workspace: after compaction"
            self.manager.projection_payload["compaction_generation"] = 2
            self.manager.projection_payload["messages"] = [record for record in self.manager.projection_payload["messages"]
                if record["source_ids"] not in (["long"], ["old"])]

        self.manager.compact_due_sync = compact
        with patch.object(config, "LLM_AUTO_COMPACT_TOKEN_LIMIT", 4000):
            compacted = self.prepare(post_user_turns=_tool_pair(1))
        self.assertTrue(compacted["prompt_budget"]["compact_attempted"])
        self.assertIn("after compaction", str(compacted["ephemeral_turns"]))
        self.assertEqual(compacted["post_user_turns"], _tool_pair(1))
        self.dynamic["observation"] = "later change"
        self.assertNotIn("later change", str(self.prepare(post_user_turns=_tool_pair(1))["ephemeral_turns"]))


if __name__ == "__main__":
    unittest.main()
