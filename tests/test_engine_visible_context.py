from __future__ import annotations

import unittest

from companion_v01.engine import AkaneMemoryEngine


class EngineVisibleContextExclusionTests(unittest.TestCase):
    def test_collect_visible_context_source_ids_excludes_only_directly_visible_records(self) -> None:
        visible_ids = AkaneMemoryEngine._collect_visible_context_source_ids(
            recent_raw=[
                {"source_id": "raw-1"},
                {"source_id": "current-user"},
                {"source_id": "raw-1"},
            ],
            recent_episodic_summaries=[
                {
                    "summary_id": "summary-1",
                    "source_id": "summary-1",
                    "source_ids": ["raw-summarized-1", "raw-summarized-2"],
                },
                {
                    "summary_id": "summary-2",
                    "source_ids": ["raw-summarized-3"],
                },
            ],
            recent_semantic_summaries=[
                {
                    "semantic_id": "semantic-1",
                    "source_id": "semantic-1",
                    "source_summary_ids": ["summary-semanticized-1"],
                },
                {
                    "semantic_id": "semantic-2",
                    "source_summary_ids": ["summary-semanticized-2"],
                },
            ],
            extra_source_ids=["current-user", "", "manual-extra"],
        )

        self.assertEqual(
            visible_ids,
            [
                "current-user",
                "manual-extra",
                "raw-1",
                "summary-1",
                "summary-2",
                "semantic-1",
                "semantic-2",
            ],
        )
        self.assertNotIn("raw-summarized-1", visible_ids)
        self.assertNotIn("raw-summarized-2", visible_ids)
        self.assertNotIn("raw-summarized-3", visible_ids)
        self.assertNotIn("summary-semanticized-1", visible_ids)
        self.assertNotIn("summary-semanticized-2", visible_ids)


if __name__ == "__main__":
    unittest.main()
