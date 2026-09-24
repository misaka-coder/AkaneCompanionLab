import unittest
from types import SimpleNamespace

from scripts.tools.provider_tool_probe import probe_model


class ProviderToolProbeTests(unittest.TestCase):
    def test_probe_never_combines_native_tools_with_forced_json(self) -> None:
        payloads: list[dict] = []

        def create(**payload):
            payloads.append(payload)
            if payload.get("tools"):
                message = SimpleNamespace(
                    content="",
                    tool_calls=[
                        SimpleNamespace(
                            id="call-1",
                            function=SimpleNamespace(name="web_search", arguments='{"query":"上海天气"}'),
                        )
                    ],
                )
                finish_reason = "tool_calls"
            else:
                message = SimpleNamespace(content='{"speech":"巴黎","tool_call":null}', tool_calls=[])
                finish_reason = "stop"
            return SimpleNamespace(
                choices=[SimpleNamespace(message=message, finish_reason=finish_reason)]
            )

        client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )
        report = probe_model(
            client=client,
            base_url="https://api.example.test/v1",
            protocol="openai",
            model="example-model",
            temperature=0.0,
        )

        self.assertEqual(len(payloads), 3)
        self.assertTrue(all("response_format" not in payload for payload in payloads))
        self.assertEqual([case["id"] for case in report["cases"]], [
            "A_only_tools",
            "B_tools_prompt_json",
            "C_no_tool_prompt_json",
        ])
        self.assertTrue(report["suggested_profile"]["supports_native_tools"])
        self.assertTrue(report["suggested_profile"]["probe_passed_prompt_json_no_tool"])
        self.assertNotIn("native_tools_coexist_with_forced_json", report["suggested_profile"])


if __name__ == "__main__":
    unittest.main()
