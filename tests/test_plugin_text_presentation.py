from __future__ import annotations

import json
import unittest

from companion_v01.plugin_text_presentation import (
    apply_plugin_text_presentation_policy,
    strip_leading_addresses,
)


class PluginTextPresentationTests(unittest.TestCase):
    def test_strips_longest_declared_address_and_separator(self) -> None:
        self.assertEqual(
            strip_leading_addresses(
                "亲爱的主人，你关注的公告有更新。",
                ("主人", "亲爱的主人"),
            ),
            "你关注的公告有更新。",
        )

    def test_only_changes_the_leading_address(self) -> None:
        self.assertEqual(
            strip_leading_addresses("新闻主人公公布新计划。", ("主人",)),
            "新闻主人公公布新计划。",
        )

    def test_sanitizes_speech_and_first_segment_consistently(self) -> None:
        frame = {
            "speech": "主人，这条快讯值得关注。",
            "speech_segments": ["主人，", "这条快讯值得关注。"],
            "_provider_output_raw": json.dumps(
                {
                    "emotion": "thinking",
                    "speech": "主人，这条快讯值得关注。",
                    "speech_segments": ["主人，", "这条快讯值得关注。"],
                },
                ensure_ascii=False,
            ),
        }
        apply_plugin_text_presentation_policy(
            frame,
            strip_leading_addresses_from=("主人",),
        )
        self.assertEqual(frame["speech"], "这条快讯值得关注。")
        self.assertEqual(frame["speech_segments"], ["这条快讯值得关注。"])
        provider_output = json.loads(frame["_provider_output_raw"])
        self.assertEqual(provider_output["speech"], "这条快讯值得关注。")
        self.assertEqual(provider_output["speech_segments"], ["这条快讯值得关注。"])


if __name__ == "__main__":
    unittest.main()
