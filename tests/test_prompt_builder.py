from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from companion_v01.persona_config import load_persona_config
from companion_v01.prompt_blocks import (
    build_desktop_pet_system_prompt,
    build_qq_text_system_prompt,
    strip_care_prompt_contract,
)
from companion_v01.prompt_builder import PromptBuilder, TOOL_CONTEXT_STABLE_RULES
from companion_v01.prompt_profiles import PromptProfileRegistry
from companion_v01.client_protocol import ClientMode


def _history_text(result: dict) -> str:
    return "\n".join(
        str(turn.get("content") or "") for turn in result.get("history_turns") or [] if isinstance(turn, dict)
    )


def _ephemeral_text(result: dict) -> str:
    return "\n".join(
        str(turn.get("content") or "") for turn in result.get("ephemeral_turns") or [] if isinstance(turn, dict)
    )


def _provider_text(result: dict) -> str:
    return "\n".join(
        part
        for part in [_history_text(result), str(result.get("user_prompt") or ""), _ephemeral_text(result)]
        if part
    )


def _provider_turns(result: dict) -> list[dict]:
    turns = [dict(turn) for turn in result.get("history_turns") or [] if isinstance(turn, dict)]
    turns.append({"role": "user", "content": str(result.get("user_prompt") or "")})
    turns.extend(dict(turn) for turn in result.get("ephemeral_turns") or [] if isinstance(turn, dict))
    return turns


def _build_minimal_final(
    builder: PromptBuilder,
    *,
    current_message_text: str = "User: hello",
    raw_text: str = "User: hello",
    **overrides: object,
) -> dict:
    values = {
        "now_ts": 1_712_400_000,
        "raw_text": raw_text,
        "current_message_text": current_message_text,
        "episodic_summary_text": "",
        "semantic_summary_text": "",
        "memory_text": "",
        "current_visual_context": "",
        "resource_context": "",
        "extra_context": "",
        "visual_defaults": {
            "major": "home",
            "minor": "room",
            "background": "morning",
            "bgm": "",
            "outfit": "default",
            "emotion": "normal",
        },
        "allow_tool_call": True,
        "tool_prompt_context": "tools",
        "debug_enabled": False,
    }
    values.update(overrides)
    return builder.build_final_generation_context(**values)  # type: ignore[arg-type]


class PersonaConfigTomlTests(unittest.TestCase):
    def test_stable_tool_rules_distinguish_parallel_native_from_single_legacy_field(self) -> None:
        self.assertIn("多个互不依赖的工具", TOOL_CONTEXT_STABLE_RULES)
        self.assertIn("JSON `tool_call` 字段每轮仍只容纳一个", TOOL_CONTEXT_STABLE_RULES)

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
        self.assertIn("生日、重要日期、偏好、称呼、旧约定", user_prompt)
        self.assertIn("当前助手自己的深层记忆空间", user_prompt)
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
        self.assertIn("信息量很低的纯追问可以设为 false", user_prompt)
        self.assertIn("不影响是否需要检索", user_prompt)

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

    def test_memory_summary_prompts_can_include_persona_perspective_without_fact_pollution(self) -> None:
        builder = PromptBuilder(load_persona_config())

        summary_system, _ = builder.build_summary_prompts(
            transcript="User: 我喜欢喝可乐。",
            batch_size=1,
            persona_system_context="角色设定：Mika 会认真记住主人的偏好。",
            persona_reference_context="表达侧面：温柔吐槽。",
        )
        semantic_system, _ = builder.build_semantic_summary_prompts(
            source_text="主人提到自己喜欢喝可乐。",
            persona_system_context="角色设定：Mika 会认真记住主人的偏好。",
        )
        reinforcement_system, _ = builder.build_semantic_reinforcement_prompts(
            existing_text="已有长期记忆",
            incoming_text="新的摘要",
            persona_system_context="角色设定：Mika 会认真记住主人的偏好。",
        )

        for prompt in (summary_system, semantic_system, reinforcement_system):
            self.assertIn("[CURRENT CHARACTER MEMORY SELF]", prompt)
            self.assertIn("你此刻的角色身份与表达侧面", prompt)
            self.assertIn("整理记忆时就按这个身份记", prompt)
            self.assertIn("不是这段对话发生过的事实", prompt)
            self.assertIn("[MEMORY TIME ANCHOR RULES]", prompt)
            self.assertIn("相对 YYYY-MM-DD 的", prompt)
            self.assertIn("不要留下未锚定的相对时间", prompt)
            self.assertIn("Mika", prompt)
        self.assertIn("温柔吐槽", summary_system)

    def test_build_summary_prompts_can_include_reference_summaries_for_consistency(self) -> None:
        builder = PromptBuilder(load_persona_config())

        _, user_prompt = builder.build_summary_prompts(
            transcript="[日期 2026-04-11]\n[20:00] User: 我明天继续复习。",
            batch_size=1,
            reference_summary_text="[2026-04-10 20:00 ~ 20:20] 摘要: 用户在推进复习计划。",
        )

        self.assertIn("可参考的既有阶段摘要", user_prompt)
        self.assertIn("人物关系、项目脉络、时间线和记忆口吻一致", user_prompt)
        self.assertIn("不要把参考摘要里出现、但本段原始对话没有出现的内容写成这段的新事实", user_prompt)
        self.assertIn("用户在推进复习计划", user_prompt)

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
                history_turns=[{"role": "user", "content": "User: hi"}],
                current_message_text="User: current",
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
                extra_context_audit_sections=[
                    {"name": "relationship", "text": "extra"},
                    {"name": "user.extra_context.turn_extra_context", "text": "turn"},
                    {"name": "", "text": "ignored"},
                    {"name": "empty", "text": ""},
                ],
            )

            self.assertTrue(result["debug_enabled"])
            self.assertEqual(result["fallback"]["thought"], "fallback thought")
            self.assertEqual(result["fallback"]["speech_segments"], [])
            self.assertEqual(result["fallback"]["code_snippet"], "")
            self.assertEqual(result["fallback"]["persona"]["active"], "current_card")
            self.assertIn("final system", result["system_prompt"])
            self.assertIn("debug mode", result["system_prompt"])
            self.assertIn("历史记忆与当前任务边界", result["system_prompt"])
            self.assertIn("历史状态不是当前待办", result["system_prompt"])
            self.assertNotIn("- fake tool", result["system_prompt"])
            self.assertIn("本轮能力与工具上下文边界", result["system_prompt"])
            provider_text = _provider_text(result)
            history_text = _history_text(result)
            self.assertIn("- fake tool", history_text)
            self.assertNotIn("[CURRENT ASSISTANT STATE - EMBODY THIS]", result["system_prompt"])
            self.assertNotIn("persona state", result["system_prompt"])
            self.assertIn("persona state", history_text)
            self.assertNotIn("persona state", result["user_prompt"])
            self.assertEqual(
                result["system_extra_blocks"],
                [
                    "可用视觉资源：\nresource",
                ],
            )
            self.assertIn("较长期的语义记忆（最多3条）：\nsemantic", history_text)
            self.assertIn("最近可见的阶段摘要（5~10条弹性窗口）：\nepisode", history_text)
            self.assertNotIn("可用视觉资源", result["user_prompt"])
            self.assertIn("记忆情绪", history_text)
            self.assertIn("情感余温", history_text)
            self.assertIn("不要把它当作用户事实", history_text)
            self.assertIn("回应时自然带着这份余温即可", history_text)
            self.assertLess(
                provider_text.index("extra"),
                provider_text.index("较长期的语义记忆"),
            )
            self.assertLess(
                provider_text.index("extra"),
                provider_text.index("最近可见的阶段摘要"),
            )
            self.assertLess(
                provider_text.index("- fake tool"),
                provider_text.index("extra"),
            )
            self.assertIn("extra", history_text)
            self.assertIn("persona refs", history_text)
            self.assertNotIn("extra", result["user_prompt"])
            self.assertNotIn("persona refs", result["user_prompt"])
            self.assertLess(provider_text.index("User: hi"), provider_text.index("当前演出状态"))
            audit_names = [section["name"] for section in result["prompt_audit_sections"]]
            self.assertIn("system.full", audit_names)
            self.assertIn("system_extra.resource_context", audit_names)
            self.assertIn("user.semantic_memory", audit_names)
            self.assertIn("user.episodic_summary", audit_names)
            self.assertIn("user.memory_context", audit_names)
            self.assertIn("user.ephemeral_context", audit_names)
            self.assertIn("user.raw_recent_timeline", audit_names)
            self.assertIn("user.retrieval_snippets", audit_names)
            self.assertIn("user.current_visual_context", audit_names)
            self.assertIn("user.stable_context", audit_names)
            self.assertIn("user.tool_context", audit_names)
            self.assertIn("user.persona_state", audit_names)
            self.assertIn("user.current_message", audit_names)
            self.assertIn("user.extra_context.relationship", audit_names)
            self.assertIn("user.extra_context.turn_extra_context", audit_names)
            self.assertNotIn("user.extra_context.empty", audit_names)

    def test_dynamic_tool_state_does_not_change_stable_system_prefix(self) -> None:
        persona = load_persona_config()
        builder = PromptBuilder(persona)
        common = {
            "now_ts": 1_712_400_000,
            "raw_text": "User: 请处理这个文件",
            "current_message_text": "User: 请处理这个文件",
            "episodic_summary_text": "",
            "semantic_summary_text": "",
            "memory_text": "",
            "current_visual_context": "",
            "resource_context": "",
            "extra_context": "",
            "visual_defaults": {
                "major": "home",
                "minor": "room",
                "background": "morning",
                "bgm": "",
                "outfit": "default",
                "emotion": "normal",
            },
            "allow_tool_call": True,
            "debug_enabled": False,
        }

        latent = builder.build_final_generation_context(
            **common,
            tool_prompt_context="【可按需激活的能力】\n- 上传音频后启用媒体处理。",
        )
        ready = builder.build_final_generation_context(
            **common,
            tool_prompt_context="【当前可用能力概览】\n- 已启用媒体处理。\n- transcribe_media：测试 schema。",
        )

        self.assertEqual(latent["system_prompt"], ready["system_prompt"])
        self.assertEqual(latent["user_prompt"], ready["user_prompt"])
        self.assertNotEqual(latent["history_turns"], ready["history_turns"])
        self.assertNotIn("上传音频后启用媒体处理", latent["system_prompt"])
        self.assertIn("【本轮系统能力与工具上下文】", _history_text(latent))
        self.assertEqual(latent["user_prompt"], "User: 请处理这个文件")
        self.assertNotEqual(latent["tool_prompt_context_hash"], ready["tool_prompt_context_hash"])

    def test_runtime_context_precedes_append_only_history_without_entering_current_tail(self) -> None:
        builder = PromptBuilder(load_persona_config())
        common = {
            "now_ts": 1_712_400_000,
            "episodic_summary_text": "stable episode",
            "semantic_summary_text": "stable semantic",
            "memory_text": "dynamic retrieval",
            "current_visual_context": "dynamic visual",
            "resource_context": "stable resources",
            "extra_context": "dynamic extra",
            "visual_defaults": {
                "major": "home",
                "minor": "room",
                "background": "morning",
                "bgm": "",
                "outfit": "default",
                "emotion": "normal",
            },
            "allow_tool_call": True,
            "tool_prompt_context": "stable tool contract",
            "debug_enabled": False,
            "persona_reference_context": "dynamic reference",
        }
        first = builder.build_final_generation_context(
            **common,
            raw_text="User: first event",
            history_turns=[{"role": "user", "content": "User: first event"}],
            current_message_text="User: current A",
            persona_system_context="persona state A",
        )
        second = builder.build_final_generation_context(
            **common,
            raw_text="User: first event\nAssistant: first answer",
            history_turns=[
                {"role": "user", "content": "User: first event"},
                {"role": "assistant", "content": "Assistant: first answer"},
            ],
            current_message_text="User: current B",
            persona_system_context="persona state A",
        )

        self.assertEqual(first["system_prompt"], second["system_prompt"])
        self.assertNotIn("persona state A", first["system_prompt"])
        first_history = _history_text(first)
        second_history = _history_text(second)
        self.assertLess(first_history.index("stable tool contract"), first_history.index("User: first event"))
        self.assertIn("persona state A", first_history)
        self.assertIn("dynamic extra", first_history)
        self.assertIn("dynamic reference", first_history)
        self.assertNotIn("persona state A", first["user_prompt"])
        self.assertNotIn("dynamic extra", first["user_prompt"])
        self.assertNotIn("dynamic reference", first["user_prompt"])
        self.assertEqual(first["user_prompt"], "User: current A")
        self.assertIn("dynamic retrieval", _ephemeral_text(first))
        self.assertIn("dynamic visual", _ephemeral_text(first))
        self.assertLess(first_history.index("dynamic extra"), first_history.index("User: first event"))
        self.assertLess(first_history.index("persona state A"), first_history.index("User: first event"))
        first_event_end = first_history.index("User: first event") + len("User: first event")
        self.assertEqual(first_history[:first_event_end], second_history[:first_event_end])
        self.assertEqual(
            first["history_turns"],
            second["history_turns"][: len(first["history_turns"])],
        )

    def test_per_turn_context_stays_after_append_only_history(self) -> None:
        builder = PromptBuilder(load_persona_config())
        common = {
            "now_ts": 1_712_400_000,
            "raw_text": "User: first event",
            "episodic_summary_text": "stable episode",
            "semantic_summary_text": "stable semantic",
            "memory_text": "",
            "resource_context": "",
            "extra_context": "stable runtime context",
            "visual_defaults": {
                "major": "home",
                "minor": "room",
                "background": "morning",
                "bgm": "",
                "outfit": "default",
                "emotion": "normal",
            },
            "allow_tool_call": True,
            "tool_prompt_context": "stable tool contract",
            "debug_enabled": False,
            "persona_system_context": "stable persona state",
            "persona_reference_context": "stable persona reference",
        }
        first = builder.build_final_generation_context(
            **common,
            history_turns=[{"role": "user", "content": "User: first event"}],
            current_message_text="User: current A",
            current_visual_context="visual A",
            volatile_extra_context="turn context A",
        )
        second = builder.build_final_generation_context(
            **common,
            history_turns=[
                {"role": "user", "content": "User: first event"},
                {"role": "assistant", "content": "Assistant: first answer"},
            ],
            current_message_text="User: current B",
            current_visual_context="visual B",
            volatile_extra_context="turn context B",
        )

        self.assertEqual(
            first["history_turns"],
            second["history_turns"][: len(first["history_turns"])],
        )
        self.assertIn("stable runtime context", _history_text(first))
        self.assertNotIn("turn context A", _history_text(first))
        self.assertNotIn("visual A", _history_text(first))
        self.assertNotIn("stable runtime context", first["user_prompt"])
        self.assertEqual(first["user_prompt"], "User: current A")
        self.assertIn("turn context A", _ephemeral_text(first))
        self.assertIn("visual A", _ephemeral_text(first))
        audit = {section["name"]: section["text"] for section in first["prompt_audit_sections"]}
        self.assertEqual(audit["user.stable_extra_context"], "stable runtime context")
        self.assertEqual(audit["user.volatile_extra_context"], "turn context A")

    def test_large_stable_runtime_context_is_not_frozen_into_each_memcore_turn(self) -> None:
        builder = PromptBuilder(load_persona_config())
        stable_runtime = "stable runtime state " * 2_000
        common = {
            "now_ts": 1_712_400_000,
            "raw_text": "",
            "episodic_summary_text": "",
            "semantic_summary_text": "",
            "memory_text": "",
            "current_visual_context": "visual",
            "resource_context": "",
            "extra_context": stable_runtime,
            "visual_defaults": {
                "major": "home",
                "minor": "room",
                "background": "morning",
                "bgm": "",
                "outfit": "default",
                "emotion": "normal",
            },
            "allow_tool_call": True,
            "tool_prompt_context": "stable tool contract",
            "debug_enabled": False,
            "persona_system_context": "stable persona state",
            "persona_reference_context": "stable persona reference",
            "volatile_extra_context": "current transport context",
            "current_message_in_raw": True,
        }
        first = builder.build_final_generation_context(
            **common,
            history_turns=[],
            current_message_text="User: first",
        )
        second = builder.build_final_generation_context(
            **common,
            history_turns=[
                {"role": "user", "content": first["user_prompt"]},
                {"role": "assistant", "content": "Assistant: first answer"},
            ],
            current_message_text="User: second",
        )

        self.assertNotIn(stable_runtime.strip(), first["user_prompt"])
        self.assertNotIn(stable_runtime.strip(), second["user_prompt"])
        self.assertLess(len(second["user_prompt"]), 300)
        self.assertEqual(_provider_text(second).count(stable_runtime.strip()), 1)
        self.assertIn(first["user_prompt"], _history_text(second))

    def test_plugin_proactive_scope_uses_stable_system_and_appends_memory_timeline_once(self) -> None:
        builder = PromptBuilder(load_persona_config())

        result = builder.build_final_generation_context(
            now_ts=1_712_400_000,
            raw_text="Assistant: earlier analysis\nUser: real finance event",
            history_turns=[{"role": "assistant", "content": "Assistant: earlier analysis"}],
            current_message_text="User: real finance event",
            episodic_summary_text="ordinary episodic memory",
            semantic_summary_text="ordinary semantic memory",
            memory_text="automatic retrieval evidence",
            current_visual_context="bounded proactive visual state",
            resource_context="",
            extra_context="stable plugin runtime",
            volatile_extra_context="dynamic plugin instruction",
            visual_defaults={
                "major": "home",
                "minor": "room",
                "background": "morning",
                "bgm": "",
                "outfit": "default",
                "emotion": "normal",
            },
            allow_tool_call=True,
            tool_prompt_context="stable finance capability contract",
            debug_enabled=False,
            prompt_scope="plugin_proactive",
            stable_system_context="stable finance research principles",
            current_message_in_raw=True,
        )

        prompt = _provider_text(result)
        self.assertEqual(result["system_extra_blocks"][0], "stable finance research principles")
        self.assertNotIn("stable finance research principles", prompt)
        self.assertIn("ordinary episodic memory", prompt)
        self.assertIn("ordinary semantic memory", prompt)
        self.assertIn("automatic retrieval evidence", prompt)
        self.assertIn("Assistant: earlier analysis", prompt)
        self.assertEqual(prompt.count("real finance event"), 1)
        self.assertEqual(result["user_prompt"], "User: real finance event")
        self.assertNotIn("当前时间：", prompt)
        self.assertNotIn("debug_enabled=", prompt)
        self.assertNotIn("debug_enabled=", result["system_prompt"])
        self.assertLess(prompt.index("stable finance capability contract"), prompt.index("ordinary semantic memory"))
        self.assertLess(prompt.index("ordinary semantic memory"), prompt.index("ordinary episodic memory"))
        self.assertLess(prompt.index("ordinary episodic memory"), prompt.index("Assistant: earlier analysis"))
        self.assertLess(prompt.index("Assistant: earlier analysis"), prompt.index("automatic retrieval evidence"))
        self.assertLess(prompt.index("real finance event"), prompt.index("automatic retrieval evidence"))
        self.assertLess(prompt.index("stable plugin runtime"), prompt.index("Assistant: earlier analysis"))
        self.assertLess(prompt.index("automatic retrieval evidence"), prompt.index("dynamic plugin instruction"))
        self.assertLess(prompt.index("dynamic plugin instruction"), prompt.index("bounded proactive visual state"))
        self.assertNotIn("stable plugin runtime", result["user_prompt"])
        self.assertIn("stable plugin runtime", _history_text(result))
        self.assertIn("dynamic plugin instruction", _ephemeral_text(result))
        self.assertTrue(result["stable_system_context_hash"])

    def test_plugin_proactive_scope_falls_back_to_current_message_when_raw_does_not_contain_it(self) -> None:
        builder = PromptBuilder(load_persona_config())
        result = builder.build_final_generation_context(
            now_ts=1_712_400_000,
            raw_text="Assistant: earlier analysis",
            current_message_text="User: current finance event",
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
                "bgm": "",
                "outfit": "default",
                "emotion": "normal",
            },
            allow_tool_call=True,
            tool_prompt_context="tools",
            debug_enabled=False,
            prompt_scope="plugin_proactive",
            current_message_in_raw=False,
        )

        self.assertEqual(result["user_prompt"].count("current finance event"), 1)
        self.assertIn("当前时间：", _ephemeral_text(result))

    def test_plugin_proactive_scope_uses_exact_memcore_current_turn_when_no_retrieval_tail(self) -> None:
        builder = PromptBuilder(load_persona_config())
        result = builder.build_final_generation_context(
            now_ts=1_712_400_000,
            raw_text="Assistant: earlier analysis\nUser: exact finance event",
            history_turns=[{"role": "assistant", "content": "Assistant: earlier analysis"}],
            current_message_text="User: exact finance event",
            episodic_summary_text="stable episode",
            semantic_summary_text="stable semantic",
            memory_text="",
            current_visual_context="stable proactive visual",
            resource_context="",
            extra_context="stable proactive runtime",
            volatile_extra_context="stable delivery limit",
            visual_defaults={
                "major": "home",
                "minor": "room",
                "background": "morning",
                "bgm": "",
                "outfit": "default",
                "emotion": "normal",
            },
            allow_tool_call=True,
            tool_prompt_context="stable tools",
            debug_enabled=False,
            prompt_scope="plugin_proactive",
            current_message_in_raw=True,
        )

        self.assertTrue(result["linear_timeline_turn"])
        self.assertEqual(result["user_prompt"], "User: exact finance event")
        ephemeral = _ephemeral_text(result)
        self.assertIn("stable delivery limit", ephemeral)
        self.assertIn("stable proactive visual", ephemeral)
        self.assertLess(
            _provider_text(result).index("User: exact finance event"),
            _provider_text(result).index("stable delivery limit"),
        )
        history = _history_text(result)
        self.assertIn("stable proactive runtime", history)
        self.assertNotIn("stable delivery limit", history)
        self.assertNotIn("stable proactive visual", history)
        self.assertNotIn("stable proactive runtime", result["user_prompt"])
        self.assertLess(history.index("stable proactive runtime"), history.index("Assistant: earlier analysis"))

    def test_normal_and_proactive_scopes_share_one_append_only_timeline_layout(self) -> None:
        builder = PromptBuilder(load_persona_config())
        common = {
            "now_ts": 1_712_400_000,
            "episodic_summary_text": "stable episode",
            "semantic_summary_text": "stable semantic",
            "memory_text": "",
            "current_visual_context": "stable qq visual state",
            "resource_context": "",
            "extra_context": "stable runtime context",
            "volatile_extra_context": "stable delivery context",
            "visual_defaults": {
                "major": "home",
                "minor": "room",
                "background": "morning",
                "bgm": "",
                "outfit": "default",
                "emotion": "normal",
            },
            "allow_tool_call": True,
            "tool_prompt_context": "stable tools",
            "debug_enabled": False,
            "stable_system_context": "stable finance research principles",
            "current_message_in_raw": True,
        }
        timeline = [
            ("[10:00] Master: 普通消息 A", "", "[10:01] Akane: 普通回复 A"),
            (
                "[10:02] event.finance\nsource: mock\npublished_at: 2026-04-06T10:02:00+08:00\n"
                "title: 金融事件 A\nsummary: 摘要 A\nurl: https://example.com/a",
                "plugin_proactive",
                "[10:03] Akane: 金融分析 A",
            ),
            ("[10:04] Master: 普通消息 B", "", "[10:05] Akane: 普通回复 B"),
            (
                "[10:06] event.finance\nsource: mock\npublished_at: 2026-04-06T10:06:00+08:00\n"
                "title: 金融事件 B\nsummary: 摘要 B\nurl: https://example.com/b",
                "plugin_proactive",
                "[10:07] Akane: 金融分析 B",
            ),
        ]
        history: list[dict[str, str]] = []
        requests: list[dict] = []
        expected_raw_histories: list[list[dict[str, str]]] = []
        for current_message, prompt_scope, assistant_reply in timeline:
            result = builder.build_final_generation_context(
                **common,
                raw_text="\n".join([str(turn["content"]) for turn in history] + [current_message]),
                history_turns=list(history),
                current_message_text=current_message,
                prompt_scope=prompt_scope,
            )
            self.assertTrue(result["linear_timeline_turn"])
            self.assertEqual(result["user_prompt"], current_message)
            self.assertIn("stable delivery context", _ephemeral_text(result))
            self.assertIn("stable qq visual state", _ephemeral_text(result))
            self.assertNotIn("stable delivery context", _history_text(result))
            self.assertNotIn("stable qq visual state", _history_text(result))
            expected_raw_histories.append(list(history))
            requests.append(result)
            history.extend(
                [
                    {"role": "user", "content": current_message},
                    {"role": "assistant", "content": assistant_reply},
                ]
            )

        stable_prefixes: list[list[dict]] = []
        for request, raw_history in zip(requests, expected_raw_histories):
            history_turns = request["history_turns"]
            if raw_history:
                self.assertEqual(history_turns[-len(raw_history) :], raw_history)
                stable_prefixes.append(history_turns[: -len(raw_history)])
            else:
                stable_prefixes.append(history_turns)
        for previous, current in zip(requests, requests[1:]):
            self.assertEqual(previous["system_prompt"], current["system_prompt"])
            self.assertEqual(previous["system_extra_blocks"], current["system_extra_blocks"])
        for stable_prefix in stable_prefixes[1:]:
            self.assertEqual(stable_prefixes[0], stable_prefix)

        final_prompt = _provider_text(requests[-1])
        for current_message, _prompt_scope, _assistant_reply in timeline:
            self.assertEqual(final_prompt.count(current_message), 1)

        normal = builder.build_final_generation_context(
            **common,
            raw_text="same current turn",
            current_message_text="same current turn",
            prompt_scope="",
        )
        proactive = builder.build_final_generation_context(
            **common,
            raw_text="same current turn",
            current_message_text="same current turn",
            prompt_scope="plugin_proactive",
        )
        self.assertEqual(_provider_turns(normal), _provider_turns(proactive))
        self.assertEqual(normal["system_prompt"], proactive["system_prompt"])

    def test_plugin_stable_system_hash_ignores_dynamic_finance_event(self) -> None:
        builder = PromptBuilder(load_persona_config())
        common = {
            "now_ts": 1_712_400_000,
            "episodic_summary_text": "",
            "semantic_summary_text": "",
            "memory_text": "",
            "current_visual_context": "",
            "resource_context": "",
            "extra_context": "",
            "visual_defaults": {
                "major": "home",
                "minor": "room",
                "background": "morning",
                "bgm": "",
                "outfit": "default",
                "emotion": "normal",
            },
            "allow_tool_call": True,
            "tool_prompt_context": "tools",
            "debug_enabled": False,
            "prompt_scope": "plugin_proactive",
            "stable_system_context": "stable finance research principles",
            "current_message_in_raw": True,
        }

        first = builder.build_final_generation_context(
            **common,
            raw_text="User: finance event A",
            current_message_text="User: finance event A",
        )
        second = builder.build_final_generation_context(
            **common,
            raw_text="User: finance event B",
            current_message_text="User: finance event B",
        )

        self.assertEqual(first["stable_system_context_hash"], second["stable_system_context_hash"])
        self.assertNotEqual(first["user_prompt"], second["user_prompt"])

    def test_registered_system_blocks_are_shared_by_normal_and_proactive_turns(self) -> None:
        builder = PromptBuilder(
            load_persona_config(),
            stable_system_blocks_provider=lambda: (
                "finance research method",
                "finance research method",
                "finance risk rules",
            ),
        )

        normal = _build_minimal_final(
            builder,
            current_message_text="User: normal conversation",
            raw_text="User: normal conversation",
            stable_system_context="finance research method",
            domain_profile_context="shared domain profile",
            resource_context="shared visual resource",
        )
        proactive = _build_minimal_final(
            builder,
            current_message_text="event.finance: market news",
            raw_text="event.finance: market news",
            stable_system_context="finance research method",
            domain_profile_context="shared domain profile",
            resource_context="shared visual resource",
            prompt_scope="plugin_proactive",
            current_message_in_raw=True,
        )

        expected = [
            "finance research method",
            "finance risk rules",
            "shared domain profile",
            "可用视觉资源：\nshared visual resource",
        ]
        self.assertEqual(normal["system_extra_blocks"], expected)
        self.assertEqual(proactive["system_extra_blocks"], expected)
        self.assertEqual(
            normal["stable_system_context_hash"],
            proactive["stable_system_context_hash"],
        )
        audit = {section["name"]: section["text"] for section in normal["prompt_audit_sections"]}
        self.assertIn("system_extra.registered_stable_metadata", audit)
        self.assertNotIn(
            "finance research method",
            audit["system_extra.registered_stable_metadata"],
        )

    def test_registered_system_hash_tracks_block_text_and_order_not_dynamic_tail(self) -> None:
        provided = [("research-a", "research-b")]
        builder = PromptBuilder(
            load_persona_config(),
            stable_system_blocks_provider=lambda: provided[0],
        )

        first = _build_minimal_final(
            builder,
            current_message_text="event.finance: A",
            raw_text="event.finance: A",
        )
        same_prefix = _build_minimal_final(
            builder,
            current_message_text="event.finance: B",
            raw_text="event.finance: B",
            now_ts=1_800_000_000,
            memory_text="different retrieval tail",
            episodic_summary_text="different compacted memory",
        )
        provided[0] = ("research-b", "research-a")
        reordered = _build_minimal_final(builder)
        provided[0] = ("research-b", "research-changed")
        changed = _build_minimal_final(builder)

        self.assertEqual(
            first["stable_system_context_hash"],
            same_prefix["stable_system_context_hash"],
        )
        self.assertNotEqual(
            first["stable_system_context_hash"],
            reordered["stable_system_context_hash"],
        )
        self.assertNotEqual(
            reordered["stable_system_context_hash"],
            changed["stable_system_context_hash"],
        )

    def test_legacy_stable_system_hash_is_unchanged_without_registered_blocks(self) -> None:
        legacy_text = "stable finance research principles"
        result = _build_minimal_final(
            PromptBuilder(load_persona_config()),
            stable_system_context=legacy_text,
        )

        self.assertEqual(
            result["stable_system_context_hash"],
            hashlib.sha256(legacy_text.encode("utf-8")).hexdigest(),
        )

    def test_final_output_schema_places_tool_call_after_speech_segments(self) -> None:
        persona = load_persona_config()
        builder = PromptBuilder(persona)

        self.assertIn("字段固定为 emotion, speech, speech_segments, tool_call", persona.final_fast_mode_prompt)
        self.assertIn(
            '"speech":"我在哦，欢迎回来。","speech_segments":[],"tool_call":null', persona.final_fast_mode_prompt
        )
        self.assertIn(
            "字段固定为 thought, emotion, speech, speech_segments, tool_call", persona.final_debug_mode_prompt
        )
        self.assertIn(
            '"speech":"我在哦，欢迎回来。","speech_segments":[],"tool_call":null', persona.final_debug_mode_prompt
        )
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

    def test_desktop_pet_system_prompt_is_block_composed_and_pet_scoped(self) -> None:
        prompt = build_desktop_pet_system_prompt()

        self.assertIn("desktop_pet 桌宠模式", prompt)
        self.assertIn("只能从本轮给你的角色包资源清单里选择，不要编造不存在的 emotion", prompt)
        self.assertIn("用户说出「生成/转换/发送/处理/导出/提取/分析文件」", prompt)
        self.assertIn("activity 是给桌宠执行的请求", prompt)
        self.assertIn("affinity 是本轮好感度变化量", prompt)
        self.assertIn("不是当前总值", prompt)
        self.assertIn("[CURRENT ASSISTANT STATE - EMBODY THIS]", prompt)
        self.assertNotIn("scene.major 表示场景大类", prompt)
        self.assertNotIn("像 galgame 选项", prompt)
        self.assertNotIn("QQ 文字聊天模式", prompt)

    def test_qq_text_system_prompt_is_block_composed_and_text_scoped(self) -> None:
        prompt = build_qq_text_system_prompt()

        self.assertIn("当前是 QQ 文字聊天模式", prompt)
        self.assertIn("用户说出「生成/转换/发送/处理/导出/提取/分析文件」", prompt)
        self.assertIn("不要总拿上一轮或更早的事开头", prompt)
        self.assertIn("先回用户眼前这句话", prompt)
        self.assertIn("[CURRENT ASSISTANT STATE - EMBODY THIS]", prompt)
        self.assertNotIn("desktop_pet 桌宠模式", prompt)
        self.assertNotIn("scene.major 表示场景大类", prompt)
        self.assertNotIn("character.outfit 表示服装大类", prompt)
        self.assertNotIn("activity 是给桌宠执行的请求", prompt)

    def test_final_prompt_includes_group_speaker_and_material_attribution_rules(self) -> None:
        persona = load_persona_config()
        builder = PromptBuilder(persona)

        result = builder.build_final_generation_context(
            now_ts=1712400000,
            raw_text="[12:00] user: 【休比】这张图是我发的",
            current_message_text="[12:01] user: 【灵梦】那是谁发的图？",
            episodic_summary_text="",
            semantic_summary_text="",
            memory_text="",
            current_visual_context="",
            resource_context="",
            extra_context="【当前材料工作台】\n1. [img_001] 图片《窗边小猫》\n   发送者：休比",
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

        history_text = _history_text(result)
        self.assertIn("发言与材料归属规则", history_text)
        self.assertIn("`【名字】正文` 是群聊说话人标签", history_text)
        self.assertIn("图片、文件和工作台材料若写有“发送者”", history_text)

    def test_desktop_pet_profile_override_removes_generic_scene_rules_from_final_prompt(self) -> None:
        persona = load_persona_config()
        builder = PromptBuilder(persona)
        profile = PromptProfileRegistry().get(ClientMode.DESKTOP_PET)

        result = builder.build_final_generation_context(
            now_ts=1712400000,
            raw_text="User: hi",
            current_message_text="User: hi",
            episodic_summary_text="",
            semantic_summary_text="",
            memory_text="",
            current_visual_context="服装: default；表情: normal",
            resource_context="可用服装与表情：\n- default -> 表情: normal, happy",
            extra_context="",
            visual_defaults={
                "major": "home",
                "minor": "room",
                "background": "morning",
                "bgm": "",
                "outfit": "default",
                "emotion": "normal",
            },
            allow_tool_call=False,
            tool_prompt_context="",
            debug_enabled=False,
            persona_system_context="角色包身份：Mika",
            system_prompt_override=profile.system_prompt_override,
            mode_prompt_override=profile.mode_prompt_override(debug_enabled=False),
        )

        self.assertIn("desktop_pet 桌宠模式", result["system_prompt"])
        self.assertNotIn("角色包身份：Mika", result["system_prompt"])
        self.assertIn("角色包身份：Mika", _history_text(result))
        self.assertNotIn("角色包身份：Mika", result["user_prompt"])
        self.assertIn("字段固定为 emotion, speech", result["system_prompt"])
        self.assertIn("memory_metadata", result["system_prompt"])
        self.assertIn("memory_metadata", result["fallback"])
        self.assertIn("mood_tags", result["fallback"]["memory_metadata"])
        self.assertNotIn("memory_tags", result["fallback"])
        self.assertNotIn("scene.major 表示场景大类", result["system_prompt"])
        self.assertNotIn("像 galgame 选项", result["system_prompt"])

    def test_care_disabled_profiles_remove_state_contract_without_losing_schema(self) -> None:
        registry = PromptProfileRegistry()
        for mode in (ClientMode.DESKTOP_PET, ClientMode.QQ_TEXT, ClientMode.SCENE_STATIC):
            profile = registry.get(mode, care_enabled=False)
            combined = "\n".join([profile.system_prompt_override, profile.fast_mode_prompt, profile.debug_mode_prompt])
            self.assertNotIn("state_request", combined)
            self.assertNotIn("state_request", profile.system_block_ids)

        desktop = registry.get(ClientMode.DESKTOP_PET, care_enabled=False)
        self.assertIn("emotion", desktop.fast_mode_prompt)
        self.assertIn("speech_segments", desktop.fast_mode_prompt)
        self.assertIn("tool_call", desktop.fast_mode_prompt)
        self.assertIn("memory_metadata", desktop.fast_mode_prompt)

    def test_strip_care_prompt_contract_preserves_non_care_json_fields(self) -> None:
        source = (
            "字段固定为 emotion, speech, state_request，禁止输出 scene。\n"
            '{"emotion":"normal","speech":"hi","memory_metadata":{},"state_request":null}\n'
            "state_request 用于修改养成状态。"
        )
        stripped = strip_care_prompt_contract(source)
        self.assertNotIn("state_request", stripped)
        self.assertIn('"emotion":"normal"', stripped)
        self.assertIn('"memory_metadata":{}', stripped)

    def test_care_disabled_profile_reaches_real_final_system_prompt(self) -> None:
        builder = PromptBuilder(load_persona_config())
        profile = PromptProfileRegistry().get(ClientMode.DESKTOP_PET, care_enabled=False)

        result = builder.build_final_generation_context(
            now_ts=1712400000,
            raw_text="User: hi",
            current_message_text="User: hi",
            episodic_summary_text="",
            semantic_summary_text="",
            memory_text="",
            current_visual_context="服装: default；表情: normal",
            resource_context="可用服装与表情：normal",
            extra_context="",
            visual_defaults={
                "major": "home",
                "minor": "room",
                "background": "morning",
                "bgm": "",
                "outfit": "default",
                "emotion": "normal",
            },
            allow_tool_call=False,
            tool_prompt_context="",
            debug_enabled=False,
            system_prompt_override=profile.system_prompt_override,
            mode_prompt_override=profile.mode_prompt_override(debug_enabled=False),
        )

        self.assertNotIn("state_request", result["system_prompt"])
        self.assertNotIn("affinity", result["system_prompt"])
        self.assertIn("speech_segments", result["system_prompt"])
        self.assertIn("memory_metadata", result["system_prompt"])


if __name__ == "__main__":
    unittest.main()
