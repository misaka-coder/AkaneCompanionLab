import unittest
from unittest.mock import patch

from companion_v01 import mcp_stdio_discoverer


class McpStdioCommandResolutionTests(unittest.TestCase):
    def test_windows_cmd_file_is_executed_directly(self) -> None:
        with patch.object(mcp_stdio_discoverer.sys, "platform", "win32"), patch.object(
            mcp_stdio_discoverer.shutil,
            "which",
            return_value=r"D:\Program Files\nodejs\npx.CMD",
        ):
            exe, prefix_args = mcp_stdio_discoverer._resolve_stdio_command("npx")

        self.assertEqual(exe, r"D:\Program Files\nodejs\npx.CMD")
        self.assertEqual(prefix_args, [])

    def test_non_windows_command_keeps_resolved_executable(self) -> None:
        with patch.object(mcp_stdio_discoverer.sys, "platform", "linux"), patch.object(
            mcp_stdio_discoverer.shutil,
            "which",
            return_value="/usr/bin/npx",
        ):
            exe, prefix_args = mcp_stdio_discoverer._resolve_stdio_command("npx")

        self.assertEqual(exe, "/usr/bin/npx")
        self.assertEqual(prefix_args, [])


class McpTransportFacadeTests(unittest.IsolatedAsyncioTestCase):
    async def test_streamable_http_call_expands_header_placeholder_and_uses_sdk_client(self) -> None:
        class FakeHttpClient:
            def __init__(self) -> None:
                self.server = None

            async def call_tool(self, server, tool_name, arguments):
                self.server = server
                return {"content": [{"type": "text", "text": "ok"}], "isError": False}

        fake = FakeHttpClient()
        caller = mcp_stdio_discoverer.McpToolCaller(timeout_seconds=9)
        caller._streamable_http = fake

        with patch.dict(mcp_stdio_discoverer.os.environ, {"ANYSEARCH_API_KEY": "test-key"}, clear=False):
            result = await caller(
                server={
                    "serverId": "search",
                    "enabled": True,
                    "transport": "streamable_http",
                    "url": "https://mcp.example.test/rpc",
                    "headers": {"Authorization": "Bearer ${ANYSEARCH_API_KEY}"},
                },
                tool_name="search",
                arguments={"query": "Akane"},
            )

        self.assertFalse(result["isError"])
        self.assertEqual(fake.server.headers, {"Authorization": "Bearer test-key"})
        self.assertEqual(fake.server.url, "https://mcp.example.test/rpc")

    async def test_streamable_http_discovery_projects_sdk_tool_records(self) -> None:
        class FakeHttpClient:
            async def list_tools(self, server):
                del server
                return (
                    mcp_stdio_discoverer.McpToolRecord(
                        name="search",
                        description="Search public pages.",
                        input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
                    ),
                )

        discoverer = mcp_stdio_discoverer.McpToolDiscoverer(timeout_seconds=9)
        discoverer._streamable_http = FakeHttpClient()

        result = await discoverer(
            server={
                "serverId": "search",
                "enabled": True,
                "transport": "streamable_http",
                "url": "https://mcp.example.test/rpc",
            }
        )

        self.assertEqual(result["tools"][0]["name"], "search")
        self.assertEqual(result["tools"][0]["inputSchema"]["properties"]["query"]["type"], "string")


if __name__ == "__main__":
    unittest.main()
