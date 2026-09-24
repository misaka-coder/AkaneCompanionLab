from __future__ import annotations

import asyncio
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from capcore import CapabilityResult, InvocationContext

from companion_v01.capability_approval import CapabilityApprovalStore
from companion_v01.capability_registry import CapabilityRegistry, CapabilitySnapshot
from companion_v01.client_protocol import ClientMode
from companion_v01.extension_management import ExtensionManagementService, PluginSelectionStore
from companion_v01.instance_profile import PluginSelection
from companion_v01.plugin_installation import PluginInstallationError
from companion_v01.tool_handlers.core import ToolExecutionContext
from companion_v01.tool_handlers.extensions import ManageExtensionToolHandler


PLUGIN_ID = "akane.test.extension"


class PluginSelectionStoreTests(unittest.TestCase):
    def test_atomic_overlay_roundtrip_and_default_merge(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "plugin-selections.json"
            defaults = (
                PluginSelection(PLUGIN_ID, True),
                PluginSelection("akane.test.second", False),
            )
            store = PluginSelectionStore(path, defaults=defaults, instance_id="bot-a")

            store.save(
                (
                    PluginSelection(PLUGIN_ID, False),
                    PluginSelection("akane.test.second", False),
                )
            )

            self.assertEqual(store.load(), (PluginSelection(PLUGIN_ID, False), PluginSelection("akane.test.second", False)))
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["overrides"], {PLUGIN_ID: False})
            self.assertFalse(list(path.parent.glob("*.tmp")))

    def test_invalid_overlay_is_observable_and_falls_back_to_declared_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "plugin-selections.json"
            path.write_text("not-json", encoding="utf-8")
            defaults = (PluginSelection(PLUGIN_ID, True),)
            store = PluginSelectionStore(path, defaults=defaults, instance_id="bot-a")

            self.assertEqual(store.load(), defaults)
            self.assertEqual(store.load_reason, "plugin_selection_state_invalid")

    def test_management_has_no_default_total_duration_limit(self) -> None:
        service = ExtensionManagementService(
            plugin_runtime=Mock(),
            selection_store=Mock(),
        )

        self.assertIsNone(service.sync_timeout_seconds)


class ExtensionManagementPublicSnapshotTests(unittest.TestCase):
    def test_disabled_managed_plugin_keeps_declared_surfaces_without_internal_artifact_fields(self) -> None:
        plugin_runtime = SimpleNamespace(
            code_reload_mode="atomic_generation_switch",
            status_snapshot=Mock(return_value={
                "status": "active",
                "reason": "",
                "generation": 9,
                "plugins": [{
                    "plugin_id": PLUGIN_ID,
                    "enabled": False,
                    "status": "disabled",
                }],
            }),
        )
        artifact_store = Mock()
        artifact_store.snapshot.return_value = {
            "plugins": [{
                "plugin_id": PLUGIN_ID,
                "version": "1.2.3",
                "digest": "must-not-be-public",
                "permissions": ["agent.turn.request"],
                "contribution_snapshot": {
                    "generation": 4,
                    "surfaces": ["desktop", "qq", "unknown"],
                    "capabilities": ["akane.test.extension.schedule"],
                    "background_services": ["scheduler"],
                },
                "last_good_digest": "also-private",
                "pending_activation": False,
            }],
            "stages": [{"stage_id": "private-stage"}],
        }
        service = ExtensionManagementService(
            plugin_runtime=plugin_runtime,
            selection_store=Mock(load_reason="defaults"),
            artifact_store=artifact_store,
        )

        public = service.public_snapshot()

        self.assertEqual(public["plugin_count"], 1)
        plugin = public["plugins"][0]
        self.assertEqual(plugin["surfaces"], ["desktop", "qq"])
        self.assertEqual(plugin["contributions"]["background_services"], ["scheduler"])
        self.assertTrue(plugin["declared_only"])
        self.assertTrue(plugin["rollback_available"])
        serialized = json.dumps(public)
        self.assertNotIn("must-not-be-public", serialized)
        self.assertNotIn("private-stage", serialized)


class ExtensionManagementLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_schema_failure_details_reach_the_existing_management_tool_result(self):
        from companion_v01.tool_handlers.extensions import _result

        errors = [{"code": "invalid_schema", "field": "properties.formatting.minLength", "message": "invalid JSON Schema declaration"}]
        diagnostics = [{"stage": "dependency_preflight", "code": "dependency_source_required", "package": "dep-fixture", "diagnostic_id": "plugin-diag-test"}]
        artifact_store = Mock()
        artifact_store.stage_source.side_effect = PluginInstallationError(
            "invalid_schema", schema_errors=errors, diagnostics=diagnostics
        )
        service = ExtensionManagementService(plugin_runtime=Mock(), selection_store=Mock(), artifact_store=artifact_store)
        response = await service.stage_source(source_path="C:/work/plugin")
        self.assertFalse(response["ok"])
        self.assertEqual(response["schema_errors"], errors)
        self.assertEqual(response["diagnostics"], diagnostics)
        model_result = _result(response)
        self.assertEqual(json.loads(model_result.followup_context)["schema_errors"], errors)
        self.assertTrue(model_result.followup_envelope.complete)

    async def test_cancelled_wheel_stage_drains_and_discards_candidate(self) -> None:
        started = threading.Event()
        release = threading.Event()
        artifact_store = Mock()

        def stage(_path):
            started.set()
            release.wait(5)
            return {"ok": True, "status": "staged", "stage_id": "a" * 32}

        artifact_store.stage_wheel.side_effect = stage
        service = ExtensionManagementService(
            plugin_runtime=Mock(),
            selection_store=Mock(),
            artifact_store=artifact_store,
        )

        task = asyncio.create_task(service.stage_wheel(wheel_path="C:/work/plugin.whl"))
        self.assertTrue(await asyncio.to_thread(started.wait, 2))
        task.cancel()
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        artifact_store.discard_stage.assert_called_once_with("a" * 32)

    async def test_diagnostics_invoke_uses_the_same_runtime_facade(self) -> None:
        expected = CapabilityResult(is_error=False, content={"value": "ready"})
        plugin_runtime = SimpleNamespace(invoke=AsyncMock(return_value=expected))
        service = ExtensionManagementService(
            plugin_runtime=plugin_runtime,
            selection_store=Mock(),
        )
        context = InvocationContext(client_mode="plugin_admin")

        result = await service.invoke_capability(
            "akane.test.extension.inspect.v1",
            {"scope": "active"},
            context=context,
        )

        self.assertIs(result, expected)
        plugin_runtime.invoke.assert_awaited_once_with(
            "akane.test.extension.inspect.v1",
            {"scope": "active"},
            context=context,
        )

    async def test_generation_runtime_reloads_pending_code(self) -> None:
        plugin_runtime = SimpleNamespace(
            code_reload_mode="atomic_generation_switch",
            selections=(PluginSelection(PLUGIN_ID, True),),
            reconfigure=AsyncMock(
                return_value={
                    "ok": True,
                    "status": "active",
                    "published": True,
                    "plugins": [{"plugin_id": PLUGIN_ID, "status": "active"}],
                }
            ),
        )
        artifact_store = Mock()
        artifact_store.reconcile_runtime.return_value = {
            "status": "ready",
            "reload_required": False,
        }
        selection_store = Mock()
        selection_store.load.return_value = (PluginSelection(PLUGIN_ID, True),)
        service = ExtensionManagementService(
            plugin_runtime=plugin_runtime,
            selection_store=selection_store,
            artifact_store=artifact_store,
        )

        result = await service.restart(requested_plugin_id=PLUGIN_ID)

        self.assertTrue(result["published"])
        self.assertEqual(result["artifact_status"]["status"], "ready")
        plugin_runtime.reconfigure.assert_awaited_once_with(
            (PluginSelection(PLUGIN_ID, True),)
        )

    async def test_rejected_generation_candidate_does_not_trigger_redundant_rollback(self) -> None:
        previous = (PluginSelection(PLUGIN_ID, False),)
        plugin_runtime = SimpleNamespace(
            selections=previous,
            reconfigure=AsyncMock(
                return_value={
                    "ok": False,
                    "status": "candidate_rejected",
                    "reason": "plugin_probe_failed",
                    "published": False,
                    "plugins": [
                        {
                            "plugin_id": PLUGIN_ID,
                            "status": "unavailable",
                            "reason": "plugin_probe_failed",
                        }
                    ],
                }
            ),
        )
        selection_store = Mock()
        selection_store.load.return_value = previous
        service = ExtensionManagementService(
            plugin_runtime=plugin_runtime,
            selection_store=selection_store,
        )

        result = await service.set_enabled(plugin_id=PLUGIN_ID, enabled=True)

        self.assertEqual(result["status"], "activation_failed")
        self.assertEqual(result["reason"], "plugin_probe_failed")
        self.assertEqual(result["rollback_status"], "not_required")
        plugin_runtime.reconfigure.assert_awaited_once()

    async def test_install_stage_publishes_and_activates_new_plugin_as_one_operation(self) -> None:
        plugin_runtime = SimpleNamespace(
            selections=(),
            reconfigure=AsyncMock(
                return_value={
                    "ok": True,
                    "status": "active",
                    "published": True,
                    "plugins": [{"plugin_id": PLUGIN_ID, "enabled": True, "status": "active"}],
                }
            ),
        )
        selection_store = Mock()
        selection_store.load.return_value = ()
        artifact_store = Mock()
        artifact_store.publish_stage.return_value = {
            "ok": True,
            "status": "installed",
            "plugin_id": PLUGIN_ID,
            "version": "1.0.0",
            "digest": "a" * 64,
            "permissions": ["storage.write"],
            "unchanged": False,
        }
        artifact_store.reconcile_runtime.return_value = {
            "status": "ready",
            "reload_required": False,
        }
        service = ExtensionManagementService(
            plugin_runtime=plugin_runtime,
            selection_store=selection_store,
            artifact_store=artifact_store,
        )

        result = await service.install_stage(
            stage_id="stage-1",
            approved_permissions=("storage.write",),
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "active")
        candidate = (PluginSelection(PLUGIN_ID, True),)
        plugin_runtime.reconfigure.assert_awaited_once_with(candidate)
        selection_store.save.assert_called_once_with(candidate)
        artifact_store.reconcile_runtime.assert_called_once_with(
            [{"plugin_id": PLUGIN_ID, "enabled": True, "status": "active"}]
        )

    async def test_source_tests_delegate_to_current_artifact_runtime(self) -> None:
        plugin_runtime = SimpleNamespace()
        selection_store = Mock()
        artifact_store = Mock()
        artifact_store.test_source.return_value = {
            "ok": True,
            "status": "passed",
            "test_framework": "unittest",
        }
        service = ExtensionManagementService(
            plugin_runtime=plugin_runtime,
            selection_store=selection_store,
            artifact_store=artifact_store,
        )

        result = await service.test_source(source_path="C:/work/plugin")

        self.assertEqual(result["status"], "passed")
        artifact_store.test_source.assert_called_once_with(Path("C:/work/plugin"))

    async def test_install_stage_removes_new_artifact_when_activation_fails(self) -> None:
        plugin_runtime = SimpleNamespace(
            selections=(),
            reconfigure=AsyncMock(
                side_effect=(
                    {
                        "ok": False,
                        "status": "candidate_rejected",
                        "reason": "plugin_probe_failed",
                        "published": False,
                        "plugins": [
                            {
                                "plugin_id": PLUGIN_ID,
                                "enabled": True,
                                "status": "unavailable",
                                "reason": "plugin_probe_failed",
                            }
                        ],
                    },
                    {"ok": True, "status": "inactive", "published": True, "plugins": []},
                )
            ),
        )
        selection_store = Mock()
        selection_store.load.return_value = ()
        artifact_store = Mock()
        artifact_store.publish_stage.return_value = {
            "ok": True,
            "status": "installed",
            "plugin_id": PLUGIN_ID,
            "version": "1.0.0",
            "digest": "a" * 64,
            "permissions": [],
            "unchanged": False,
        }
        artifact_store.reconcile_runtime.return_value = {
            "status": "activation_failed",
            "reload_required": False,
        }
        artifact_store.remove_plugin.return_value = {
            "ok": True,
            "status": "removed",
            "plugin_id": PLUGIN_ID,
        }
        service = ExtensionManagementService(
            plugin_runtime=plugin_runtime,
            selection_store=selection_store,
            artifact_store=artifact_store,
        )

        result = await service.install_stage(stage_id="stage-1", approved_permissions=())

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "activation_failed")
        self.assertEqual(result["reason"], "plugin_probe_failed")
        artifact_store.remove_plugin.assert_called_once_with(PLUGIN_ID)
        selection_store.save.assert_called_once_with(())
        self.assertEqual(plugin_runtime.reconfigure.await_count, 2)

    async def test_install_stage_restores_runtime_when_selection_persistence_fails(self) -> None:
        plugin_runtime = SimpleNamespace(
            selections=(),
            reconfigure=AsyncMock(
                side_effect=(
                    {
                        "ok": True,
                        "status": "active",
                        "published": True,
                        "plugins": [{"plugin_id": PLUGIN_ID, "enabled": True, "status": "active"}],
                    },
                    {"ok": True, "status": "inactive", "published": True, "plugins": []},
                )
            ),
        )
        selection_store = Mock()
        selection_store.load.return_value = ()
        selection_store.save.side_effect = (OSError("disk full"), None)
        artifact_store = Mock()
        artifact_store.publish_stage.return_value = {
            "ok": True,
            "status": "installed",
            "plugin_id": PLUGIN_ID,
            "version": "1.0.0",
            "digest": "a" * 64,
            "permissions": [],
            "unchanged": False,
        }
        artifact_store.remove_plugin.return_value = {
            "ok": True,
            "status": "removed",
            "plugin_id": PLUGIN_ID,
        }
        service = ExtensionManagementService(
            plugin_runtime=plugin_runtime,
            selection_store=selection_store,
            artifact_store=artifact_store,
        )

        result = await service.install_stage(stage_id="stage-1", approved_permissions=())

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "persist_failed")
        artifact_store.remove_plugin.assert_called_once_with(PLUGIN_ID)
        self.assertEqual(selection_store.save.call_args_list[0].args, ((PluginSelection(PLUGIN_ID, True),),))
        self.assertEqual(selection_store.save.call_args_list[1].args, ((),))
        self.assertEqual(plugin_runtime.reconfigure.await_count, 2)

    async def test_persisted_new_plugin_can_be_enabled_when_live_snapshot_is_stale(self) -> None:
        desired = (PluginSelection(PLUGIN_ID, False),)
        plugin_runtime = SimpleNamespace(
            selections=(),
            code_reload_mode="atomic_generation_switch",
            reconfigure=AsyncMock(
                return_value={
                    "ok": True,
                    "status": "active",
                    "published": True,
                    "plugins": [{"plugin_id": PLUGIN_ID, "enabled": True, "status": "active"}],
                }
            ),
            status_snapshot=Mock(
                return_value={
                    "ok": True,
                    "status": "active",
                    "plugins": [{"plugin_id": PLUGIN_ID, "enabled": True, "status": "active"}],
                }
            ),
        )
        selection_store = Mock()
        selection_store.load.return_value = desired
        selection_store.load_reason = ""
        service = ExtensionManagementService(
            plugin_runtime=plugin_runtime,
            selection_store=selection_store,
        )

        result = await service.set_enabled(plugin_id=PLUGIN_ID, enabled=True)

        candidate = (PluginSelection(PLUGIN_ID, True),)
        self.assertTrue(result["ok"])
        plugin_runtime.reconfigure.assert_awaited_once_with(candidate)
        selection_store.save.assert_called_once_with(candidate)

    async def test_uninstall_removes_dynamic_selection_from_live_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            selection_store = PluginSelectionStore(
                Path(temp_dir) / "plugin-selections.json",
                defaults=(),
                instance_id="bot-a",
            )
            selection_store.save((PluginSelection(PLUGIN_ID, True),))
            plugin_host = SimpleNamespace(
                selections=(PluginSelection(PLUGIN_ID, True),),
                reconfigure=AsyncMock(
                    side_effect=(
                        {
                            "status": "active",
                            "plugins": [{"plugin_id": PLUGIN_ID, "status": "disabled"}],
                        },
                        {"status": "active", "plugins": []},
                    )
                ),
            )
            artifact_store = Mock()
            artifact_store.snapshot.return_value = {
                "plugins": [{"plugin_id": PLUGIN_ID}],
            }
            artifact_store.remove_plugin.return_value = {
                "ok": True,
                "status": "removed",
                "plugin_id": PLUGIN_ID,
            }
            service = ExtensionManagementService(
                plugin_runtime=plugin_host,
                selection_store=selection_store,
                artifact_store=artifact_store,
            )

            result = await service.uninstall(plugin_id=PLUGIN_ID)

            self.assertTrue(result["ok"])
            self.assertEqual(selection_store.load(), ())
            self.assertEqual(plugin_host.reconfigure.await_count, 2)


class ManageExtensionToolTests(unittest.TestCase):
    def _context(self, *, client_mode: str, sender: str = "") -> ToolExecutionContext:
        request_context = {}
        if sender:
            request_context["qq_delivery_context"] = {"user_id": sender}
        return ToolExecutionContext(
            profile_user_id="master",
            session_id="session",
            now_ts=1,
            visual_payload={},
            client_mode=client_mode,
            request_context=request_context,
        )

    def test_owner_can_list_and_normal_member_cannot_mutate(self) -> None:
        service = Mock()
        service.execute_sync.return_value = {"ok": True, "status": "active", "plugins": []}
        handler = ManageExtensionToolHandler(service=service)

        desktop = handler.execute(
            call={"type": "manage_extension", "action": "list"},
            context=self._context(client_mode="desktop_pet"),
        )
        with patch("companion_v01.tool_handlers.extensions.config.MASTER_QQ", "123456"):
            denied = handler.execute(
                call={"type": "manage_extension", "action": "disable", "plugin_id": PLUGIN_ID},
                context=self._context(client_mode="qq_text", sender="999999"),
            )

        self.assertIn('"ok":true', desktop.followup_context)
        self.assertIn("extension_management_requires_owner", denied.followup_context)
        service.execute_sync.assert_called_once_with(
            action="list",
            plugin_id="",
            path="",
            stage_id="",
            approved_permissions=(),
        )

    def test_management_tool_is_visible_to_desktop_and_qq_without_shell_gate(self) -> None:
        registry = CapabilityRegistry()
        desktop = registry.select(CapabilitySnapshot(client_mode=ClientMode.DESKTOP_PET))
        qq = registry.select(CapabilitySnapshot(client_mode=ClientMode.QQ_TEXT))
        web = registry.select(CapabilitySnapshot(client_mode=ClientMode.SCENE_STATIC))

        self.assertIn("manage_extension", desktop.tool_names)
        self.assertIn("manage_extension", qq.tool_names)
        self.assertNotIn("manage_extension", web.tool_names)

    def test_mutation_uses_extension_family_and_exact_grant(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = Mock()
            service.execute_sync.return_value = {"ok": True, "status": "disabled"}
            approval_store = CapabilityApprovalStore()
            handler = ManageExtensionToolHandler(
                service=service,
                approval_store=approval_store,
                config_base_dir=Path(temp_dir),
            )
            context = self._context(client_mode="desktop_pet")
            call = {"type": "manage_extension", "action": "disable", "plugin_id": PLUGIN_ID}

            pending = handler.execute(call=call, context=context)
            self.assertIn("需要主人批准", pending.followup_context)
            service.execute_sync.assert_not_called()
            requests = approval_store.list_requests(
                profile_user_id="master",
                include_resolved=False,
            )["approvalRequests"]
            self.assertEqual(len(requests), 1)
            approval_store.decide_request(
                profile_user_id="master",
                request_id=requests[0]["requestId"],
                payload={"decision": "approved"},
            )

            completed = handler.execute(call=call, context=context)
            self.assertIn('"ok":true', completed.followup_context)
            service.execute_sync.assert_called_once_with(
                action="disable",
                plugin_id=PLUGIN_ID,
                path="",
                stage_id="",
                approved_permissions=(),
            )

    def test_source_stage_and_install_are_one_progressive_tool_contract(self) -> None:
        service = Mock()
        service.execute_sync.return_value = {"ok": True, "status": "staged"}
        handler = ManageExtensionToolHandler(service=service)
        source = handler.normalize_call(
            {"type": "manage_extension", "action": "stage_source", "path": "C:/work/plugin"}
        )
        tests = handler.normalize_call(
            {"type": "manage_extension", "action": "test_source", "path": "C:/work/plugin"}
        )
        install = handler.normalize_call(
            {
                "type": "manage_extension",
                "action": "install",
                "stage_id": "stage-1",
                "approved_permissions": ["storage.write", "job.run"],
            }
        )

        self.assertEqual(source["path"], "C:/work/plugin")
        self.assertEqual(tests["path"], "C:/work/plugin")
        self.assertEqual(install["stage_id"], "stage-1")
        self.assertEqual(install["approved_permissions"], ["storage.write", "job.run"])
        self.assertIsNone(
            handler.normalize_call(
                {"type": "manage_extension", "action": "install", "stage_id": "stage-1"}
            )
        )

    def test_market_browse_is_read_only_and_stage_approval_binds_reviewed_digest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = Mock()
            service.execute_sync.return_value = {"ok": True, "status": "ready", "plugins": []}
            approval_store = CapabilityApprovalStore()
            handler = ManageExtensionToolHandler(service=service, approval_store=approval_store, config_base_dir=Path(temp_dir))
            context = self._context(client_mode="desktop_pet")
            browsed = handler.execute(call=handler.normalize_call({"type": "manage_extension", "action": "market"}), context=context)
            self.assertIn('"ok":true', browsed.followup_context)
            service.execute_sync.assert_called_once()
            service.reset_mock()
            call = handler.normalize_call({"type": "manage_extension", "action": "stage_market", "plugin_id": PLUGIN_ID, "digest": "a" * 64})
            self.assertEqual(call["digest"], "a" * 64)
            handler.execute(call=call, context=context)
            service.execute_sync.assert_not_called()
            requests = approval_store.list_requests(profile_user_id="master", include_resolved=False)["approvalRequests"]
            approval_store.decide_request(profile_user_id="master", request_id=requests[0]["requestId"], payload={"decision": "approved"})
            handler.execute(call=call, context=context)
            self.assertEqual(service.execute_sync.call_args.kwargs["digest"], "a" * 64)
            service.reset_mock()
            handler.execute(call={**call, "digest": "b" * 64}, context=context)
            service.execute_sync.assert_not_called()
            self.assertIsNone(handler.normalize_call({"type": "manage_extension", "action": "stage_market", "plugin_id": PLUGIN_ID}))


if __name__ == "__main__":
    unittest.main()
