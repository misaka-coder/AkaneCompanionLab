import unittest

from companion_v01.scene.story.compiler import (
    StoryCompilationError,
    compile_story_markdown,
)


class TestStoryCompiler(unittest.TestCase):
    def test_compile_full_story(self):
        md = """---
story_id: test_story
title: 测试剧本
description: 一个用于测试完整流程的剧本
cover_image: scenes/test.png
initial_node_id: start
---

## start [script]
角色: Akane
表情: normal
动作: nod
背景: scenes/room.png
音乐: bgm/warm.mp3
下一幕: branch_choice

你好，欢迎来到这个故事。

## branch_choice [choice]
标题: 请做出抉择
角色: Akane
表情: 卖萌
动作: idle

你想听听我的心里话，还是直接自由聊聊？

? 你的选择:
- "我想听心里话" -> branch_story
- "我们随便聊两句" -> agent_chat

## branch_story [script]
角色: Akane
表情: 脸红
动作: nod
下一幕: end_warm

其实……我一直很感激你陪在我身边。

## agent_chat [agent]
角色: Akane
表情: 思考中
动作: nod
目标: 茜茜有些害羞，请根据玩家回复引导向结局
下一幕: end_warm

哼，你想跟我聊什么呀？

## end_warm [ending]
角色: Akane
表情: 开心
动作: bounce
结局标题: 暖心结局
结局总结: 两人在平静温和的气氛中达成了美好的默契。

谢谢你今天的陪伴！
"""
        pack = compile_story_markdown(md)
        self.assertEqual(pack.story_id, "test_story")
        self.assertEqual(pack.title, "测试剧本")
        self.assertEqual(pack.description, "一个用于测试完整流程的剧本")
        self.assertEqual(pack.cover_image, "scenes/test.png")
        self.assertEqual(pack.initial_node_id, "start")
        self.assertEqual(len(pack.nodes), 5)

        start = pack.nodes["start"]
        self.assertEqual(start.kind, "script")
        self.assertEqual(start.speaker, "Akane")
        self.assertEqual(start.emotion_id, "normal")
        self.assertEqual(start.motion_id, "nod")
        self.assertEqual(start.background_id, "scenes/room.png")
        self.assertEqual(start.music_id, "bgm/warm.mp3")
        self.assertEqual(start.next_node_id, "branch_choice")
        self.assertEqual(start.speech, "你好，欢迎来到这个故事。")

        choice = pack.nodes["branch_choice"]
        self.assertEqual(choice.kind, "choice")
        self.assertEqual(choice.title, "请做出抉择")
        self.assertEqual(len(choice.options), 2)
        self.assertEqual(choice.options[0].label, "我想听心里话")
        self.assertEqual(choice.options[0].next_node_id, "branch_story")
        self.assertEqual(choice.options[1].label, "我们随便聊两句")
        self.assertEqual(choice.options[1].next_node_id, "agent_chat")

        agent = pack.nodes["agent_chat"]
        self.assertEqual(agent.kind, "agent")
        self.assertEqual(agent.prompt_objective, "茜茜有些害羞，请根据玩家回复引导向结局")
        self.assertEqual(agent.next_node_id, "end_warm")

        ending = pack.nodes["end_warm"]
        self.assertEqual(ending.kind, "ending")
        self.assertEqual(ending.ending_title, "暖心结局")
        self.assertEqual(ending.ending_summary, "两人在平静温和的气氛中达成了美好的默契。")

    def test_kind_auto_inference(self):
        md = """---
story_id: infer_story
title: 推断测试
---

## node1
角色: Akane
下一幕: node2

这是默认推断为script的节点。

## node2
标题: 选项
? 选项：
- 走向结局 -> node3

## node3
结局标题: 完结
谢谢大家。
"""
        pack = compile_story_markdown(md)
        self.assertEqual(pack.nodes["node1"].kind, "script")
        self.assertEqual(pack.nodes["node2"].kind, "choice")
        self.assertEqual(pack.nodes["node3"].kind, "ending")

    def test_compilation_errors(self):
        # Missing frontmatter
        with self.assertRaises(StoryCompilationError):
            compile_story_markdown("## node1\nhello")

        # Missing story_id
        with self.assertRaises(StoryCompilationError):
            compile_story_markdown("---\ntitle: test\n---\n## n1\nhello")

        # Invalid next_node_id
        with self.assertRaises(StoryCompilationError) as ctx:
            compile_story_markdown("""---
story_id: s1
title: t1
---
## n1
下一幕: nonexistent_node
hello
""")
        self.assertIn("nonexistent_node", str(ctx.exception))

        # Choice pointing to nonexistent node
        with self.assertRaises(StoryCompilationError) as ctx:
            compile_story_markdown("""---
story_id: s1
title: t1
---
## n1
? 选项:
- 走去哪里 -> nowhere
""")
        self.assertIn("nowhere", str(ctx.exception))

    def test_frontmatter_and_node_scene_attributes(self):
        md = """---
story_id: scene_decor_story
title: 场景装扮剧本
background: scenes/家/夜晚客厅.png
bgm: 吟诗作对
outfit: 水手服
---

## intro_1
角色: Akane
下一幕: intro_2

我们到了夜晚客厅哦。

## intro_2
角色: Akane
服装: 睡衣
音乐: 周杰伦 - 说好的幸福呢
背景: scenes/家/白天客厅.png
结局标题: 晚安
下一幕: 

换上睡衣准备休息啦。
"""
        pack = compile_story_markdown(md)
        self.assertEqual(pack.background_id, "scenes/家/夜晚客厅.png")
        self.assertEqual(pack.music_id, "吟诗作对")
        self.assertEqual(pack.outfit_id, "水手服")

        # Initial node inherits frontmatter defaults
        n1 = pack.nodes["intro_1"]
        self.assertEqual(n1.background_id, "scenes/家/夜晚客厅.png")
        self.assertEqual(n1.music_id, "吟诗作对")
        self.assertEqual(n1.outfit_id, "水手服")

        # Second node overrides them
        n2 = pack.nodes["intro_2"]
        self.assertEqual(n2.background_id, "scenes/家/白天客厅.png")
        self.assertEqual(n2.music_id, "周杰伦 - 说好的幸福呢")
        self.assertEqual(n2.outfit_id, "睡衣")


if __name__ == "__main__":
    unittest.main()

