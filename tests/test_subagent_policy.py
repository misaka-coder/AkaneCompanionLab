from dataclasses import replace
from types import MappingProxyType, SimpleNamespace
import unittest

from companion_v01.capability_registry import CapabilitySelection
from companion_v01.subagent_policy import task_capability_selection
from companion_v01.tool_handlers.core import BaseToolHandler, ToolMetadata, TOOL_SPEC_BY_TYPE


class SubagentPolicyTests(unittest.TestCase):
    def selection(self, names):
        handlers = {}
        for name in names:
            handler = BaseToolHandler()
            handler.tool_type = name
            handlers[name] = handler
        return CapabilitySelection(
            light_hints=(), module_names=(), tool_names=tuple(names), schema_tool_names=tuple(names),
            resolved_handlers=MappingProxyType(handlers),
            tool_specs=tuple(TOOL_SPEC_BY_TYPE[n] for n in names if n in TOOL_SPEC_BY_TYPE),
        )

    def test_work_tools_inherit_without_a_coding_allowlist(self):
        names = ("browser_page", "inspect_generated_file", "manage_generated_file",
                 "register_workspace_items", "manage_skill", "mcp_manage", "manage_extension",
                 "project_inspect", "workspace_write", "workspace_patch", "exec_run", "exec_status",
                 "exec_cancel", "web_search", "load_skill", "load_mcp", "invoke_mcp", "mcp.demo.echo",
                 "plugin.new_analysis")
        parent = self.selection(names)
        child = task_capability_selection(parent)
        self.assertEqual(child.tool_names, names)
        self.assertEqual(child.schema_tool_names, names)
        self.assertIsNone(parent.execution_allowlist)
        self.assertNotIn("not_available", child.execution_allowlist)

    def test_parent_semantics_removed_from_all_surfaces(self):
        names = ("retrieve_memory", "open_memory", "browse_memory", "read_memory_timeline",
                 "load_character_context", "send_file", "send_audio", "send_sticker", "send_music_card",
                 "open_music_search", "onebot_action", "open_browser", "spawn_subagent",
                 "manage_project_workspace", "clear_attachment_focus", "web_search")
        parent = replace(self.selection(names), native_tool_aliases={"old_send": "send_file"},
                         execution_receipts={"send_file": {"id": "receipt"}})
        child = task_capability_selection(parent)
        self.assertEqual(child.tool_names, ("web_search",))
        self.assertEqual(child.schema_tool_names, ("web_search",))
        self.assertEqual(tuple(child.resolved_handlers), ("web_search",))
        self.assertEqual([s.capability_id for s in child.tool_specs], ["web_search"])
        self.assertEqual(child.native_tool_aliases, {})
        self.assertEqual(child.execution_receipts, {})
        self.assertEqual(parent.native_tool_aliases, {"old_send": "send_file"})

    def test_existing_ceiling_and_new_family_members_respected(self):
        parent = self.selection(("web_search", "browser_page", "new_delivery"))
        handlers = dict(parent.resolved_handlers)
        handlers["new_delivery"] = SimpleNamespace(tool_metadata=lambda: ToolMetadata(family="file_handoff"))
        parent = replace(parent, resolved_handlers=MappingProxyType(handlers),
                         execution_allowlist=frozenset({"web_search", "new_delivery"}))
        self.assertEqual(task_capability_selection(parent).tool_names, ("web_search",))

    def test_metadata_error_is_not_silently_treated_as_allowed(self):
        def broken():
            raise RuntimeError("metadata unavailable")
        parent = replace(self.selection(("broken",)), resolved_handlers={"broken": SimpleNamespace(tool_metadata=broken)})
        with self.assertRaises(RuntimeError):
            task_capability_selection(parent)

    def test_deferred_ceiling_survives_without_making_schemas_resident(self):
        parent = replace(self.selection(("invoke_mcp",)),
                         execution_allowlist=frozenset({"invoke_mcp", "mcp.demo.echo", "send_file"}))
        child = task_capability_selection(parent)
        self.assertEqual(child.schema_tool_names, ("invoke_mcp",))
        self.assertEqual(child.execution_allowlist, frozenset({"invoke_mcp", "mcp.demo.echo"}))
        self.assertEqual(task_capability_selection(child).execution_allowlist, child.execution_allowlist)


if __name__ == "__main__":
    unittest.main()
