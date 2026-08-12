"""Phase 4 focused tests: log hygiene for the two evidence-backed defects.

  1. Vision observation failures must log the safe target id, never the host
     absolute path (real leak: full desktop path reached the log on 2026-07-13).
  2. tool_call_rejected must log the rejection detail so a repeated rejected
     call is diagnosable even when the legacy tool_call field carries no type
     (real case: 10 rejections with empty reason_tool in one group).
"""

from __future__ import annotations

import logging
import tempfile
import unittest
from pathlib import Path

from companion_v01.engine import AkaneMemoryEngine
from companion_v01.store import MemoryStore
from companion_v01.vision_service import VisionObservationService, VisionTarget


class VisionObservationLogHygieneTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def _target(self) -> VisionTarget:
        secret_path = self.root / "deep" / "inbox" / "2026-07-12" / "img_027__LONGNAME.png"
        secret_path.parent.mkdir(parents=True, exist_ok=True)
        secret_path.write_bytes(b"fake png")
        return VisionTarget(
            observation_type="attachment_image",
            target_id="attachment:img_027",
            source_path=secret_path,
            public_path="",
            resource_fingerprint="fp-1",
            prompt_version="v1",
            title="图片附件",
            hint_text="img_027",
        )

    def _service(self, *, fail: bool = True) -> VisionObservationService:
        store = MemoryStore(self.root / "db")

        def analyze(target):
            if fail:
                raise RuntimeError("upstream 502")
            return {"summary": "ok"}

        return VisionObservationService(
            base_dir=self.root,
            store=store,
            analyze_image_fn=analyze,
        )

    def test_failure_log_contains_target_id_but_not_absolute_path(self) -> None:
        service = self._service(fail=True)
        target = self._target()
        logger = logging.getLogger("akane.vision")
        with self.assertLogs(logger, level="WARNING") as captured:
            service._observe_target(target)
        joined = "\n".join(captured.output)
        self.assertIn("attachment:img_027", joined)
        self.assertIn("type=attachment_image", joined)
        self.assertNotIn(str(self.root), joined)
        self.assertNotIn("C:\\", joined)

    def test_success_path_logs_nothing_and_stores_ready(self) -> None:
        service = self._service(fail=False)
        target = self._target()
        logger = logging.getLogger("akane.vision")
        with self.assertNoLogs(logger, level="WARNING"):
            saved = service._observe_target(target)
        self.assertEqual(saved["status"], "ready")


class ToolCallRejectionLogHygieneTests(unittest.TestCase):
    def test_rejection_log_includes_detail_and_session(self) -> None:
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        followups: list[str] = []
        logger = logging.getLogger("akane.engine")
        with self.assertLogs(logger, level="WARNING") as captured:
            allow = engine._record_tool_call_rejection(
                final_output={"tool_call": {"type": ""}},
                rejection="工具 fetch_media_from_url 本轮不可用。\n请改用现有链接工具。",
                tool_followups=followups,
                session_id="qq_group_shared_302814983",
                tool_round_index=0,
                max_tool_rounds=4,
            )
        joined = "\n".join(captured.output)
        self.assertIn("qq_group_shared_302814983", joined)
        self.assertIn("fetch_media_from_url", joined)
        self.assertIn("reason_tool=", joined)
        self.assertEqual(followups, ["工具 fetch_media_from_url 本轮不可用。\n请改用现有链接工具。"])
        self.assertTrue(allow)

    def test_rejection_log_marks_unknown_when_detail_empty(self) -> None:
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        logger = logging.getLogger("akane.engine")
        with self.assertLogs(logger, level="WARNING") as captured:
            engine._record_tool_call_rejection(
                final_output={},
                rejection="",
                tool_followups=[],
                session_id="qq_group_shared_302814983",
                tool_round_index=2,
                max_tool_rounds=4,
            )
        joined = "\n".join(captured.output)
        self.assertIn("rejected call carried no legacy type", joined)
        self.assertNotIn("C:\\", joined)


if __name__ == "__main__":
    unittest.main()
