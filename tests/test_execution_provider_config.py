from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from companion_v01.capability_registry import CapabilityRegistry, CapabilitySnapshot, ServerLocalOfferIndex
from companion_v01.client_protocol import ClientMode
from companion_v01.engine import AkaneMemoryEngine
from companion_v01.execution_local import TrustedLocalExecutor
from companion_v01.tool_handlers.execution import ExecRunToolHandler


def _desktop_snapshot(*, execution_enabled: bool) -> CapabilitySnapshot:
    return CapabilitySnapshot(client_mode=ClientMode.DESKTOP_PET, execution_enabled=execution_enabled)


class ExecCapabilityModuleTests(unittest.TestCase):
    def test_exec_tools_selected_when_enabled_in_desktop_mode(self) -> None:
        selection = CapabilityRegistry().select(_desktop_snapshot(execution_enabled=True))
        self.assertIn("exec_run", selection.tool_names)
        self.assertIn("exec_status", selection.schema_tool_names)
        self.assertIn("exec_cancel", selection.tool_names)

    def test_exec_tools_absent_when_disabled(self) -> None:
        selection = CapabilityRegistry().select(_desktop_snapshot(execution_enabled=False))
        self.assertNotIn("exec_run", selection.tool_names)
        self.assertNotIn("exec_run", selection.schema_tool_names)

    def test_exec_tools_absent_in_qq_mode(self) -> None:
        selection = CapabilityRegistry().select(
            CapabilitySnapshot(client_mode=ClientMode.QQ_TEXT, execution_enabled=True)
        )
        self.assertNotIn("exec_run", selection.tool_names)

    def test_provider_unavailable_stays_in_schema_with_unavailable_disclosure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "missing_workspace"  # never created -> availability disabled
            run_log_dir = Path(tmp) / "runlogs"
            provider = TrustedLocalExecutor(workspace_root=workspace, run_log_dir=run_log_dir)
            handler = ExecRunToolHandler(execution_provider=provider)
            index = ServerLocalOfferIndex()
            index.replace_handlers(
                {
                    "exec_run": handler,
                    "exec_status": ExecRunToolHandler(execution_provider=provider),
                    "exec_cancel": ExecRunToolHandler(execution_provider=provider),
                }
            )
            registry = CapabilityRegistry(server_offer_index=index)
            selection = registry.select(_desktop_snapshot(execution_enabled=True))
            # Schema keeps the tool (stable), but it is not executable and the
            # disclosure is a structured unavailable, not a removed tool.
            self.assertIn("exec_run", selection.schema_tool_names)
            self.assertNotIn("exec_run", selection.tool_names)
            states = {str(d.state).lower() for d in selection.disclosures}
            self.assertIn("unavailable", states)


class ExecCapabilityStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workspace = Path(self._tmp.name) / "workspace"
        self.workspace.mkdir()
        self.run_log_dir = Path(self._tmp.name) / "runlogs"

    def test_capability_status_ready_when_provider_available(self) -> None:
        provider = TrustedLocalExecutor(workspace_root=self.workspace, run_log_dir=self.run_log_dir)
        handler = ExecRunToolHandler(execution_provider=provider)
        status = handler.capability_status()
        self.assertIs(status["enabled"], True)
        self.assertIn(str(status["status"]).lower(), {"ready", "ok", "available"})

    def test_capability_status_unavailable_when_workspace_missing(self) -> None:
        missing = Path(self._tmp.name) / "missing"
        provider = TrustedLocalExecutor(workspace_root=missing, run_log_dir=self.run_log_dir)
        handler = ExecRunToolHandler(execution_provider=provider)
        status = handler.capability_status()
        self.assertIs(status["enabled"], False)
        self.assertEqual(status["status"], "unavailable")

    def test_capability_status_unavailable_when_provider_unconfigured(self) -> None:
        handler = ExecRunToolHandler(execution_provider=None)
        status = handler.capability_status()
        self.assertIs(status["enabled"], False)
        self.assertIn("unconfigured", status["reason"])


class ExecEngineProviderTests(unittest.TestCase):
    def test_provider_absent_when_execution_disabled(self) -> None:
        engine = object.__new__(AkaneMemoryEngine)
        with patch("companion_v01.engine.config.EXECUTION_ENABLED", False):
            provider = engine._build_execution_provider()
        self.assertIsNone(provider)

    def test_provider_built_and_cached_when_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = object.__new__(AkaneMemoryEngine)
            with patch("companion_v01.engine.config.EXECUTION_ENABLED", True), patch(
                "companion_v01.engine.config.EXECUTION_WORKSPACE_ROOT", str(Path(tmp) / "workspace")
            ), patch("companion_v01.engine.config.EXECUTION_RUN_LOG_DIR", str(Path(tmp) / "runlogs")), patch(
                "companion_v01.engine.config.EXECUTION_ALLOWED_ENV_NAMES", "PATH,COMSPEC"
            ):
                provider = engine._build_execution_provider()
            self.assertIsInstance(provider, TrustedLocalExecutor)
            self.assertEqual(engine._build_execution_provider(), provider)
            self.assertEqual(provider.allowed_env_names, {"PATH", "COMSPEC"})


if __name__ == "__main__":
    unittest.main()
