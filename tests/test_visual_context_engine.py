from __future__ import annotations

import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from companion_v01.resource_manifest import ResourceManifest
from companion_v01.visual_context_engine import build_current_visual_context


class CurrentVisualContextTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        assets = Path(temp.name) / "assets"
        outfit = assets / "characters" / "daily"
        outfit.mkdir(parents=True)
        (outfit / "normal.png").write_bytes(b"stub")
        (outfit / "happy.png").write_bytes(b"stub")
        for emotion, metadata in {
            "normal": {"name": "平静", "description": "平静表情的完整说明"},
            "happy": {"name": "开心", "aliases": ["喜悦"], "description": "开心表情的完整说明"},
        }.items():
            (outfit / f"{emotion}.meta.json").write_text(
                json.dumps(metadata, ensure_ascii=False), encoding="utf-8"
            )
        (outfit / "meta.json").write_text(
            json.dumps(
                {
                    "name": "日常服",
                    "default_emotion": "normal",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self.manifest = ResourceManifest(assets)
        self.manifest.refresh()
        self.engine = SimpleNamespace(resource_manifest=self.manifest)

    def render(self, visual, **kwargs):
        return build_current_visual_context(
            self.engine,
            profile_user_id="user",
            session_id="session",
            current_visual_payload=visual,
            visual_payload=visual,
            runtime_projection={"extra_character_outfits": []},
            **kwargs,
        )

    def test_desktop_state_keeps_selected_ids_and_names_without_repeating_catalog(self):
        visual = {"character": {"outfit": "daily"}, "emotion": "喜悦"}
        original = copy.deepcopy(visual)
        text = self.render(visual, character_only=True)
        self.assertEqual(text, "服装: daily (日常服)；表情: happy (开心)")
        self.assertEqual(visual, original)
        self.assertNotIn("平静", text)
        self.assertNotIn("完整说明", text)
        # The model still receives all choices and their meanings in the prefix.
        catalog = self.manifest.build_character_catalog_prompt_context()
        self.assertIn("平静表情的完整说明", catalog)
        self.assertIn("开心表情的完整说明", catalog)

    def test_desktop_state_uses_manifest_fallback_for_unavailable_selection(self):
        visual = {"character": {"outfit": "missing"}, "emotion": "missing"}
        self.assertEqual(
            self.render(visual, character_only=True),
            "服装: daily (日常服)；表情: normal (平静)",
        )

    def test_scene_state_retains_existing_resource_descriptions(self):
        visual = {"character": {"outfit": "daily"}, "emotion": "happy"}
        self.assertEqual(
            self.render(visual, character_only=False),
            self.manifest.describe_visual_state(visual),
        )
        self.assertIn("开心表情的完整说明", self.render(visual, character_only=False))


if __name__ == "__main__":
    unittest.main()
