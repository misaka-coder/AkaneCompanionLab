"""Fast request worksets keep transaction isolation and live tool authority."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from companion_v01.care_runtime import CareRuntimeStore
from companion_v01.care_state_migration import SCHEMA_VERSION
from companion_v01 import capability_exposure
from tests import test_tool_exposure_wire


class CareCommittedWorksetTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "care.json"
        self.store = CareRuntimeStore(self.path)
        self.state = {"schema_version": SCHEMA_VERSION, "bodies": {"cecilia": {
            "master": {"energy": 67, "hunger": 55, "vitals_updated_at_ms": 1000}}},
            "care_events": [{"event_id": "turn:one", "timestamp_ms": 1000}],
            "accounts": {"cecilia": {"master": {"qq_coins": 23,
                "inventory": {"tea": {"count": 2, "name": "お茶\n茶", "effects": {"hunger": 20}}}}}}}

    def test_committed_and_loaded_trees_are_isolated_and_round_trip_unknown_fields(self):
        expected = json.loads(json.dumps(self.state, ensure_ascii=False))
        self.store._save(self.state)
        self.state["accounts"]["cecilia"]["master"]["inventory"]["tea"]["count"] = 100
        loaded = self.store._load()
        loaded["care_events"][0]["event_id"] = "uncommitted"
        loaded["bodies"]["cecilia"]["master"]["energy"] = 0
        self.assertEqual(self.store._load(), expected)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), expected)
        self.assertEqual(CareRuntimeStore(self.path)._load(), expected)

    def test_failed_save_does_not_publish_memory_json_or_a_partial_file(self):
        self.store._save(self.state)
        committed = self.path.read_bytes()
        original = self.store._load()
        candidate = self.store._load()
        candidate["care_events"].append({"event_id": "turn:failed"})
        for method in ("write_text", "replace"):
            with self.subTest(method=method), patch.object(Path, method, side_effect=OSError("synthetic disk failure")):
                with self.assertRaises(OSError):
                    self.store._save(candidate)
            self.assertEqual(self.path.read_bytes(), committed)
            self.assertEqual(self.store._load(), original)
            self.assertEqual(list(self.path.parent.glob("*.tmp")), [])
        self.store._save(candidate)
        self.assertEqual(CareRuntimeStore(self.path)._load(), candidate)

    def test_serialization_failure_leaves_last_commit_retryable(self):
        self.store._save(self.state)
        committed = self.path.read_bytes()
        candidate = self.store._load()
        candidate["unsupported"] = object()
        with self.assertRaises(TypeError):
            self.store._save(candidate)
        self.assertEqual(self.path.read_bytes(), committed)
        self.assertEqual(self.store._load(), self.state)

    def test_first_load_baseline_reset_is_preserved_in_each_isolated_view(self):
        self.path.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")
        store = CareRuntimeStore(self.path, reset_baseline_on_first_load=True)
        with patch("companion_v01.care_persistence.time.time", return_value=5):
            first = store._load()
        self.assertEqual(first["bodies"]["cecilia"]["master"]["vitals_updated_at_ms"], 5000)
        first["bodies"]["cecilia"]["master"]["vitals_updated_at_ms"] = 0
        self.assertEqual(store._load()["bodies"]["cecilia"]["master"]["vitals_updated_at_ms"], 5000)


class ToolExposureWorksetTests(unittest.TestCase):
    def fixture(self):
        fixture = test_tool_exposure_wire.ToolExposureWireTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        return fixture

    def test_ordinary_provider_request_freezes_authority_once_and_keeps_real_schemas(self):
        fixture = self.fixture()
        fixture.provider("openai")
        fixture.begin(1)
        with patch.object(capability_exposure, "freeze_for_projection",
            wraps=capability_exposure.freeze_for_projection) as freeze:
            prepared, payload = fixture.request()
        self.assertEqual(freeze.call_count, 1)
        self.assertTrue(payload["tools"])
        self.assertIn("demo.alpha", prepared["_tool_capability_selection"].schema_tool_names)

    def test_compaction_still_reads_changed_provider_preferences_and_tool_authority(self):
        fixture = self.fixture()
        with patch.object(capability_exposure, "freeze_for_projection",
            wraps=capability_exposure.freeze_for_projection) as freeze:
            fixture.test_compaction_in_prepare_reads_final_provider_and_preference_state()
        self.assertEqual(freeze.call_count, 3, "Initial request, next request, and real compaction refresh")


if __name__ == "__main__":
    unittest.main()
