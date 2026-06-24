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


if __name__ == "__main__":
    unittest.main()
