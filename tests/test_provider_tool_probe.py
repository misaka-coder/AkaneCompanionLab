import unittest

from companion_v01.llm_runtime import LLMRuntime
from scripts.tools.provider_tool_probe import suggested_allowlist_entry


class SuggestedAllowlistEntryTests(unittest.TestCase):
    """The probe prints a ready-to-paste NATIVE_TOOL_PROVIDER_ALLOWLIST entry.

    The point of these tests is to prove the printed string is the *same* shape
    the runtime gate actually parses — otherwise the convenience would be a fake
    feature (a string nobody can use). The parser methods on LLMRuntime are
    stateless, so we exercise them on an instance built without __init__.
    """

    def setUp(self) -> None:
        self.runtime = LLMRuntime.__new__(LLMRuntime)

    def _parse(self, item: str):
        return self.runtime._parse_native_tool_provider_allowlist_item(item)

    def test_unsupported_yields_no_entry_fail_closed(self) -> None:
        # No native support -> empty entry -> runtime keeps prompt-only JSON.
        entry = suggested_allowlist_entry(
            base_url="https://api.example.com",
            model="some-model",
            suggested={
                "supports_native_tools": False,
                "native_tools_coexist_with_forced_json": True,
            },
        )
        self.assertEqual(entry, "")

    def test_supported_without_json_round_trips_through_runtime(self) -> None:
        entry = suggested_allowlist_entry(
            base_url="https://api.deepseek.com/v1",
            model="Deepseek-V4-Flash",
            suggested={
                "supports_native_tools": True,
                "native_tools_coexist_with_forced_json": False,
            },
        )
        self.assertEqual(entry, "api.deepseek.com:deepseek-v4-flash")
        host, model, coexist = self._parse(entry)
        self.assertEqual(host, "api.deepseek.com")
        self.assertEqual(model, "deepseek-v4-flash")
        self.assertFalse(coexist)

    def test_supported_with_json_round_trips_through_runtime(self) -> None:
        entry = suggested_allowlist_entry(
            base_url="https://api.deepseek.com",
            model="deepseek-v4-pro",
            suggested={
                "supports_native_tools": True,
                "native_tools_coexist_with_forced_json": True,
            },
        )
        self.assertEqual(entry, "api.deepseek.com:deepseek-v4-pro:json")
        host, model, coexist = self._parse(entry)
        self.assertEqual(host, "api.deepseek.com")
        self.assertEqual(model, "deepseek-v4-pro")
        self.assertTrue(coexist)


if __name__ == "__main__":
    unittest.main()
