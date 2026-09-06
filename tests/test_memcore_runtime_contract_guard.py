from __future__ import annotations

import unittest
from unittest.mock import patch

from scripts.check_memcore_runtime_contract import check_runtime


class MemcoreRuntimeContractGuardTests(unittest.TestCase):
    def test_installed_v6_preserves_plain_and_json_authorship(self) -> None:
        import memcore

        if memcore.PROJECTION_VERSION < 6:
            self.skipTest("run this acceptance case with the V6 release candidate")
        self.assertEqual(check_runtime()["status"], "ok")
        self.assertEqual(check_runtime()["authorship_cases_checked"], 6)

    def test_v4_is_rejected_even_when_action_only_completion_exists(self) -> None:
        with patch("memcore.PROJECTION_VERSION", 4):
            self.assertEqual(check_runtime()["reason"], "chat_authorship_v6_required")

    def test_version_number_cannot_hide_timestamp_injection(self) -> None:
        def polluted(_adapter, entry, *, provider_profile):
            return {"role": "assistant", "content": "[time] Assistant: " + entry.payload["provider_output_raw"]}

        with (
            patch("memcore.PROJECTION_VERSION", 6),
            patch("memcore.projection.ProjectionAdapter.context_surface_payload", polluted),
        ):
            self.assertEqual(check_runtime()["reason"], "assistant_authorship_changed")


if __name__ == "__main__":
    unittest.main()
