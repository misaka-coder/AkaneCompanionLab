"""Akane 宿主侧: MEMCORE_OPERATION_PROJECTION_POLICY 配置与紧凑回读提示。

覆盖:
- 配置缺省 = full_until_raw_compaction(零行为差异), 两个合法 wire value 解析, 非法值启动失败;
- manager._build_memory_config 把解析后 wire value 传给 MemoryConfig;
- build_context_projection 透传 has_compact_history;
- 回读提示注入条件 = 当前策略为 compact OR 可见历史含 compact turn(切回 full 后旧 compact 提示仍在);
- 缺省时提示为空(请求体逐字节不变), 角色包 schema 不变。
"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from companion_v01.engine_services.response_builder import (
    COMPACT_READBACK_STABLE_HINT,
    build_compact_readback_hint,
    with_compact_readback_hint,
)

ROOT = Path(__file__).resolve().parents[1]


def _run_config_probe(*, env: dict[str, str], code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )


def _probe_env(**overrides: str) -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    env.setdefault("AKANE_DATA_ROOT", str(ROOT / "users_data"))
    for key, value in overrides.items():
        env[key] = value
    return env


class OperationProjectionPolicyConfigTests(unittest.TestCase):
    def test_settlement_saved_ratio_defaults_to_thirty_percent(self) -> None:
        env = _probe_env()
        env.pop("MEMCORE_OPERATION_SETTLEMENT_MIN_SAVED_RATIO", None)
        result = _run_config_probe(
            env=env,
            code="import config; print(config.MEMCORE_OPERATION_SETTLEMENT_MIN_SAVED_RATIO)",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.strip(), "0.3")

    def test_settlement_saved_ratio_accepts_selected_values_and_rejects_invalid(self) -> None:
        name = "MEMCORE_OPERATION_SETTLEMENT_MIN_SAVED_RATIO"
        for value in ("0.2", "0.3", "0.5"):
            with self.subTest(value=value):
                result = _run_config_probe(
                    env=_probe_env(**{name: value}),
                    code=f"import config; print(config.{name})",
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(float(result.stdout.strip()), float(value))
        for value in ("0", "1", "-0.1", "nan", "inf"):
            with self.subTest(value=value):
                result = _run_config_probe(env=_probe_env(**{name: value}), code="import config")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(name, result.stderr)

    def test_default_is_full_until_raw_compaction(self) -> None:
        env = _probe_env()
        env.pop("MEMCORE_OPERATION_PROJECTION_POLICY", None)
        result = _run_config_probe(
            env=env,
            code="import config; print(config.MEMCORE_OPERATION_PROJECTION_POLICY)",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.strip(), "full_until_raw_compaction")

    def test_compact_wire_value_parses(self) -> None:
        env = _probe_env(MEMCORE_OPERATION_PROJECTION_POLICY="compact_after_terminal")
        result = _run_config_probe(
            env=env,
            code="import config; print(config.MEMCORE_OPERATION_PROJECTION_POLICY)",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.strip(), "compact_after_terminal")

    def test_upper_case_full_wire_value_normalizes(self) -> None:
        env = _probe_env(MEMCORE_OPERATION_PROJECTION_POLICY="FULL_UNTIL_RAW_COMPACTION")
        result = _run_config_probe(
            env=env,
            code="import config; print(config.MEMCORE_OPERATION_PROJECTION_POLICY)",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.strip(), "full_until_raw_compaction")

    def test_invalid_value_fails_loading_without_fallback(self) -> None:
        env = _probe_env(MEMCORE_OPERATION_PROJECTION_POLICY="compress_everything")
        result = _run_config_probe(
            env=env,
            code="import config; print(config.MEMCORE_OPERATION_PROJECTION_POLICY)",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid MEMCORE_OPERATION_PROJECTION_POLICY", result.stderr)


class ManagerConfigTests(unittest.TestCase):
    def _manager_and_memcore(self) -> tuple[object, object]:
        from types import SimpleNamespace

        from companion_v01.memcore_integration.manager import MemcoreManager

        manager = MemcoreManager.__new__(MemcoreManager)
        manager.visible_scope = "conversation"
        manager.enable_flavor = False
        fake_memcore = SimpleNamespace(
            DEFAULT_CATEGORIES=("tool_trace",),
            MemoryConfig=lambda **kwargs: kwargs,
        )
        return manager, fake_memcore

    def test_memory_config_receives_resolved_policy(self) -> None:
        import config

        manager, fake_memcore = self._manager_and_memcore()
        with patch.object(config, "MEMCORE_OPERATION_PROJECTION_POLICY", "compact_after_terminal", create=True):
            memory_config = manager._build_memory_config(fake_memcore)
        self.assertEqual(memory_config["operation_projection_policy"], "compact_after_terminal")

    def test_memory_config_defaults_to_full(self) -> None:
        import config

        manager, fake_memcore = self._manager_and_memcore()
        with patch.object(config, "MEMCORE_OPERATION_PROJECTION_POLICY", "full_until_raw_compaction", create=True):
            memory_config = manager._build_memory_config(fake_memcore)
        self.assertEqual(memory_config["operation_projection_policy"], "full_until_raw_compaction")

    def test_argument_policy_and_thresholds_reach_memcore_config(self) -> None:
        import config

        manager, fake_memcore = self._manager_and_memcore()
        with patch.multiple(config, MEMCORE_OPERATION_PROJECTION_POLICY="compact_arguments_after_terminal",
                            MEMCORE_OPERATION_ARGUMENT_MIN_UTF8_BYTES=2048, MEMCORE_OPERATION_ARGUMENT_PREVIEW_CHARS=37,
                            MEMCORE_OPERATION_SETTLEMENT_MIN_SAVED_RATIO=0.3):
            memory_config = manager._build_memory_config(fake_memcore)
        self.assertEqual(memory_config["operation_projection_policy"], "compact_arguments_after_terminal")
        self.assertEqual(memory_config["operation_argument_min_utf8_bytes"], 2048)
        self.assertEqual(memory_config["operation_argument_preview_chars"], 37)
        self.assertEqual(memory_config["operation_settlement_min_saved_ratio"], 0.3)

    def test_build_context_projection_carries_has_compact_history(self) -> None:
        from companion_v01.memcore_integration.manager import MemcoreManager

        class _FakeProjection:
            provider_profile = "openai_chat"
            messages = ()
            payloads = ()
            stable_prefix_hash = "h"
            projection_version = 1
            compaction_generation = 0
            projection_generation = 0
            has_compact_history = True
            has_compact_arguments_history = True

        class _FakeSystem:
            def build_context_projection(self, *, provider_profile: str):
                return _FakeProjection()

        manager = MemcoreManager.__new__(MemcoreManager)
        manager._get_system_or_none = lambda *args, **kwargs: _FakeSystem()
        projection = manager.build_context_projection(
            provider_profile="openai_chat",
            profile_user_id="u1",
            session_id="s1",
        )
        self.assertTrue(projection["ok"])
        self.assertIs(projection["has_compact_history"], True)
        self.assertIs(projection["has_compact_arguments_history"], True)

    def test_build_context_projection_defaults_has_compact_history_false(self) -> None:
        from companion_v01.memcore_integration.manager import MemcoreManager

        class _FakeProjection:
            provider_profile = "openai_chat"
            messages = ()
            payloads = ()
            stable_prefix_hash = "h"
            projection_version = 1
            compaction_generation = 0
            projection_generation = 0

        class _FakeSystem:
            def build_context_projection(self, *, provider_profile: str):
                return _FakeProjection()

        manager = MemcoreManager.__new__(MemcoreManager)
        manager._get_system_or_none = lambda *args, **kwargs: _FakeSystem()
        projection = manager.build_context_projection(
            provider_profile="openai_chat",
            profile_user_id="u1",
            session_id="s1",
        )
        self.assertTrue(projection["ok"])
        self.assertIs(projection["has_compact_history"], False)


class SettlementSavedRatioBehaviorTests(unittest.TestCase):
    def test_lower_ratio_settles_only_closed_results_and_keeps_readback_and_frozen_history(self) -> None:
        import config
        import memcore
        from companion_v01.memcore_integration.manager import MemcoreManager

        class NoopLLM(memcore.LLMClient):
            def call(self, request):
                return memcore.LLMResult(ok=True, data=request.fallback or {}, attempts=1)

        body = "Synthetic result. " + "x" * 250 + "\nSAFE_TAIL=VALUE"
        for ratio in (0.5, 0.3, 0.2):
            with self.subTest(ratio=ratio):
                manager = MemcoreManager.__new__(MemcoreManager)
                manager.visible_scope = "conversation"
                manager.enable_flavor = False
                with patch.multiple(config, MEMCORE_OPERATION_PROJECTION_POLICY="compact_arguments_after_terminal",
                                    MEMCORE_OPERATION_SETTLEMENT_MIN_SAVED_RATIO=ratio):
                    memory_config = manager._build_memory_config(memcore)
                store = memcore.SQLiteMemoryStore(":memory:")
                embedding = memcore.HashedEmbeddingProvider()
                memory = memcore.MemorySystem(
                    llm=NoopLLM(), store=store, embedding=embedding,
                    index=memcore.InMemoryVectorIndex(embedding=embedding),
                    namespace=memcore.Namespace(user_id="ratio-test", conversation_id="ratio-test"),
                    config=memory_config, timezone="Asia/Shanghai",
                )
                try:
                    memory.begin_turn(turn_id="t1", stimuli=[memcore.TimelineEntryInput(
                        source_id="ratio-user", kind="message.user", origin=memcore.EntryOrigin.USER,
                        turn_role=memcore.TurnRole.STIMULUS, semantic_text="Record an isolated fixture.",
                        payload={"text": "Record an isolated fixture."},
                    )])
                    exchange = memory.record_tool_exchange(
                        turn_id="t1", tool_name="workspace_write", tool_call_id="ratio-call",
                        tool_input={"path": "fixture.txt", "content": "a" * 1024}, result=body,
                        source_id_prefix="ratio-test-tool-exchange-with-observation",
                    )
                    opened = memory.build_context_projection(provider_profile="openai_chat")
                    self.assertTrue(any(m.payload.get("role") == "tool" and m.payload["content"] == body
                                        for m in opened.messages))
                    memory.config.operation_settlement_min_saved_ratio = 0.8
                    memory.complete_turn(
                        turn_id="t1", semantic_text="Recorded.", provider_output_raw="Recorded.",
                        provider_profile="openai_chat", provider_projection={"role": "assistant", "content": "Recorded."},
                    )
                    row = store.get_turn_projection_settlement(
                        namespace=memory.namespace, turn_id="t1", provider_profile="openai_chat",
                    )
                    self.assertEqual(row["settlement_min_saved_ratio"], ratio)
                    closed = memory.build_context_projection(provider_profile="openai_chat")
                    result = next(m.payload["content"] for m in closed.messages if m.payload.get("role") == "tool")
                    if ratio == 0.5:
                        self.assertEqual(result, body)
                    else:
                        self.assertIn("[compact_reloadable]", result)
                        original = memory.open_memory(memory_id=exchange["tool_result"]["source_id"], view="content", detail="full")
                        self.assertEqual(original["status"], "ok")
                        self.assertIn(body, original["text"])
                    self.assertEqual(closed.payloads, memory.build_context_projection(provider_profile="openai_chat").payloads)
                finally:
                    memory.close()
                    store.close()


class CompactReadbackHintTests(unittest.TestCase):
    def test_argument_hint_is_short_stable_and_kept_after_rollback(self) -> None:
        from companion_v01.engine_services.response_assembly import COMPACT_ARGUMENT_READBACK_STABLE_HINT

        hint = build_compact_readback_hint(policy="compact_arguments_after_terminal", has_compact_history=False)
        self.assertEqual(hint, COMPACT_ARGUMENT_READBACK_STABLE_HINT)
        self.assertIn("open_memory", hint)
        self.assertNotIn("参数仍在", hint)
        self.assertEqual(build_compact_readback_hint(policy="full_until_raw_compaction", has_compact_history=True,
                                                   has_compact_arguments_history=True), hint)
        context = with_compact_readback_hint("stable base", policy="compact_arguments_after_terminal", has_compact_history=False)
        self.assertEqual(with_compact_readback_hint(context, policy="compact_arguments_after_terminal",
                                                  has_compact_history=True), context)

    def test_argument_config_is_accepted_and_rejects_nonpositive(self) -> None:
        accepted = _run_config_probe(env=_probe_env(MEMCORE_OPERATION_PROJECTION_POLICY="compact_arguments_after_terminal"),
                                    code="import config; print(config.MEMCORE_OPERATION_PROJECTION_POLICY)")
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertEqual(accepted.stdout.strip(), "compact_arguments_after_terminal")
        for name in ("MEMCORE_OPERATION_ARGUMENT_MIN_UTF8_BYTES", "MEMCORE_OPERATION_ARGUMENT_PREVIEW_CHARS"):
            rejected = _run_config_probe(env=_probe_env(**{name: "0"}), code="import config")
            self.assertNotEqual(rejected.returncode, 0)

    def test_compact_policy_injects_hint_even_without_compact_history(self) -> None:
        hint = build_compact_readback_hint(policy="compact_after_terminal", has_compact_history=False)
        self.assertIn("紧凑回执", hint)
        self.assertIn("source_id", hint)

    def test_full_policy_with_compact_history_still_injects_hint(self) -> None:
        hint = build_compact_readback_hint(policy="full_until_raw_compaction", has_compact_history=True)
        self.assertIn("紧凑回执", hint)

    def test_default_full_policy_without_compact_history_is_empty(self) -> None:
        hint = build_compact_readback_hint(policy="full_until_raw_compaction", has_compact_history=False)
        self.assertEqual(hint, "")

    def test_empty_policy_is_treated_as_full_default(self) -> None:
        hint = build_compact_readback_hint(policy="", has_compact_history=False)
        self.assertEqual(hint, "")

    def test_prompt_context_rebuild_does_not_append_the_hint_twice(self) -> None:
        first = with_compact_readback_hint(
            "stable base",
            policy="compact_after_terminal",
            has_compact_history=False,
        )
        second = with_compact_readback_hint(
            first,
            policy="compact_after_terminal",
            has_compact_history=True,
        )
        self.assertEqual(second, first)
        self.assertEqual(second.count(COMPACT_READBACK_STABLE_HINT), 1)


class NoRolePackSchemaChangeTests(unittest.TestCase):
    def test_no_character_pack_schema_keys_added(self) -> None:
        import config

        self.assertEqual(getattr(config, "MEMCORE_OPERATION_PROJECTION_POLICY", ""), "full_until_raw_compaction")


if __name__ == "__main__":
    unittest.main()
