from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import config

from companion_v01.capability_registry import CapabilityRegistry, CapabilitySnapshot, ServerLocalOfferIndex
from companion_v01.client_protocol import ClientMode
from companion_v01.engine import AkaneMemoryEngine
from companion_v01.execution_local import TrustedLocalExecutor
from companion_v01.native_tool_schema import build_openai_native_tool_specs
from companion_v01.tool_handlers.execution import (
    ExecCancelToolHandler,
    ExecRunToolHandler,
    ExecStatusToolHandler,
)
from companion_v01.engine_services.tool_rounds import build_capability_snapshot


def _desktop_snapshot(*, execution_enabled: bool) -> CapabilitySnapshot:
    return CapabilitySnapshot(client_mode=ClientMode.DESKTOP_PET, execution_enabled=execution_enabled)


def _qq_snapshot(*, execution_enabled: bool, execution_qq_enabled: bool = False) -> CapabilitySnapshot:
    return CapabilitySnapshot(
        client_mode=ClientMode.QQ_TEXT,
        execution_enabled=execution_enabled,
        execution_qq_enabled=execution_qq_enabled,
    )


class ExecCapabilityModuleTests(unittest.TestCase):
    def test_runtime_config_exports_qq_execution_gate(self) -> None:
        self.assertTrue(hasattr(config, "EXECUTION_QQ_ENABLED"))
        self.assertEqual(config.EXECUTION_QQ_ENABLED, config.settings.EXECUTION_QQ_ENABLED)

    def test_real_snapshot_only_enables_qq_execution_for_master_profile(self) -> None:
        class _Store:
            @staticmethod
            def list_attachment_inbox_items(**_kwargs):
                return []

            @staticmethod
            def list_generated_files(**_kwargs):
                return []

        engine = SimpleNamespace(store=_Store(), execution_provider=object(), tool_handlers={})
        context = SimpleNamespace(effective_mode=ClientMode.QQ_TEXT)
        with patch("companion_v01.engine_services.tool_rounds._host_config.EXECUTION_QQ_ENABLED", True):
            master = build_capability_snapshot(
                engine,
                client_context=context,
                profile_user_id="master",
                session_id="master-session",
            )
            ordinary = build_capability_snapshot(
                engine,
                client_context=context,
                profile_user_id="qq_pri_123",
                session_id="ordinary-session",
            )
            group = build_capability_snapshot(
                engine,
                client_context=context,
                profile_user_id="qq_group_shared_456",
                session_id="group-session",
            )
        self.assertTrue(master.execution_qq_enabled)
        self.assertFalse(ordinary.execution_qq_enabled)
        self.assertFalse(group.execution_qq_enabled)

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
        self.assertNotIn("exec_run", selection.schema_tool_names)

    def test_exec_tools_absent_in_qq_mode_without_host_gate(self) -> None:
        # EXECUTION_QQ_ENABLED=false must keep QQ free of any exec tool/placeholder,
        # even when the provider is present.
        selection = CapabilityRegistry().select(_qq_snapshot(execution_enabled=True))
        self.assertNotIn("exec_run", selection.tool_names)
        self.assertNotIn("exec_run", selection.schema_tool_names)
        self.assertNotIn("exec_status", selection.schema_tool_names)
        self.assertNotIn("exec_cancel", selection.schema_tool_names)
        exec_disclosures = [d for d in selection.disclosures if "exec_run" in d.tool_names]
        self.assertEqual(exec_disclosures, [])
        self.assertFalse(any("exec_run" in hint for hint in selection.light_hints))

    def test_exec_tools_selected_for_qq_master_when_host_gate_on(self) -> None:
        selection = CapabilityRegistry().select(_qq_snapshot(execution_enabled=True, execution_qq_enabled=True))
        self.assertIn("exec_run", selection.tool_names)
        self.assertIn("exec_status", selection.schema_tool_names)
        self.assertIn("exec_cancel", selection.tool_names)
        qq_hints = [hint for hint in selection.light_hints if "exec_run" in hint]
        self.assertTrue(qq_hints)
        self.assertIn("QQ 主账号", qq_hints[0])
        self.assertIn("QQ Bot 后端所在机器", qq_hints[0])

    def test_exec_tools_absent_for_qq_master_when_execution_disabled(self) -> None:
        selection = CapabilityRegistry().select(_qq_snapshot(execution_enabled=False, execution_qq_enabled=False))
        self.assertNotIn("exec_run", selection.schema_tool_names)

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

    def test_default_workspace_is_auto_created_when_enabled_without_explicit_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp) / "data"
            engine = object.__new__(AkaneMemoryEngine)
            with patch("companion_v01.engine.config.EXECUTION_ENABLED", True), patch(
                "companion_v01.engine.config.EXECUTION_WORKSPACE_ROOT", ""
            ), patch("companion_v01.engine.config.EXECUTION_RUN_LOG_DIR", ""), patch(
                "companion_v01.engine.config.DATA_ROOT", str(data_root)
            ), patch("companion_v01.engine.config.STATE_DIR", str(Path(tmp) / "state")):
                provider = engine._build_execution_provider()
            self.assertIsInstance(provider, TrustedLocalExecutor)
            self.assertTrue((data_root / "execution_workspace").is_dir())
            availability = provider.availability()
            self.assertTrue(availability.enabled)
            self.assertEqual(str(availability.status).lower(), "ready")

    def test_disabled_execution_never_creates_default_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp) / "data"
            engine = object.__new__(AkaneMemoryEngine)
            with patch("companion_v01.engine.config.EXECUTION_ENABLED", False), patch(
                "companion_v01.engine.config.EXECUTION_WORKSPACE_ROOT", ""
            ), patch("companion_v01.engine.config.DATA_ROOT", str(data_root)):
                provider = engine._build_execution_provider()
            self.assertIsNone(provider)
            self.assertFalse((data_root / "execution_workspace").exists())

    def test_explicit_missing_workspace_is_never_auto_created(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            explicit = Path(tmp) / "typo_workspace"
            engine = object.__new__(AkaneMemoryEngine)
            with patch("companion_v01.engine.config.EXECUTION_ENABLED", True), patch(
                "companion_v01.engine.config.EXECUTION_WORKSPACE_ROOT", str(explicit)
            ), patch("companion_v01.engine.config.EXECUTION_RUN_LOG_DIR", ""), patch(
                "companion_v01.engine.config.DATA_ROOT", str(Path(tmp) / "data")
            ), patch("companion_v01.engine.config.STATE_DIR", str(Path(tmp) / "state")):
                provider = engine._build_execution_provider()
            self.assertIsInstance(provider, TrustedLocalExecutor)
            self.assertFalse(explicit.exists())
            availability = provider.availability()
            self.assertFalse(availability.enabled)
            self.assertEqual(availability.reason, "workspace_missing")

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

    def _native_schema_dump(self, provider) -> str:
        handlers = {
            "exec_run": ExecRunToolHandler(execution_provider=provider),
            "exec_status": ExecStatusToolHandler(execution_provider=provider),
            "exec_cancel": ExecCancelToolHandler(execution_provider=provider),
        }
        specs = build_openai_native_tool_specs(handlers, allowed_tool_names=set(handlers))
        return json.dumps(
            [dict(spec) for spec in specs],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def test_native_exec_schema_is_byte_stable_across_provider_readiness(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing_ws"  # never created -> unavailable
            ready = Path(tmp) / "ready_ws"
            ready.mkdir()
            unavailable_provider = TrustedLocalExecutor(workspace_root=missing, run_log_dir=Path(tmp) / "rl1")
            ready_provider = TrustedLocalExecutor(workspace_root=ready, run_log_dir=Path(tmp) / "rl2")
            self.assertFalse(unavailable_provider.availability().enabled)
            self.assertTrue(ready_provider.availability().enabled)
            self.assertEqual(
                self._native_schema_dump(unavailable_provider),
                self._native_schema_dump(ready_provider),
            )
            names = {spec["function"]["name"] for spec in json.loads(self._native_schema_dump(ready_provider))}
            self.assertEqual(names, {"exec_run", "exec_status", "exec_cancel"})

    def test_native_exec_schema_is_deterministic_across_repeated_rounds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ready = Path(tmp) / "ready_ws"
            ready.mkdir()
            provider = TrustedLocalExecutor(workspace_root=ready, run_log_dir=Path(tmp) / "rl")
            first = self._native_schema_dump(provider)
            for _ in range(3):
                self.assertEqual(self._native_schema_dump(provider), first)

    def test_pure_text_request_has_no_exec_placeholder_when_disabled(self) -> None:
        """Disabled execution must be absent from both tools and model-visible hints."""
        registry = CapabilityRegistry()
        snapshot = _desktop_snapshot(execution_enabled=False)
        selection = registry.select(snapshot)
        baseline_without_execution_module = CapabilityRegistry(
            modules=tuple(module for module in registry.modules if module.name != "execution")
        ).select(snapshot)
        self.assertEqual(selection, baseline_without_execution_module)
        self.assertNotIn("exec_run", selection.schema_tool_names)
        self.assertNotIn("exec_run", selection.tool_names)
        self.assertNotIn("exec_status", selection.tool_names)
        self.assertNotIn("exec_cancel", selection.tool_names)
        exec_disclosures = [d for d in selection.disclosures if "exec_run" in d.tool_names]
        self.assertEqual(exec_disclosures, [])
        self.assertFalse(any("exec_run" in hint for hint in selection.light_hints))

        engine = object.__new__(AkaneMemoryEngine)
        engine._resolve_tool_handlers = lambda **_kwargs: {}
        prompt_context = engine._build_tool_prompt_context(
            allow_tool_call=True,
            capability_selection=selection,
        )
        self.assertNotIn("exec_run", prompt_context)
        self.assertNotIn("exec_status", prompt_context)
        self.assertNotIn("exec_cancel", prompt_context)
        self.assertNotIn("本机命令执行", prompt_context)
        self.assertNotIn("宿主在本机启用", prompt_context)



if __name__ == "__main__":
    unittest.main()
