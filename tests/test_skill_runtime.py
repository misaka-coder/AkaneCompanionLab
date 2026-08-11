from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from companion_v01.capability_registry import CapabilityRegistry, CapabilitySelection, CapabilitySnapshot
from companion_v01.client_protocol import ClientMode
from companion_v01.engine import AkaneMemoryEngine
from companion_v01.skill_runtime import SkillRegistry
from companion_v01.skill_specs import LOAD_SKILL_TOOL_SPEC, MANAGE_SKILL_TOOL_SPEC
from companion_v01.tool_handlers.core import ToolExecutionContext
from companion_v01.tool_handlers.skills import LoadSkillToolHandler, ManageSkillToolHandler


def _write_skill(root: Path, name: str, description: str, body: str, *, resource: str = "") -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n",
        encoding="utf-8",
    )
    if resource:
        target = skill_dir / resource
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"resource for {name}\n", encoding="utf-8")
    return skill_dir


class SkillRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.bundled = self.root / "bundled"
        self.managed = self.root / "managed"
        self.workspace = self.root / "workspace"
        self.bundled.mkdir()
        self.managed.mkdir()
        self.workspace.mkdir()
        self.registry = SkillRegistry(
            bundled_root=self.bundled,
            managed_root=self.managed,
            execution_workspace_root=self.workspace,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_catalog_is_sorted_stable_and_contains_no_absolute_paths(self) -> None:
        _write_skill(self.bundled, "zeta", "Zeta tasks.", "Do zeta.")
        _write_skill(self.bundled, "alpha", "Alpha tasks.", "Do alpha.")
        first = self.registry.snapshot()
        second = self.registry.snapshot()
        self.assertEqual([entry.name for entry in first.entries], ["alpha", "zeta"])
        self.assertEqual(first.catalog_revision, second.catalog_revision)
        catalog = self.registry.prompt_catalog()
        self.assertLess(catalog.index("alpha"), catalog.index("zeta"))
        self.assertNotIn(str(self.root), catalog)

    def test_managed_skill_overrides_same_named_bundled_skill(self) -> None:
        _write_skill(self.bundled, "demo", "Bundled demo.", "bundled body")
        _write_skill(self.managed, "demo", "Managed demo.", "managed body")
        entry = self.registry.snapshot().by_name()["demo"]
        self.assertEqual(entry.source, "managed")
        self.assertEqual(entry.instructions, "managed body")

    def test_hot_reload_updates_next_snapshot_without_restart(self) -> None:
        skill_dir = _write_skill(self.managed, "demo", "Old description.", "old body")
        old = self.registry.snapshot()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: demo\ndescription: New description.\n---\n\nnew body\n",
            encoding="utf-8",
        )
        new = self.registry.snapshot()
        self.assertNotEqual(old.catalog_revision, new.catalog_revision)
        self.assertEqual(new.by_name()["demo"].instructions, "new body")

    def test_body_only_reload_keeps_prompt_catalog_stable(self) -> None:
        skill_dir = _write_skill(self.managed, "demo", "Same routing metadata.", "old body")
        old_snapshot = self.registry.snapshot()
        old_read = self.registry.load("demo")
        (skill_dir / "SKILL.md").write_text(
            "---\nname: demo\ndescription: Same routing metadata.\n---\n\nnew body\n",
            encoding="utf-8",
        )
        new_snapshot = self.registry.snapshot()
        new_read = self.registry.load("demo")
        self.assertEqual(old_snapshot.catalog_revision, new_snapshot.catalog_revision)
        self.assertNotEqual(old_read.revision, new_read.revision)
        self.assertEqual(new_read.content, "new body")

    def test_invalid_hot_update_keeps_last_good_and_reports_diagnostic(self) -> None:
        skill_dir = _write_skill(self.managed, "demo", "Valid demo.", "valid body")
        self.registry.snapshot()
        (skill_dir / "SKILL.md").write_text("half-written", encoding="utf-8")
        snapshot = self.registry.snapshot()
        self.assertEqual(snapshot.by_name()["demo"].instructions, "valid body")
        self.assertEqual(snapshot.diagnostics[0]["fallback"], "last_good")
        self.assertIn("上一有效版本", self.registry.prompt_catalog())

    def test_load_skill_supports_progressive_resource_and_blocks_traversal(self) -> None:
        _write_skill(
            self.managed,
            "demo",
            "Demo workflow.",
            "Read references/detail.md only when needed.",
            resource="references/detail.md",
        )
        main = self.registry.load("demo")
        resource = self.registry.load("demo", resource="references/detail.md")
        escaped = self.registry.load("demo", resource="../secret.txt")
        self.assertEqual(main.status, "loaded")
        self.assertEqual(main.execution_cwd, "alias:skills")
        self.assertIn("references/detail.md", main.files)
        self.assertEqual(resource.content, "resource for demo\n")
        self.assertEqual(escaped.reason, "skill_resource_path_invalid")

    def test_publish_validates_then_atomically_installs_and_hot_reloads(self) -> None:
        _write_skill(self.workspace / "skill_drafts", "demo", "Publish demo.", "published body")
        validated = self.registry.validate_draft("skill_drafts/demo")
        published = self.registry.publish("skill_drafts/demo")
        self.assertEqual(validated.status, "valid")
        self.assertEqual(published.status, "published")
        self.assertFalse(published.replaced)
        loaded = self.registry.load("demo")
        self.assertEqual(loaded.content, "published body")
        self.assertNotIn(".akane-skill.json", loaded.files)
        self.assertTrue((self.managed / "demo" / ".akane-skill.json").is_file())

    def test_invalid_replacement_does_not_clobber_installed_skill(self) -> None:
        _write_skill(self.managed, "demo", "Working demo.", "working body")
        draft = self.workspace / "skill_drafts" / "demo"
        draft.mkdir(parents=True)
        (draft / "SKILL.md").write_text("invalid", encoding="utf-8")
        result = self.registry.publish("skill_drafts/demo", replace=True)
        self.assertEqual(result.status, "invalid")
        self.assertEqual(self.registry.load("demo").content, "working body")

    def test_load_handler_returns_full_producer_bounded_evidence(self) -> None:
        _write_skill(self.managed, "demo", "Demo workflow.", "follow these exact steps")
        handler = LoadSkillToolHandler(registry=self.registry)
        result = handler.execute(
            call={"type": "load_skill", "name": "demo"},
            context=ToolExecutionContext("master", "session", 1, {}),
        )
        self.assertIn("follow these exact steps", result.followup_context)
        self.assertTrue(result.followup_envelope.producer_bounded)
        self.assertIn("alias:skills", result.followup_context)
        self.assertNotIn(str(self.root), result.followup_context)

    def test_engine_prompt_keeps_catalog_when_native_tool_is_excluded_from_legacy_prompt(self) -> None:
        _write_skill(self.managed, "demo", "Use for a model-visible workflow.", "follow the workflow")
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        engine.skill_registry = self.registry
        handler = LoadSkillToolHandler(registry=self.registry)
        engine._resolve_tool_handlers = mock.Mock(return_value={"load_skill": handler})
        selection = CapabilitySelection(
            light_hints=(),
            tool_names=("load_skill",),
            module_names=(),
        )

        prompt = engine._build_tool_prompt_context(
            allow_tool_call=True,
            exclude_tool_types={"load_skill"},
            capability_selection=selection,
            include_capability_status=False,
        )

        self.assertEqual(prompt.count("【可按需加载的 Skills"), 1)
        self.assertIn("demo：Use for a model-visible workflow.", prompt)
        self.assertIn("本轮可直接调用的工具及参数", prompt)
        self.assertNotIn(str(self.root), prompt)

    def test_manage_handler_is_owner_only_for_qq_but_available_to_desktop(self) -> None:
        _write_skill(self.workspace / "skill_drafts", "demo", "Owner demo.", "body")
        handler = ManageSkillToolHandler(registry=self.registry)
        qq_context = lambda user_id: ToolExecutionContext(
            profile_user_id=str(user_id),
            session_id="group:1",
            now_ts=1,
            visual_payload={},
            client_mode="qq_text",
            request_context={"qq_delivery_context": {"user_id": str(user_id), "is_group": True}},
        )
        with mock.patch("companion_v01.tool_handlers.skills.config.MASTER_QQ", "123"):
            denied = handler.execute(
                call={"type": "manage_skill", "action": "validate", "draft_path": "skill_drafts/demo"},
                context=qq_context("456"),
            )
            allowed = handler.execute(
                call={"type": "manage_skill", "action": "validate", "draft_path": "skill_drafts/demo"},
                context=qq_context("123"),
            )
        desktop = handler.execute(
            call={"type": "manage_skill", "action": "validate", "draft_path": "skill_drafts/demo"},
            context=ToolExecutionContext("master", "desktop", 1, {}, client_mode="desktop_pet"),
        )
        self.assertIn("主人权限", denied.followup_context)
        self.assertIn("校验通过", allowed.followup_context)
        self.assertIn("校验通过", desktop.followup_context)

    def test_specs_and_capability_selection_are_deterministic(self) -> None:
        first = json.dumps(LOAD_SKILL_TOOL_SPEC.input_schema, ensure_ascii=False, separators=(",", ":"))
        second = json.dumps(LOAD_SKILL_TOOL_SPEC.input_schema, ensure_ascii=False, separators=(",", ":"))
        self.assertEqual(first, second)
        self.assertEqual(MANAGE_SKILL_TOOL_SPEC.input_schema["properties"]["action"]["enum"], ["validate", "publish"])
        registry = CapabilityRegistry()
        desktop = registry.select(CapabilitySnapshot(client_mode=ClientMode.DESKTOP_PET, execution_enabled=True))
        qq = registry.select(
            CapabilitySnapshot(client_mode=ClientMode.QQ_TEXT, execution_enabled=True, execution_qq_enabled=True)
        )
        web = registry.select(CapabilitySnapshot(client_mode=ClientMode.SCENE_STATIC))
        self.assertIn("load_skill", desktop.tool_names)
        self.assertIn("load_skill", qq.tool_names)
        self.assertIn("load_skill", web.tool_names)
        self.assertIn("manage_skill", desktop.tool_names)
        self.assertIn("manage_skill", qq.tool_names)
        self.assertNotIn("manage_skill", web.tool_names)


if __name__ == "__main__":
    unittest.main()
