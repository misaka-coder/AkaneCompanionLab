from __future__ import annotations

import unittest

from companion_v01.capability_adapters import (
    CapabilityProtocolError,
    InvocationContext,
    McpStdioCapabilityAdapter,
)
from companion_v01.mcp_stdio_discoverer import McpStdioDiscoveryError


class FakeCaller:
    def __init__(self, result=None, error: Exception | None = None) -> None:
        self.result = result if result is not None else {"content": [{"type": "text", "text": "ok"}]}
        self.error = error
        self.calls: list[dict] = []

    async def __call__(self, *, server, tool_name, arguments):
        self.calls.append({"server": server, "tool_name": tool_name, "arguments": arguments})
        if self.error:
            raise self.error
        return self.result


def build_adapter(*, caller=None, low_risk_allowlist=None, risk="low", prompt_exposed=True):
    server_config = {
        "serverId": "demo",
        "enabled": True,
        "transport": "stdio",
        "command": "python",
        "lowRiskAllowlist": list(low_risk_allowlist or []),
    }
    tool = {
        "name": "echo",
        "description": "Echo text",
        "risk": risk,
        "confirm": "never",
        "promptExposed": prompt_exposed,
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "Text to echo"}},
            "required": ["text"],
        },
    }
    return McpStdioCapabilityAdapter(
        provider_id="provider.mcp.demo",
        server_id="demo",
        server_config=server_config,
        tool_configs=(tool,),
        caller=caller or FakeCaller(),
    )


class McpStdioCapabilityAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_capabilities_uses_namespaced_id_and_schema(self) -> None:
        adapter = build_adapter(low_risk_allowlist=["echo"])
        capabilities = await adapter.list_capabilities()
        self.assertEqual(capabilities[0].id, "mcp.demo.echo")
        self.assertTrue(capabilities[0].prompt_exposed)
        self.assertEqual(capabilities[0].inputs[0].name, "text")
        self.assertEqual(capabilities[0].risk, "low")

    async def test_dynamic_low_risk_promotes_without_allowlist(self) -> None:
        adapter = build_adapter(low_risk_allowlist=[])
        capability = (await adapter.list_capabilities())[0]
        self.assertEqual(capability.risk, "medium")

    async def test_unknown_capability_raises_protocol_error(self) -> None:
        adapter = build_adapter()
        with self.assertRaises(CapabilityProtocolError):
            await adapter.invoke("mcp.demo.missing", {}, InvocationContext())

    async def test_invoke_success_returns_capability_result(self) -> None:
        caller = FakeCaller({"content": [{"type": "text", "text": "hello"}]})
        adapter = build_adapter(caller=caller, low_risk_allowlist=["echo"])
        result = await adapter.invoke("mcp.demo.echo", {"text": "hello"}, InvocationContext())
        self.assertFalse(result.is_error)
        self.assertEqual(caller.calls[0]["tool_name"], "echo")
        self.assertEqual(caller.calls[0]["arguments"], {"text": "hello"})

    async def test_mcp_is_error_returns_business_error_result(self) -> None:
        adapter = build_adapter(caller=FakeCaller({"isError": True, "content": [{"type": "text", "text": "bad"}]}))
        result = await adapter.invoke("mcp.demo.echo", {"text": "x"}, InvocationContext())
        self.assertTrue(result.is_error)
        self.assertEqual(result.status, "error")

    async def test_caller_error_raises_protocol_error(self) -> None:
        adapter = build_adapter(caller=FakeCaller(error=McpStdioDiscoveryError("mcp_tool_call_timeout")))
        with self.assertRaises(CapabilityProtocolError):
            await adapter.invoke("mcp.demo.echo", {"text": "x"}, InvocationContext())


if __name__ == "__main__":
    unittest.main()
