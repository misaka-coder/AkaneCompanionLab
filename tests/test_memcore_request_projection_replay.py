"""Isolated provider-boundary regressions; no model or production storage."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from companion_v01.engine import AkaneMemoryEngine
from companion_v01.engine_services.response_projection import _build_memcore_provider_history
from companion_v01.memcore_integration.manager import MemcoreManager


class _NoModel:
    pass


class MemcoreRequestProjectionReplayTests(unittest.TestCase):
    scope = {"profile_user_id": "fixture-user", "session_id": "fixture-session", "character_pack_id": "fixture"}
    profile = "openai_chat"

    def setUp(self):
        self.temp = TemporaryDirectory(prefix="akane-projection-replay-")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "memcore.db"
        self.manager = self.new_manager()
        opened = self.manager.begin_input_turn(
            {"source_id": "fixture-input", "content": "fixture stimulus", "timestamp": 100},
            external_event={"event_type": "job.succeeded", "source": "host.jobs", "data": {
                "job_id": "fixture-job", "status": "succeeded", "origin_turn_id": "fixture-origin"}}, **self.scope)
        self.assertTrue(opened["ok"], opened)
        self.turn_id = opened["turn_id"]

    def new_manager(self):
        manager = MemcoreManager(backend="memcore", storage_path=self.path, visible_scope="conversation",
            enable_flavor=True, shadow_compare=False, llm=_NoModel(), embedding_provider=None,
            retrieval_mode="navigation")
        self.addCleanup(manager.close)
        self.assertTrue(manager.available, manager._reason)
        return manager

    def projection(self, turn_id=None):
        result = self.manager.build_context_projection(provider_profile=self.profile, **self.scope)
        self.assertTrue(result["ok"], result)
        return [item for item in result["messages"] if item["turn_id"] == (turn_id or self.turn_id)]

    def observe(self, descriptors=None, actual=None, turn_id=None, observer=None, audit_tail=None):
        descriptors = self.projection(turn_id) if descriptors is None else deepcopy(descriptors)
        actual = [deepcopy(item["payload"]) for item in descriptors] if actual is None else deepcopy(actual)
        context = {"memcore_projection_read": {
            "current_turn_id": turn_id or self.turn_id, "current_turn_messages": descriptors}}
        if observer is None:
            engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
            engine.memcore_manager = self.manager
            engine.llm = SimpleNamespace(supports_request_observer=True)
            observer = engine._build_memcore_request_observer(generation_context=context, **self.scope)
        result = observer({"protocol": self.profile, "persistent_turn_messages": actual,
            "history_messages": actual, "audit_history_messages": [*actual, *(audit_tail or [])],
            "system_prefix": "fixture stable system", "tool_schema": [], "model_route": {"model": "fixture"}})
        return result, context, observer

    def audits(self, turn_id=None):
        system = self.manager._get_system(**self.scope)
        return self.manager._store.list_projection_audits(namespace=system.namespace,
            turn_id=turn_id or self.turn_id, provider_profile=self.profile)

    def snapshot(self):
        return [(item["projection_index"], item["projection_status"], item["projection_version"],
            tuple(item["source_ids"]), deepcopy(item["payload"]), item["payload_hash"]) for item in self.projection()]

    def freeze(self):
        descriptors = self.projection()
        actual = [deepcopy(item["payload"]) for item in descriptors]
        actual[0]["content"] = "fixture provider-visible stimulus"
        result, context, observer = self.observe(descriptors, actual)
        self.assertEqual(result["status"], "recorded", context)
        return actual, observer

    def complete(self):
        result = self.manager.complete_input_turn(turn_id=self.turn_id,
            assistant_record={"source_id": "fixture-final", "content": "fixture final", "timestamp": 102},
            memory_metadata={}, provider_output_raw='{"speech":"fixture final"}', provider_profile=self.profile,
            provider_projection={"role": "assistant", "content": "fixture final"}, **self.scope)
        self.assertTrue(result["ok"], result)

    def test_first_freeze_and_same_observer_retry_record_actual_request(self):
        actual, observer = self.freeze()
        first = self.snapshot()
        result, _, _ = self.observe(actual=actual, observer=observer,
            audit_tail=[{"role": "user", "content": "request-local retry only"}])
        self.assertEqual(result["status"], "recorded")
        self.assertEqual(self.snapshot(), first)
        self.assertEqual([audit.attempt for audit in self.audits()], [1, 2])
        self.assertNotEqual(self.audits()[0].history_hash, self.audits()[1].history_hash)

    def test_completed_notification_same_shape_replay_does_not_refreeze_final(self):
        # Production shape: one request-frozen stimulus plus one complete final.
        self.freeze()
        self.complete()
        before = self.snapshot()
        audit_count = len(self.audits())
        result, context, _ = self.observe()
        self.assertEqual(result["status"], "skipped", context)
        self.assertFalse(result["recorded"])
        self.assertEqual(result["reason"], "projection_turn_not_open")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(len(self.audits()), audit_count)
        self.assertNotIn("memcore_request_projection_failure", context)

    def record(self, descriptors, actual=None, **kwargs):
        actual = [deepcopy(item["payload"]) for item in descriptors] if actual is None else actual
        return self.manager.record_request_projection(turn_id=self.turn_id, provider_profile=self.profile,
            turn_messages=descriptors, history_messages=actual, **self.scope, **kwargs)

    def append_tool(self):
        result = self.manager.record_tool_batch(turn_id=self.turn_id, exchanges=[{
            "tool_name": "project_inspect", "tool_call_id": "fixture-call", "tool_input": {"path": "fixture.py"},
            "result": "fixture result", "source": "fixture", "timestamp": 101,
            "source_id_prefix": "fixture-tool", "result_status": "success"}], **self.scope)
        self.assertTrue(result["ok"], result)

    def test_real_context_surface_replays_completed_notification(self):
        self.freeze()
        self.complete()
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        engine.memcore_manager = self.manager
        engine.llm = SimpleNamespace(supports_request_observer=True, chat_provider_protocol=lambda **_: "openai")
        with patch("config.MEMORY_BACKEND", "memcore"):
            projection = _build_memcore_provider_history(engine, current_source_id="fixture-input",
                chat_model_override="", **self.scope)
        self.assertTrue(projection["ok"], projection)
        self.assertEqual(len(projection["current_turn_messages"]), 2)
        result, context, _ = self.observe(projection["current_turn_messages"])
        self.assertEqual(result["status"], "skipped", context)
        self.assertEqual(context["memcore_request_projection"]["turn_status"], "closed")
        self.assertEqual(context["memcore_request_projection"]["recorded"], False)

    def test_open_turn_retry_after_manager_restore_freezes_only_new_suffix(self):
        self.freeze()
        frozen_user = self.snapshot()[0]
        self.append_tool()
        self.manager.close()
        self.manager = self.new_manager()
        restored = self.projection()
        self.assertEqual(len(restored), 3)
        result, _, _ = self.observe(restored)
        self.assertEqual(result["status"], "recorded")
        self.assertEqual(self.snapshot()[0], frozen_user)
        self.assertTrue(all(item[1] == "request_frozen" for item in self.snapshot()))
        self.assertEqual([audit.attempt for audit in self.audits()], [1, 2])

    def test_closed_turn_after_manager_restore_is_read_only(self):
        self.freeze()
        self.complete()
        before = self.snapshot()
        self.manager.close()
        self.manager = self.new_manager()
        result, _, _ = self.observe()
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(len(self.audits()), 1)

    def test_stop_before_first_freeze_then_restore_is_read_only(self):
        self.projection()
        aborted = self.manager.abort_input_turn(turn_id=self.turn_id, reason="user_stopped", **self.scope)
        self.assertTrue(aborted["ok"], aborted)
        before = self.snapshot()
        self.manager.close()
        self.manager = self.new_manager()
        result, context, _ = self.observe()
        self.assertEqual(result["status"], "skipped", context)
        self.assertEqual(result["turn_status"], "aborted")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(len(self.audits()), 0)

    def test_stop_with_unfrozen_tool_suffix_does_not_promote_it(self):
        self.freeze()
        self.append_tool()
        self.manager.abort_input_turn(turn_id=self.turn_id, reason="user_stopped", **self.scope)
        before = self.snapshot()
        self.assertTrue(any(item[1] != "request_frozen" for item in before))
        result, _, _ = self.observe()
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(len(self.audits()), 1)

    def test_cached_observer_after_completion_does_not_claim_new_audit(self):
        actual, observer = self.freeze()
        self.complete()
        before = self.snapshot()
        result, _, _ = self.observe(actual=actual, observer=observer)
        self.assertEqual(result["status"], "skipped")
        self.assertFalse(result["recorded"])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(len(self.audits()), 1)

    def test_multiple_requests_and_turns_keep_distinct_audits(self):
        self.freeze()
        self.complete()
        opened = self.manager.begin_input_turn(
            {"source_id": "fixture-input-2", "content": "second fixture", "timestamp": 103}, **self.scope)
        second_id = opened["turn_id"]
        second_messages = self.projection(second_id)
        for _ in range(2):
            result, _, _ = self.observe(second_messages, turn_id=second_id)
            self.assertEqual(result["status"], "recorded")
        for _ in range(2):
            result, _, _ = self.observe()
            self.assertEqual(result["status"], "skipped")
        self.assertEqual(len(self.audits()), 1)
        self.assertEqual([audit.attempt for audit in self.audits(second_id)], [1, 2])

    def test_changed_frozen_payload_is_still_a_real_immutable_conflict(self):
        self.freeze()
        before = self.snapshot()
        actual = [deepcopy(item["payload"]) for item in self.projection()]
        actual[0]["content"] = "illegal replacement of frozen input"
        result, context, _ = self.observe(actual=actual)
        self.assertEqual(result["status"], "degraded")
        self.assertEqual(context["memcore_request_projection_failure"]["detail"], "projection_immutable_conflict")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(len(self.audits()), 1)

    def test_terminal_payload_conflict_is_not_classified_as_replay(self):
        self.freeze()
        self.complete()
        before = self.snapshot()
        descriptors = self.projection()
        descriptors[-1]["payload"]["content"] = "illegal replacement of final"
        result = self.record(descriptors)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "projection_immutable_conflict")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(len(self.audits()), 1)

    def test_terminal_version_conflict_is_not_classified_as_replay(self):
        self.freeze()
        self.complete()
        before = self.snapshot()
        descriptors = self.projection()
        descriptors[0]["projection_version"] += 1
        result = self.record(descriptors)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "projection_immutable_conflict")
        self.assertEqual(self.snapshot(), before)

    def test_terminal_status_conflict_is_not_classified_as_replay(self):
        self.freeze()
        self.complete()
        descriptors = self.projection()
        descriptors[0]["projection_status"] = "settled"
        result = self.record(descriptors)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "projection_immutable_conflict")

    def test_terminal_actual_history_mismatch_is_not_classified_as_replay(self):
        self.freeze()
        self.complete()
        descriptors = self.projection()
        actual = [deepcopy(item["payload"]) for item in descriptors]
        actual[-1]["content"] = "different actual request"
        result = self.record(descriptors, actual)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "projection_actual_history_mismatch")

    def test_terminal_invalid_request_indexes_are_not_classified_as_replay(self):
        self.freeze()
        self.complete()
        descriptors = self.projection()
        for indexes in ([1, 0], [0, 0], [0], [0, 2]):
            with self.subTest(indexes=indexes):
                result = self.record(descriptors, history_message_indexes=indexes)
                self.assertFalse(result["ok"])
                self.assertEqual(result["reason"], "projection_actual_history_indexes_invalid")

    def test_terminal_source_mapping_conflict_is_not_classified_as_replay(self):
        self.freeze()
        self.complete()
        descriptors = self.projection()
        descriptors[-1]["source_ids"] = ["fixture-input"]
        result = self.record(descriptors)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "projection_source_already_mapped")

    def test_stop_winning_atomic_commit_race_verifies_before_skipping(self):
        descriptors = self.projection()
        system = self.manager._get_system(**self.scope)
        original = system.record_request_projection

        def stop_then_record(**kwargs):
            system.abort_turn(self.turn_id, reason="user_stopped")
            return original(**kwargs)

        with patch.object(system, "record_request_projection", side_effect=stop_then_record):
            result = self.record(descriptors)
        self.assertEqual(result["status"], "skipped", result)
        self.assertEqual(result["turn_status"], "aborted")
        self.assertFalse(result["recorded"])
        self.assertEqual(len(self.audits()), 0)

    def test_stop_commit_race_with_changed_payload_still_fails(self):
        descriptors = self.projection()
        before = self.snapshot()
        actual = [deepcopy(item["payload"]) for item in descriptors]
        actual[0]["content"] = "not the persisted terminal payload"
        descriptors[0]["payload"] = actual[0]
        system = self.manager._get_system(**self.scope)
        original = system.record_request_projection

        def stop_then_record(**kwargs):
            system.abort_turn(self.turn_id, reason="user_stopped")
            return original(**kwargs)

        with patch.object(system, "record_request_projection", side_effect=stop_then_record):
            result = self.record(descriptors, actual)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "projection_immutable_conflict")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(len(self.audits()), 0)

    def test_other_namespace_cannot_skip_an_owned_terminal_projection(self):
        self.freeze()
        self.complete()
        before = self.snapshot()
        descriptors = self.projection()
        other_scope = {**self.scope, "session_id": "other-session"}
        result = self.manager.record_request_projection(turn_id=self.turn_id, provider_profile=self.profile,
            turn_messages=descriptors, history_messages=[item["payload"] for item in descriptors], **other_scope)
        self.assertFalse(result["ok"], result)
        self.assertNotEqual(result["status"], "skipped")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(len(self.audits()), 1)

    def test_open_unfrozen_projection_version_conflict_is_not_overwritten(self):
        descriptors = self.projection()
        before = self.snapshot()
        descriptors[0]["projection_version"] += 1
        result = self.record(descriptors)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "projection_immutable_conflict")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(len(self.audits()), 0)


if __name__ == "__main__":
    unittest.main()
