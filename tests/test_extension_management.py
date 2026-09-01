from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from companion_v01.capability_registry import CapabilityRegistry, CapabilitySnapshot
from companion_v01.client_protocol import ClientMode
from companion_v01.extension_management import ExtensionManagementService, PluginSelectionStore
from companion_v01.instance_profile import PluginSelection
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
            plugin_host=Mock(),
            selection_store=Mock(),
        )

        self.assertIsNone(service.sync_timeout_seconds)


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
        service.execute_sync.assert_called_once_with(action="list", plugin_id="")

    def test_management_tool_is_visible_to_desktop_and_qq_without_shell_gate(self) -> None:
        registry = CapabilityRegistry()
        desktop = registry.select(CapabilitySnapshot(client_mode=ClientMode.DESKTOP_PET))
        qq = registry.select(CapabilitySnapshot(client_mode=ClientMode.QQ_TEXT))
        web = registry.select(CapabilitySnapshot(client_mode=ClientMode.SCENE_STATIC))

        self.assertIn("manage_extension", desktop.tool_names)
        self.assertIn("manage_extension", qq.tool_names)
        self.assertNotIn("manage_extension", web.tool_names)


if __name__ == "__main__":
    unittest.main()
