from __future__ import annotations

import unittest

from companion_v01.tool_runtime import RetrieveMemoryToolHandler


class RetrieveMemoryToolHandlerTests(unittest.TestCase):
    def test_normalize_call_accepts_precision_filters_and_preserves_zero_importance(self) -> None:
        handler = RetrieveMemoryToolHandler(retrieve_fn=lambda **kwargs: None)

        call = handler.normalize_call(
            {
                "type": "retrieve_memory",
                "query": "我喜欢喝什么饮料",
                "keywords": "喜欢，可乐 饮料",
                "source_layers": ["raw", "semantic", "bad_layer"],
                "subject_scopes": ["用户", "other", "bad_scope"],
                "categories": "偏好,项目,bad_category",
                "importance_min": 0,
                "limit": 99,
            }
        )

        self.assertIsNotNone(call)
        assert call is not None
        self.assertEqual(call["source_layers"], ["raw", "semantic_summary"])
        self.assertEqual(call["subject_scopes"], ["user", "other"])
        self.assertEqual(call["categories"], ["preference", "project_work"])
        self.assertEqual(call["importance_min"], 0.0)
        self.assertEqual(call["limit"], 12)


if __name__ == "__main__":
    unittest.main()
