from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from companion_v01.persona_config import load_persona_config
from companion_v01.prompt_builder import PromptBuilder


class PersonaConfigTomlTests(unittest.TestCase):
    def test_load_persona_config_supports_custom_variant_from_toml(self) -> None:
        toml_text = """
[variants.custom.meta]
assistant_name = "AkaneCustom"
user_label = "Master"
trace_prefix = "custom_trace"
surprise_memory_reason = "custom surprise"

[variants.custom.router]
system = "router system"
fast_mode = "router fast"
debug_mode = "router debug"

[variants.custom.verifier]
system = "verifier system"
fast_mode = "verifier fast"
debug_mode = "verifier debug"

[variants.custom.final]
system = "final system"
fast_mode = "final fast"
debug_mode = "final debug"
fallback_thought = "fallback thought"
fallback_speech = "fallback speech"
user_prompt_suffix = "final suffix"

[variants.custom.summary]
fallback_diary_template = "summary {tags}"
system = "summary system"

[variants.custom.semantic_summary]
fallback_template = "semantic {tags}"
system = "semantic summary system"

[variants.custom.semantic_reinforcement]
system = "semantic reinforcement system"
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            toml_path = Path(temp_dir) / "persona_profiles.toml"
            toml_path.write_text(toml_text, encoding="utf-8")

            persona = load_persona_config(path=toml_path, variant="custom")

            self.assertEqual(persona.assistant_name, "AkaneCustom")
            self.assertEqual(persona.user_label, "Master")
            self.assertEqual(persona.router_system_prompt, "router system")
            self.assertEqual(persona.summary_system_prompt, "summary system")
            self.assertEqual(persona.semantic_reinforcement_system_prompt, "semantic reinforcement system")


class PromptBuilderTests(unittest.TestCase):
    def test_build_router_prompts_explains_local_window_and_search_query_style(self) -> None:
        builder = PromptBuilder(load_persona_config())

        system_prompt, user_prompt = builder.build_router_prompts(
            now_ts=1712400000,
            recent_context_text="[2026-04-10 20:00] assistant: 刚才说到集市那件事啦",
            current_message_text="[2026-04-10 20:01] user: 是呀",
            debug_enabled=False,
        )

        self.assertIn("rewritten_query", system_prompt)
        self.assertIn("index_current_message", system_prompt)
        self.assertIn("仅包含紧邻当前消息之前的局部窗口", user_prompt)
        self.assertIn("向过去要事实", user_prompt)
        self.assertIn("问昨天买了什么、之前去过哪里、上次说过什么，通常 need_retrieval=true", user_prompt)
        self.assertIn("接当前话题不等于一定不检索", user_prompt)
        self.assertIn("我也记不清了，反正有几个扬州城地点，你再想想", user_prompt)
        self.assertIn("那个/那几个/那件事/那个地方/那个项目", user_prompt)
        self.assertIn("优先选择 memory_search", user_prompt)
        self.assertIn("4月12日晚上那件事", user_prompt)
        self.assertIn("YYYY-MM-DD 晚上 发生了什么", user_prompt)
        self.assertIn("改写时必须先看最近上下文", user_prompt)
        self.assertIn("我对这个有执念", user_prompt)
        self.assertIn("扬州城地点/二十四桥/瘦西湖", user_prompt)
        self.assertIn("主人对什么有执念", user_prompt)
        self.assertIn("共同经历、曾经说过/聊过/约定过的内容", user_prompt)
        self.assertIn("以前、曾经、过去、当时、那时候", user_prompt)
        self.assertIn("我昨天没睡好", user_prompt)
        self.assertIn("不要把“最近窗口里有没有完整答案”当成标准；如果当前问题需要更早历史事实，应该检索", user_prompt)
        self.assertIn("不要写成“请查找……”这类任务描述", user_prompt)
        self.assertIn("index_current_message=true", user_prompt)

    def test_build_verifier_prompts_mentions_selection_event_and_numbered_snippets(self) -> None:
        builder = PromptBuilder(load_persona_config())

        system_prompt, user_prompt = builder.build_verifier_prompts(
            now_ts=1712400000,
            original_query="我之前说了什么",
            rewritten_query="之前 说过",
            keywords_json='["之前","说过"]',
            time_hint_json='{"date_label":"2026-04-10","relative_time":"yesterday"}',
            snippets_text="[1]\n片段A\n\n[2]\n片段B",
            debug_enabled=False,
        )

        self.assertIn("selection", system_prompt)
        self.assertIn("编号从 1 开始", user_prompt)
        self.assertIn("若 match=true 再输出第二行 selection", user_prompt)
        self.assertIn("路由时间线索", user_prompt)
        self.assertIn("时间明显冲突", user_prompt)
        self.assertIn("retry_query 必须是更具体的搜索短句", user_prompt)
        self.assertIn("请回忆一下具体的事情或话题", user_prompt)
        self.assertIn("主人对什么有执念", user_prompt)

    def test_build_final_generation_context_uses_persona_and_debug_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            toml_path = Path(temp_dir) / "persona_profiles.toml"
            toml_path.write_text(
                """
[variants.custom.meta]
assistant_name = "AkaneCustom"
user_label = "Master"
trace_prefix = "custom_trace"
surprise_memory_reason = "custom surprise"

[variants.custom.router]
system = "router system"
fast_mode = "router fast"
debug_mode = "router debug"

[variants.custom.verifier]
system = "verifier system"
fast_mode = "verifier fast"
debug_mode = "verifier debug"

[variants.custom.final]
system = "final system "
fast_mode = "fast mode "
debug_mode = "debug mode "
fallback_thought = "fallback thought"
fallback_speech = "fallback speech"
user_prompt_suffix = "suffix"

[variants.custom.summary]
fallback_diary_template = "summary {tags}"
system = "summary system"

[variants.custom.semantic_summary]
fallback_template = "semantic {tags}"
system = "semantic summary system"

[variants.custom.semantic_reinforcement]
system = "semantic reinforcement system"
""",
                encoding="utf-8",
            )
            persona = load_persona_config(path=toml_path, variant="custom")
            builder = PromptBuilder(persona)

            result = builder.build_final_generation_context(
                now_ts=1712400000,
                raw_text="User: hi",
                current_message_text="User: hi",
                episodic_summary_text="episode",
                semantic_summary_text="semantic",
                memory_text="memory",
                current_visual_context="visual",
                resource_context="resource",
                extra_context="extra",
                visual_defaults={
                    "major": "home",
                    "minor": "room",
                    "background": "morning",
                    "bgm": "bgm",
                    "outfit": "default",
                    "emotion": "normal",
                },
                allow_tool_call=True,
                tool_prompt_context="\n- fake tool",
                debug_enabled=True,
                persona_system_context="persona state",
                persona_reference_context="persona refs",
                persona_active_id="current_card",
            )

            self.assertTrue(result["debug_enabled"])
            self.assertEqual(result["fallback"]["thought"], "fallback thought")
            self.assertEqual(result["fallback"]["speech_segments"], [])
            self.assertEqual(result["fallback"]["code_snippet"], "")
            self.assertEqual(result["fallback"]["persona"]["active"], "current_card")
            self.assertIn("final system", result["system_prompt"])
            self.assertIn("debug mode", result["system_prompt"])
            self.assertIn("- fake tool", result["system_prompt"])
            self.assertIn("[CURRENT ASSISTANT STATE - EMBODY THIS]", result["system_prompt"])
            self.assertIn("persona state", result["system_prompt"])
            self.assertLess(result["system_prompt"].index("debug mode"), result["system_prompt"].index("persona state"))
            self.assertLess(result["system_prompt"].index("- fake tool"), result["system_prompt"].index("persona state"))
            self.assertIn("较长期的语义记忆", result["user_prompt"])
            self.assertIn("extra", result["user_prompt"])
            self.assertIn("persona refs", result["user_prompt"])

    def test_final_output_schema_places_tool_call_after_speech_segments(self) -> None:
        persona = load_persona_config()
        builder = PromptBuilder(persona)

        self.assertIn("字段固定为 emotion, speech, speech_segments, tool_call", persona.final_fast_mode_prompt)
        self.assertIn('"speech":"我在哦，欢迎回来。","speech_segments":[],"tool_call":null', persona.final_fast_mode_prompt)
        self.assertIn("字段固定为 thought, emotion, speech, speech_segments, tool_call", persona.final_debug_mode_prompt)
        self.assertIn('"speech":"我在哦，欢迎回来。","speech_segments":[],"tool_call":null', persona.final_debug_mode_prompt)
        self.assertIn("tool_call 必须放在 speech_segments 字段之后", persona.final_system_prompt)

        result = builder.build_final_generation_context(
            now_ts=1712400000,
            raw_text="User: hi",
            current_message_text="User: hi",
            episodic_summary_text="",
            semantic_summary_text="",
            memory_text="",
            current_visual_context="",
            resource_context="",
            extra_context="",
            visual_defaults={
                "major": "home",
                "minor": "room",
                "background": "morning",
                "bgm": "bgm",
                "outfit": "default",
                "emotion": "normal",
            },
            allow_tool_call=True,
            tool_prompt_context="",
            debug_enabled=False,
        )
        fallback_keys = list(result["fallback"].keys())
        self.assertLess(fallback_keys.index("speech_segments"), fallback_keys.index("tool_call"))
        self.assertEqual(fallback_keys[:4], ["emotion", "speech", "speech_segments", "tool_call"])

    def test_default_prompts_do_not_force_akane_identity(self) -> None:
        persona = load_persona_config()

        self.assertIn("[CURRENT ASSISTANT STATE - EMBODY THIS]", persona.final_system_prompt)
        self.assertNotIn("[AKANE CURRENT STATE - EMBODY THIS]", persona.final_system_prompt)
        self.assertIn("当前前台角色", persona.final_user_prompt_suffix)
        self.assertNotIn("以 Akane 的身份", persona.final_user_prompt_suffix)


if __name__ == "__main__":
    unittest.main()
