"""Stored malformed legacy projection -> context binding -> actual SDK wire."""

import unittest

from memcore import ProjectionMessageInput
from tests import test_tool_exposure_wire as wire


class PoisonedHistoryWireTests(unittest.TestCase):
    setUp = wire.ToolExposureWireTests.setUp
    provider = wire.ToolExposureWireTests.provider
    begin = wire.ToolExposureWireTests.begin
    request = wire.ToolExposureWireTests.request
    finish = wire.ToolExposureWireTests.finish

    def test_stored_orphan_cannot_poison_repeated_requests_or_source_binding(self):
        self.provider("openai")
        self.begin(0, "Original legacy source remains stored.")
        system = self.manager._get_system_or_none(operation="test", **self.scope)
        old_turn = self.turn
        self.manager._store.save_turn_projections(namespace=system.namespace, turn_id=old_turn,
            projections=[ProjectionMessageInput(provider_profile="openai_chat",
                payload={"role": "tool", "tool_call_id": "missing-migrated-call", "content": "POISONED_PROJECTION"},
                source_ids=(self.source,), projection_index=0, projection_status="canonical_fallback")])
        self.finish()
        before = system.build_context_projection(provider_profile="openai_chat")
        self.assertTrue(any(m.payload.get("role") == "tool" for m in before.messages))
        for index in (1, 2):
            self.begin(index, f"Current user request {index}")
            prepared, payload = self.request(stream=index == 2)
            self.assertNotIn("POISONED_PROJECTION", str(payload))
            self.assertIn(f"Current user request {index}", str(payload))
            self.assertNotIn("memcore_request_projection_failure", prepared)
            self.assertFalse(self.last_result.error)
            self.finish()
        after = system.build_context_projection(provider_profile="openai_chat")
        self.assertTrue(any(m.turn_id == old_turn and m.payload.get("content") == "POISONED_PROJECTION"
                            for m in after.messages))


if __name__ == "__main__":
    unittest.main()
