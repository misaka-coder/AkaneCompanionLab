import json
import tempfile
import time
import unittest
from pathlib import Path

from companion_v01.scene.contracts import StoryNode, StoryPack
from companion_v01.scene.story.loader import StoryLoader


class TestStoryLoader(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.characters_dir = self.root / "characters"
        self.user_stories_dir = self.root / "users_data" / "stories"
        self.characters_dir.mkdir(parents=True)
        self.user_stories_dir.mkdir(parents=True)

        self.loader = StoryLoader(
            characters_dir=self.characters_dir,
            user_stories_dir=self.user_stories_dir,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_load_global_markdown_story(self):
        md_content = """---
story_id: global_md_story
title: 全局故事
description: 全局测试故事
initial_node_id: n1
---

## n1 [script]
角色: Akane
下一幕: n2
你好！

## n2 [ending]
再见！
"""
        file_path = self.user_stories_dir / "my_story.md"
        file_path.write_text(md_content, encoding="utf-8")

        pack = self.loader.get_pack("global_md_story")
        self.assertIsNotNone(pack)
        self.assertEqual(pack.title, "全局故事")
        self.assertEqual(len(pack.nodes), 2)

    def test_load_character_specific_story(self):
        char_stories = self.characters_dir / "akane_v1" / "stories"
        char_stories.mkdir(parents=True)

        json_pack = {
            "story_id": "akane_exclusive",
            "title": "茜茜专属故事",
            "description": "专属剧情",
            "initial_node_id": "start",
            "nodes": {
                "start": {
                    "node_id": "start",
                    "kind": "script",
                    "speech": "这是我的秘密日记。",
                    "speaker": "Akane",
                    "emotion_id": "normal",
                    "motion_id": "idle",
                    "next_node_id": "",
                    "options": [],
                }
            }
        }
        (char_stories / "secret.json").write_text(json.dumps(json_pack, ensure_ascii=False), encoding="utf-8")

        # When listing without character_pack_id, shouldn't appear
        packs_empty = self.loader.list_packs("")
        self.assertNotIn("akane_exclusive", packs_empty)

        # When listing for akane_v1, it should appear
        packs_akane = self.loader.list_packs("akane_v1")
        self.assertIn("akane_exclusive", packs_akane)
        self.assertEqual(packs_akane["akane_exclusive"].title, "茜茜专属故事")

    def test_hot_reload_on_file_change(self):
        md_file = self.user_stories_dir / "dynamic.md"
        md_v1 = """---
story_id: dynamic_story
title: 第一版标题
initial_node_id: n1
---
## n1
第一版台词
"""
        md_file.write_text(md_v1, encoding="utf-8")
        pack_v1 = self.loader.get_pack("dynamic_story")
        self.assertEqual(pack_v1.title, "第一版标题")

        # Update file with newer content
        time.sleep(0.02)  # Ensure mtime changes
        md_v2 = """---
story_id: dynamic_story
title: 第二版更新标题
initial_node_id: n1
---
## n1
第二版台词
"""
        md_file.write_text(md_v2, encoding="utf-8")

        pack_v2 = self.loader.get_pack("dynamic_story")
        self.assertEqual(pack_v2.title, "第二版更新标题")
        self.assertEqual(pack_v2.nodes["n1"].speech, "第二版台词")

    def test_safe_path_boundary_prevents_escape(self):
        # 1. Traversal in character_pack_id
        with self.assertRaises(ValueError):
            self.loader.save_story("..", "escape.md", "# test")

        with self.assertRaises(ValueError):
            self.loader.save_story("akane_v1/../../", "escape.md", "# test")

    def test_json_graph_validation_catches_missing_initial_node(self):
        from companion_v01.scene.story.compiler import StoryCompilationError
        invalid_json = json.dumps({
            "story_id": "broken_graph",
            "title": "破损剧本",
            "initial_node_id": "missing_node",
            "nodes": {
                "n1": {
                    "node_id": "n1",
                    "kind": "script",
                    "speech": "hello",
                }
            }
        })
        with self.assertRaises(StoryCompilationError):
            self.loader.save_story("akane_v1", "broken.json", invalid_json)


if __name__ == "__main__":
    unittest.main()
