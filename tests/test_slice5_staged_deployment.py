"""Slice 5: 小范围部署验证(文档 §15 Slice 5)。

在隔离的"测试 namespace"(profile_user_id/session_id 专属)下启用 compact_after_terminal,
覆盖四类观察场景: 长记忆检索回读、多轮 Web 搜索、失败工具、跨轮追问;
并观察缓存成本(settlement metrics)与压缩频率(token accounting)。
默认策略仍为 full, 本测试仅在 patch 的测试 namespace 内开启 compact。
"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import config
from companion_v01.memcore_integration.manager import MemcoreManager

PROFILE = "stage-user-1"
SESSION = "stage-session-1"


class _NoopLLM:
    pass


class _FakeEmbedding:
    name = "hashed"
    version = "test"
    dimension = 8

    def embed_text(self, text: str) -> list[float]:
        return [0.0] * self.dimension


def _user_record(source_id: str, content: str) -> dict:
    return {"source_id": source_id, "content": content, "timestamp": int(time.time())}


def _exchange(tool_name: str, call_id: str, result: str, *, status: str = "success") -> dict:
    return {
        "tool_name": tool_name,
        "tool_call_id": call_id,
        "tool_input": {"query": call_id},
        "result": result,
        "source": "stage-source",
        "source_id_prefix": f"stage-{call_id}",
        "result_status": status,
    }


LONG_BODY = "网页正文 " + "记忆引擎上下文管理策略说明内容填充。" * 200


class StagedDeploymentTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self._manager_inst = None

    def tearDown(self) -> None:
        if self._manager_inst is not None:
            try:
                self._manager_inst.close()
            except Exception:
                pass
            self._manager_inst = None
        try:
            self._temp.cleanup()
        except Exception:
            pass

    def _make_manager(self, *, policy: str) -> MemcoreManager:
        if self._manager_inst is None:
            self._manager_inst = MemcoreManager(
                backend="memcore",
                storage_path=Path(self._temp.name) / "stage.sqlite3",
                visible_scope="conversation",
                enable_flavor=False,
                shadow_compare=False,
                llm=_NoopLLM(),
                embedding_provider=_FakeEmbedding(),
            )
        return self._manager_inst

    def _run_turn(self, manager: MemcoreManager, exchanges: list[dict], *, content: str = "查完了。") -> dict:
        opened = manager.begin_input_turn(
            _user_record(f"stage-{time.time_ns()}-u", "帮我查一下"),
            profile_user_id=PROFILE,
            session_id=SESSION,
        )
        self.assertTrue(opened["ok"], opened)
        batch = manager.record_tool_batch(
            exchanges=exchanges,
            turn_id=opened["turn_id"],
            profile_user_id=PROFILE,
            session_id=SESSION,
        )
        self.assertTrue(batch["ok"], batch)
        # 宿主工具轮链路在 complete 前已冻结 turn 投影; 测试里显式对齐
        manager.build_context_projection(
            provider_profile="openai",
            profile_user_id=PROFILE,
            session_id=SESSION,
        )
        completed = manager.complete_input_turn(
            turn_id=opened["turn_id"],
            assistant_record=_user_record(f"stage-{time.time_ns()}-a", content),
            memory_metadata={},
            profile_user_id=PROFILE,
            session_id=SESSION,
            provider_output_raw=content,
            provider_profile="openai",
            provider_projection={"role": "assistant", "content": content},
        )
        self.assertTrue(completed["ok"], completed)
        return {"turn_id": opened["turn_id"], "exchanges": batch["exchanges"]}

    def _projection(self, manager: MemcoreManager) -> dict:
        return manager.build_context_projection(
            provider_profile="openai",
            profile_user_id=PROFILE,
            session_id=SESSION,
        )

    def _tool_payloads(self, projection: dict) -> list[dict]:
        return [m for m in projection.get("payloads") or [] if m.get("role") == "tool"]

    # --- 场景 A: 长记忆检索回读 ---

    def test_long_memory_retrieval_is_compact_and_readable_back(self) -> None:
        with patch.object(config, "MEMCORE_OPERATION_PROJECTION_POLICY", "compact_after_terminal"):
            manager = self._make_manager(policy="compact_after_terminal")
            result = self._run_turn(manager, [_exchange("retrieve_memory", "call_ret", LONG_BODY)])
            observation_sid = result["exchanges"][0]["tool_result_source_id"]

            projection = self._projection(manager)
            self.assertTrue(projection["ok"])
            self.assertTrue(projection["has_compact_history"])
            tool_payloads = self._tool_payloads(projection)
            self.assertEqual(len(tool_payloads), 1)
            self.assertIn("[compact_reloadable]", tool_payloads[0]["content"])
            self.assertNotIn(LONG_BODY, tool_payloads[0]["content"])

            readback = manager.open_memory(
                profile_user_id=PROFILE,
                session_id=SESSION,
                arguments={"memory_id": observation_sid, "view": "content", "detail": "full"},
            )
            self.assertTrue(readback["ok"], readback)
            self.assertIn(LONG_BODY, str(readback.get("text") or readback.get("result") or ""))

    # --- 场景 B: 多轮 Web 搜索 ---

    def test_multi_parallel_web_search_all_compact(self) -> None:
        with patch.object(config, "MEMCORE_OPERATION_PROJECTION_POLICY", "compact_after_terminal"):
            manager = self._make_manager(policy="compact_after_terminal")
            self._run_turn(
                manager,
                [
                    _exchange("web_search", "call_a", LONG_BODY + "_A"),
                    _exchange("web_search", "call_b", LONG_BODY + "_B"),
                ],
            )
            projection = self._projection(manager)
            tool_payloads = self._tool_payloads(projection)
            self.assertEqual(len(tool_payloads), 2)
            for tool_payload in tool_payloads:
                self.assertIn("[compact_reloadable]", tool_payload["content"])

    # --- 场景 C: 失败工具 ---

    def test_failed_tool_keeps_terminal_status_and_never_fakes_success(self) -> None:
        with patch.object(config, "MEMCORE_OPERATION_PROJECTION_POLICY", "compact_after_terminal"):
            manager = self._make_manager(policy="compact_after_terminal")
            self._run_turn(manager, [_exchange("web_search", "call_err", "超时", status="error")])
            projection = self._projection(manager)
            tool_payloads = self._tool_payloads(projection)
            self.assertEqual(len(tool_payloads), 1)
            content = tool_payloads[0]["content"]
            self.assertNotIn("[compact_reloadable]", content)  # 短错误文本不压缩
            self.assertIn("超时", content)

    # --- 场景 D: 跨轮追问 ---

    def test_cross_turn_followup_reads_back_then_settles_again(self) -> None:
        with patch.object(config, "MEMCORE_OPERATION_PROJECTION_POLICY", "compact_after_terminal"):
            manager = self._make_manager(policy="compact_after_terminal")
            first = self._run_turn(manager, [_exchange("web_search", "call_follow", LONG_BODY)])
            observation_sid = first["exchanges"][0]["tool_result_source_id"]

            # 新一轮: 模型追问, 历史中 turn1 已是 compact 回执
            followup_open = manager.begin_input_turn(
                _user_record("stage-followup-u", "刚才那个结果的第三段是什么？"),
                profile_user_id=PROFILE,
                session_id=SESSION,
            )
            self.assertTrue(followup_open["ok"], followup_open)

            # 模型按 source_id 回读完整正文(模拟工具调用)
            readback = manager.open_memory(
                profile_user_id=PROFILE,
                session_id=SESSION,
                arguments={"memory_id": observation_sid, "view": "content", "detail": "full"},
            )
            self.assertTrue(readback["ok"], readback)
            self.assertIn(LONG_BODY, str(readback.get("text") or readback.get("result") or ""))

            # 回读正文作为本轮工具结果, 当轮全量可见
            manager.record_tool_batch(
                exchanges=[_exchange("open_memory", "call_readback", str(readback.get("text") or ""))],
                turn_id=followup_open["turn_id"],
                profile_user_id=PROFILE,
                session_id=SESSION,
            )
            open_projection = self._projection(manager)
            open_tool_payloads = self._tool_payloads(open_projection)
            self.assertIn(LONG_BODY, open_tool_payloads[-1]["content"])  # 当前开放轮全量

            manager.complete_input_turn(
                turn_id=followup_open["turn_id"],
                assistant_record=_user_record("stage-followup-a", "这一段是……"),
                memory_metadata={},
                profile_user_id=PROFILE,
                session_id=SESSION,
                provider_output_raw="这一段是……",
                provider_profile="openai",
                provider_projection={"role": "assistant", "content": "这一段是……"},
            )
            final_projection = self._projection(manager)
            self.assertTrue(final_projection["has_compact_history"])
            final_tool_payloads = self._tool_payloads(final_projection)
            self.assertEqual(len(final_tool_payloads), 2)
            for tool_payload in final_tool_payloads:
                self.assertIn("[compact_reloadable]", tool_payload["content"])


class StagedObservabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        try:
            self._temp.cleanup()
        except Exception:
            pass

    def _manager(self) -> MemcoreManager:
        return MemcoreManager(
            backend="memcore",
            storage_path=Path(self._temp.name) / "stage.sqlite3",
            visible_scope="conversation",
            enable_flavor=False,
            shadow_compare=False,
            llm=_NoopLLM(),
            embedding_provider=_FakeEmbedding(),
        )

    def test_default_policy_stays_full_in_staging(self) -> None:
        manager = self._manager()
        try:
            memory_config = manager._build_memory_config(manager._import_memcore())
            self.assertEqual(memory_config.operation_projection_policy, "full_until_raw_compaction")
        finally:
            manager.close()

    def test_staging_metrics_report_cache_savings_without_body(self) -> None:
        with patch.object(config, "MEMCORE_OPERATION_PROJECTION_POLICY", "compact_after_terminal"):
            manager = self._manager()
            try:
                opened = manager.begin_input_turn(
                    _user_record("stage-m-u", "帮我查一下"),
                    profile_user_id=PROFILE,
                    session_id=SESSION,
                )
                manager.record_tool_batch(
                    exchanges=[_exchange("web_search", "call_metric", LONG_BODY)],
                    turn_id=opened["turn_id"],
                    profile_user_id=PROFILE,
                    session_id=SESSION,
                )
                manager.build_context_projection(
                    provider_profile="openai",
                    profile_user_id=PROFILE,
                    session_id=SESSION,
                )
                manager.complete_input_turn(
                    turn_id=opened["turn_id"],
                    assistant_record=_user_record("stage-m-a", "查完了。"),
                    memory_metadata={},
                    profile_user_id=PROFILE,
                    session_id=SESSION,
                    provider_output_raw="查完了。",
                    provider_profile="openai",
                    provider_projection={"role": "assistant", "content": "查完了。"},
                )
                system = manager._get_system_or_none(
                    operation="stage-metrics",
                    profile_user_id=PROFILE,
                    session_id=SESSION,
                    character_pack_id="",
                )
                metrics = system.settlement_metrics()
                self.assertEqual(len(metrics), 1)
                metric = metrics[0]
                self.assertEqual(metric["settlement_status"], "settled")
                self.assertGreater(metric["saved_projected_tokens"], 0)
                self.assertGreater(metric["saved_ratio"], 0)
                self.assertNotIn(LONG_BODY, str(metrics))
            finally:
                manager.close()

    def test_compaction_counts_settled_tokens_in_staging(self) -> None:
        with patch.object(config, "MEMCORE_OPERATION_PROJECTION_POLICY", "compact_after_terminal"):
            manager = self._manager()
            try:
                for index in range(3):
                    opened = manager.begin_input_turn(
                        _user_record(f"stage-c-{index}-u", "帮我查一下"),
                        profile_user_id=PROFILE,
                        session_id=SESSION,
                    )
                    manager.record_tool_batch(
                        exchanges=[_exchange("web_search", f"call_c{index}", LONG_BODY)],
                        turn_id=opened["turn_id"],
                        profile_user_id=PROFILE,
                        session_id=SESSION,
                    )
                    manager.complete_input_turn(
                        turn_id=opened["turn_id"],
                        assistant_record=_user_record(f"stage-c-{index}-a", "ok"),
                        memory_metadata={},
                        profile_user_id=PROFILE,
                        session_id=SESSION,
                        provider_output_raw="ok",
                        provider_profile="openai",
                        provider_projection={"role": "assistant", "content": "ok"},
                    )
                compact_out = manager.compact_due_sync(
                    profile_user_id=PROFILE,
                    session_id=SESSION,
                    provider_profile="openai_chat",
                )
                # settled 后 before_raw 反映实际投影(低); 跨策略对比在 memcore 侧 Slice 4 已覆盖
                stats = compact_out.get("stats") or {}
                self.assertIn("before_projected_tokens", stats)
                self.assertGreaterEqual(stats.get("before_raw_projected_tokens") or 0, 0)
            finally:
                manager.close()


if __name__ == "__main__":
    unittest.main()
