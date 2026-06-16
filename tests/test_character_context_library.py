from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from companion_v01.capability_registry import CapabilityRegistry
from companion_v01.character_context_library import CharacterContextLibraryService
from companion_v01.client_protocol import ClientMode
from companion_v01.desktop_pet_character_resources import DesktopPetCharacterResourceService
from companion_v01.engine import AkaneMemoryEngine
from companion_v01.tool_runtime import (
    LoadCharacterContextToolHandler,
    ToolExecutionContext,
    ToolExecutionResult,
)


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class CharacterContextLibraryServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.characters_dir = Path(self.temp_dir.name) / "characters"
        self.pack_dir = self.characters_dir / "reimu"

    def _write_pack(self, libraries: list[dict] | None = None) -> None:
        payload = {
            "identity": {
                "id": "reimu",
                "name": "博丽灵梦",
                "app_name": "灵梦",
                "user_title": "你",
            }
        }
        if libraries is not None:
            payload["context_libraries"] = libraries
        write_json(self.pack_dir / "character.json", payload)

    def test_arbitrary_library_folders_render_dynamic_prompt_catalog(self) -> None:
        self._write_pack(
            [
                {
                    "folder": "people_i_know",
                    "name": "熟人档案",
                    "description": "记录我与熟人的真实关系和相处方式。",
                    "load_when": "谈到具体熟人或共同经历时读取。",
                    "aliases": {
                        "魔理沙": ["雾雨魔理沙", "黑白"],
                    },
                },
                {
                    "folder": "spell_cards",
                    "name": "符卡资料",
                    "description": "记录符卡、招式和使用背景。",
                    "load_when": "讨论战斗或具体招式时读取。",
                },
            ]
        )
        write_text(self.pack_dir / "people_i_know" / "魔理沙.md", "魔理沙是长期来往的朋友。")
        write_text(self.pack_dir / "spell_cards" / "梦想封印.md", "梦想封印是代表性的符卡。")
        write_text(self.pack_dir / "spell_cards" / "ignored.txt", "不应进入清单。")
        write_text(self.pack_dir / "spell_cards" / "nested" / "深层.md", "不扫描多层目录。")

        service = CharacterContextLibraryService(characters_dir=self.characters_dir)
        prompt = service.build_prompt_context("reimu")

        self.assertIn("熟人档案（library=people_i_know）", prompt)
        self.assertIn("符卡资料（library=spell_cards）", prompt)
        self.assertIn("people_i_know/魔理沙", prompt)
        self.assertIn("别名：雾雨魔理沙、黑白", prompt)
        self.assertIn("spell_cards/梦想封印", prompt)
        self.assertIn("load_character_context", prompt)
        self.assertNotIn("ignored", prompt)
        self.assertNotIn("深层", prompt)

    def test_missing_or_empty_libraries_do_not_inject_prompt(self) -> None:
        self._write_pack()
        service = CharacterContextLibraryService(characters_dir=self.characters_dir)
        self.assertEqual(service.build_prompt_context("reimu"), "")

    def test_private_local_folder_cannot_be_exposed_as_character_context(self) -> None:
        self._write_pack(
            [
                {
                    "folder": "_local",
                    "name": "私有数据",
                    "description": "不应暴露",
                    "load_when": "永远",
                }
            ]
        )
        write_text(self.pack_dir / "_local" / "记忆.md", "私密聊天记录")
        service = CharacterContextLibraryService(characters_dir=self.characters_dir)

        self.assertEqual(service.build_prompt_context("reimu"), "")
        result = service.load_context("reimu", ["_local/记忆"])
        self.assertEqual(result["status"], "unavailable")

        self._write_pack(
            [
                {
                    "folder": "empty_library",
                    "name": "空资料库",
                    "description": "目前还没有资料。",
                }
            ]
        )
        (self.pack_dir / "empty_library").mkdir(parents=True)
        self.assertEqual(service.build_prompt_context("reimu"), "")

    def test_batch_load_returns_full_content_and_structured_failures(self) -> None:
        self._write_pack(
            [
                {
                    "folder": "relationships",
                    "name": "人物关系",
                    "description": "角色关系。",
                },
                {
                    "folder": "incidents",
                    "name": "重要事件",
                    "description": "亲历事件。",
                },
            ]
        )
        long_content = "魔理沙关系正文。" + ("细节" * 4000)
        write_text(self.pack_dir / "relationships" / "魔理沙.md", long_content)
        write_text(self.pack_dir / "incidents" / "永夜异变.md", "永夜异变正文。")
        write_text(self.characters_dir / "outside.md", "不得读取。")

        service = CharacterContextLibraryService(characters_dir=self.characters_dir)
        result = service.load_context(
            "reimu",
            [
                "relationships/魔理沙.md",
                "incidents/永夜异变",
                "../outside",
            ],
        )

        self.assertEqual(result["status"], "partial")
        self.assertEqual(
            [item["target"] for item in result["loaded"]],
            ["relationships/魔理沙", "incidents/永夜异变"],
        )
        self.assertEqual(result["loaded"][0]["content"], long_content)
        self.assertEqual(result["failed"][0]["status"], "not_found")
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn(str(self.characters_dir), serialized)
        self.assertNotIn("不得读取", serialized)

    def test_direct_filename_mention_is_loaded_automatically(self) -> None:
        self._write_pack(
            [
                {
                    "folder": "relationships",
                    "name": "人物关系",
                    "description": "人物关系。",
                    "aliases": {
                        "魔理沙": ["雾雨魔理沙", "黑白"],
                    },
                },
                {
                    "folder": "incidents",
                    "name": "重要事件",
                    "description": "亲历事件。",
                },
            ]
        )
        write_text(self.pack_dir / "relationships" / "魔理沙.md", "魔理沙关系正文。")
        write_text(self.pack_dir / "incidents" / "永夜异变.md", "永夜异变正文。")

        service = CharacterContextLibraryService(characters_dir=self.characters_dir)
        automatic_result = service.load_automatic_context(
            "reimu",
            "黑白在永夜异变时和你是什么关系？",
        )
        automatic_context = automatic_result["followup_context"]

        self.assertIn("[人物关系 / 魔理沙]", automatic_context)
        self.assertIn("[重要事件 / 永夜异变]", automatic_context)
        self.assertIn("魔理沙关系正文", automatic_context)
        self.assertIn("永夜异变正文", automatic_context)
        self.assertEqual(
            automatic_result["matches"],
            [
                {
                    "target": "relationships/魔理沙",
                    "matched_terms": ["黑白"],
                },
                {
                    "target": "incidents/永夜异变",
                    "matched_terms": ["永夜异变"],
                },
            ],
        )
        self.assertEqual(service.build_automatic_context("reimu", "今天天气怎么样？"), "")

    def test_character_resource_service_injects_catalog_only_for_chat_modes(self) -> None:
        self._write_pack(
            [
                {
                    "folder": "daily_notes",
                    "name": "日常侧面",
                    "description": "记录日常习惯。",
                    "load_when": "谈到日常生活时读取。",
                }
            ]
        )
        write_text(self.pack_dir / "daily_notes" / "神社早晨.md", "早晨会先打扫神社。")

        service = DesktopPetCharacterResourceService(characters_dir=self.characters_dir)
        desktop_context = service.build_persona_prompt_context("reimu", client_mode="desktop_pet")
        qq_context = service.build_persona_prompt_context("reimu", client_mode="qq_text")
        memory_context = service.build_persona_prompt_context("reimu", client_mode="memory")

        self.assertIn("daily_notes/神社早晨", desktop_context["system_context"])
        self.assertIn("daily_notes/神社早晨", qq_context["system_context"])
        self.assertNotIn("CHARACTER CONTEXT LIBRARIES", memory_context["system_context"])


class LoadCharacterContextToolHandlerTests(unittest.TestCase):
    def test_handler_normalizes_batch_targets_and_returns_private_followup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            characters_dir = Path(temp_dir) / "characters"
            pack_dir = characters_dir / "reimu"
            write_json(
                pack_dir / "character.json",
                {
                    "context_libraries": [
                        {
                            "folder": "relationships",
                            "name": "人物关系",
                            "description": "人物关系。",
                        }
                    ]
                },
            )
            write_text(pack_dir / "relationships" / "魔理沙.md", "是常来神社的朋友。")
            service = CharacterContextLibraryService(characters_dir=characters_dir)
            handler = LoadCharacterContextToolHandler(context_library_service=service)

            call = handler.normalize_call(
                {
                    "type": "load_character_context",
                    "targets": ["relationships/魔理沙", "relationships/魔理沙"],
                }
            )
            self.assertEqual(
                call,
                {
                    "type": "load_character_context",
                    "targets": ["relationships/魔理沙"],
                },
            )
            result = handler.execute(
                call=call or {},
                context=ToolExecutionContext(
                    profile_user_id="master",
                    session_id="desktop",
                    now_ts=1712400000,
                    visual_payload={},
                    character_pack_id="reimu",
                    client_mode="desktop_pet",
                ),
            )

            self.assertIn("是常来神社的朋友", result.followup_context)
            self.assertIn("不要向用户描述文件", result.followup_context)
            self.assertEqual(
                result.state_updates["character_context"],
                {
                    "status": "loaded",
                    "loaded": ["relationships/魔理沙"],
                    "failed": [],
                },
            )
            self.assertEqual(handler.build_prompt_instruction(), "")

    def test_capability_registry_exposes_internal_loader(self) -> None:
        registry = CapabilityRegistry()
        self.assertIn(
            "load_character_context",
            registry.tool_names_for_mode(ClientMode.DESKTOP_PET),
        )
        self.assertIn(
            "load_character_context",
            registry.tool_names_for_mode(ClientMode.QQ_TEXT),
        )

    def test_engine_debug_combines_automatic_and_tool_loads_without_content(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            characters_dir = Path(temp_dir) / "characters"
            pack_dir = characters_dir / "reimu"
            write_json(
                pack_dir / "character.json",
                {
                    "context_libraries": [
                        {
                            "folder": "relationships",
                            "name": "人物关系",
                            "description": "人物关系。",
                            "aliases": {"魔理沙": ["黑白"]},
                        }
                    ]
                },
            )
            write_text(pack_dir / "relationships" / "魔理沙.md", "不应出现在调试数据里的正文。")
            resources = DesktopPetCharacterResourceService(characters_dir=characters_dir)
            engine = object.__new__(AkaneMemoryEngine)
            engine.desktop_pet_character_resources = resources
            tool_result = ToolExecutionResult(
                tool_type="load_character_context",
                state_updates={
                    "character_context": {
                        "status": "loaded",
                        "loaded": ["events/红雾异变"],
                        "failed": [],
                    }
                },
            )

            debug = engine._build_character_context_debug_payload(
                character_pack_id="reimu",
                user_message="黑白今天来了吗？",
                tool_results=[tool_result],
            )

            self.assertEqual(debug["automatic"]["loaded"], ["relationships/魔理沙"])
            self.assertEqual(
                debug["automatic"]["matches"],
                [
                    {
                        "target": "relationships/魔理沙",
                        "matched_terms": ["黑白"],
                    }
                ],
            )
            self.assertEqual(
                debug["tool_rounds"][0]["loaded"],
                ["events/红雾异变"],
            )
            serialized = json.dumps(debug, ensure_ascii=False)
            self.assertNotIn("不应出现在调试数据里的正文", serialized)
            self.assertNotIn(str(characters_dir), serialized)


if __name__ == "__main__":
    unittest.main()
