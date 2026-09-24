import os
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from pydantic import ValidationError

import config
from companion_v01.persona_config import load_persona_config
from companion_v01.prompt_builder import PromptBuilder
from companion_v01.settings_catalog import build_settings_catalog
from companion_v01.tool_batch import execute_tool_batch
from companion_v01.tool_handlers.core import ToolExecutionResult, ToolMetadata
from tests.test_prompt_builder import _build_minimal_final


class ToolParallelConfigTests(unittest.TestCase):
    def test_environment_and_bounds_are_validated(self):
        self.assertEqual(config.Settings.model_fields["TOOL_MAX_PARALLEL"].default, 10)
        with patch.dict(os.environ, {"TOOL_MAX_PARALLEL": "7"}):
            self.assertEqual(config.Settings(_env_file=None).TOOL_MAX_PARALLEL, 7)
        for value in (0, -1, 65, "invalid"):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                config.Settings(_env_file=None, TOOL_MAX_PARALLEL=value)

    def test_actual_concurrency_and_model_prompt_use_same_configured_value(self):
        builder = PromptBuilder(load_persona_config())
        for width in (1, 2, 10):
            barrier = threading.Barrier(width)
            lock = threading.Lock()
            active = peak = 0

            def execute(call):
                nonlocal active, peak
                with lock:
                    active += 1
                    peak = max(peak, active)
                barrier.wait(timeout=5)
                with lock:
                    active -= 1
                return ToolExecutionResult(tool_type="read", followup_context=str(call["id"]))

            with self.subTest(width=width), patch.object(config, "TOOL_MAX_PARALLEL", width):
                calls = [{"type": "read", "id": index} for index in range(width * 2)]
                results = execute_tool_batch(
                    calls,
                    execute=execute,
                    handler_for=lambda call: SimpleNamespace(tool_metadata=lambda: ToolMetadata(operation="read")),
                )
                self.assertEqual(peak, width)
                self.assertEqual([r.followup_context for r in results], [str(index) for index in range(width * 2)])
                prompt = _build_minimal_final(builder)["system_prompt"]
                self.assertIn(f"每批最多同时执行 {width} 个", prompt)
                self.assertEqual(prompt.count("【工具批执行并发】"), 1)
                self.assertIn("超额会排队", prompt)
                self.assertIn("不是工具轮数上限", prompt)

    def test_catalog_surfaces_configuration_without_claiming_live_editability(self):
        entry = next(
            item
            for category in build_settings_catalog()["categories"]
            for item in category["settings"]
            if item["key"] == "TOOL_MAX_PARALLEL"
        )
        self.assertEqual(entry["default"], 10)
        self.assertEqual(entry["scope"], "restart")
        self.assertFalse(entry["editable"])
