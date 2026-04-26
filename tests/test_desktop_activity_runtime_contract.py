from __future__ import annotations

import unittest
from pathlib import Path

from companion_v01.client_protocol import (
    ClientCapability,
    ClientMode,
    ClientProtocolContext,
)
from companion_v01.engine import AkaneMemoryEngine


def _desktop_context(*, with_audio: bool = True) -> ClientProtocolContext:
    capabilities = [
        ClientCapability.SPEECH_SEGMENTS.value,
        ClientCapability.FILE_DROP.value,
        ClientCapability.TOOL_ACTIONS.value,
    ]
    if with_audio:
        capabilities.append(ClientCapability.AUDIO_PLAYBACK.value)
    return ClientProtocolContext(
        requested_mode=ClientMode.DESKTOP_PET,
        effective_mode=ClientMode.DESKTOP_PET,
        capabilities=tuple(capabilities),
        output_profile=ClientMode.DESKTOP_PET.value,
        renderer_profile=ClientMode.DESKTOP_PET.value,
    )


class DesktopActivityRuntimeContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)

    def test_audio_playback_prompt_does_not_imply_interruption(self) -> None:
        prompt = self.engine._build_desktop_activity_prompt(
            {
                "type": "audio_playback",
                "status": "running",
                "title": "雨夜的小歌.mp3",
                "source_id": "file_012",
                "progress_seconds": 37,
                "duration_seconds": 222,
            },
            _desktop_context(),
        )

        self.assertIn("【当前桌宠活动】", prompt)
        self.assertIn("类型：普通音频播放", prompt)
        self.assertIn("雨夜的小歌.mp3", prompt)
        self.assertIn("进度 00:37 / 03:42", prompt)
        self.assertIn("普通音频不会因为本轮消息自动暂停", prompt)
        self.assertNotIn("主人发消息时表演已暂停", prompt)

    def test_vocal_performance_interrupted_prompt_requires_activity_action(self) -> None:
        prompt = self.engine._build_desktop_activity_prompt(
            {
                "type": "vocal_performance",
                "status": "interrupted",
                "title": "Akane 翻唱.wav",
                "source_id": "gen_018",
                "progress_seconds": 77,
                "duration_seconds": 205,
            },
            _desktop_context(),
        )

        self.assertIn("类型：Akane 表演/唱歌", prompt)
        self.assertIn("状态：因主人发来消息已暂停", prompt)
        self.assertIn("进度 01:17 / 03:25", prompt)
        self.assertIn("如果你想继续表演，需要输出 activity action", prompt)
        self.assertIn('"action":"play|pause|resume|stop"', prompt)

    def test_activity_prompt_is_desktop_audio_capability_only(self) -> None:
        activity = {
            "type": "audio_playback",
            "status": "running",
            "title": "test.mp3",
        }
        qq_context = ClientProtocolContext(
            requested_mode=ClientMode.QQ_TEXT,
            effective_mode=ClientMode.QQ_TEXT,
            capabilities=(ClientCapability.SPEECH_SEGMENTS.value,),
            output_profile=ClientMode.QQ_TEXT.value,
            renderer_profile=ClientMode.QQ_TEXT.value,
        )

        self.assertEqual(self.engine._build_desktop_activity_prompt(activity, qq_context), "")
        self.assertEqual(
            self.engine._build_desktop_activity_prompt(activity, _desktop_context(with_audio=False)),
            "",
        )

    def test_normalize_activity_action_keeps_only_safe_actions(self) -> None:
        action = self.engine._normalize_activity_action(
            {
                "action": "resume",
                "target": "current",
                "type": "vocal_performance",
                "source_id": "gen_018",
            }
        )

        self.assertEqual(
            action,
            {
                "action": "resume",
                "target": "current",
                "type": "vocal_performance",
                "source_id": "gen_018",
            },
        )
        self.assertIsNone(self.engine._normalize_activity_action({"action": "continue"}))

    def test_desktop_runtime_only_interrupts_vocal_performance(self) -> None:
        runtime_path = Path("desktop_pet/renderer/services/ActivityRuntime.js")
        source = runtime_path.read_text(encoding="utf-8")

        self.assertIn('interruptForUserMessage()', source)
        self.assertIn('this._current?.type === "vocal_performance"', source)
        self.assertIn('this._current.status === "running"', source)
        self.assertIn('nextStatus: "interrupted"', source)
        self.assertNotIn('this._current?.type === "audio_playback" && this._current.status === "running"', source)


if __name__ == "__main__":
    unittest.main()
