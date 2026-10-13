"""Discovery races and last-confirmed declarations in the real host manager."""
from __future__ import annotations

import asyncio
import unittest

from capcore_adapter_mcp import McpClientError, McpToolRecord
from companion_v01.mcp_host_manager import McpHostManager


def record(description):
    return McpToolRecord(name="echo", description=description,
                         input_schema={"type": "object"})


class DiscoveryTransport:
    def __init__(self):
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0
        self.failure = False

    async def list_tools(self, server):
        self.calls += 1
        self.entered.set()
        await self.release.wait()
        if self.failure:
            raise McpClientError("offline")
        return (record("Confirmed echo"),)


class McpHostCatalogLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.manager = McpHostManager()
        self.transport = DiscoveryTransport()
        self.manager._stdio = self.transport
        self.config = {"transport": "stdio", "command": "controlled-mcp", "enabled": True,
                       "tools": [{"name": "echo", "description": "Old echo",
                                  "inputSchema": {"type": "object"}}]}

    def tearDown(self):
        self.assertTrue(self.manager.close())

    async def discover(self):
        return await self.manager._discover(profile_user_id="alice", server_id="demo",
                                            server_config=self.config)

    async def test_concurrent_initial_discovery_is_shared_and_failure_keeps_declarations(self):
        tasks = [asyncio.create_task(self.discover()) for _ in range(10)]
        await self.transport.entered.wait()
        self.transport.release.set()
        results = await asyncio.gather(*tasks)
        self.assertEqual(self.transport.calls, 1)
        self.assertTrue(all(value["tools"][0]["description"] == "Confirmed echo" for value in results))
        self.transport.failure = True
        with self.assertRaises(McpClientError):
            await self.discover()
        tools = self.manager.confirmed_tools(profile_user_id="alice", server_id="demo", server_config=self.config)
        self.assertEqual(tools[0]["description"], "Confirmed echo")
        self.assertEqual(self.manager.status(profile_user_id="alice", server_id="demo")["status"], "error")

    async def test_slow_discovery_cannot_restore_old_config_or_health_after_restart(self):
        task = asyncio.create_task(self.discover())
        await self.transport.entered.wait()
        self.manager.stop_server(profile_user_id="alice", server_id="demo", server_config=self.config,
                                 disabled=True)
        self.transport.release.set()
        with self.assertRaisesRegex(McpClientError, "mcp_config_changed_during_discovery"):
            await task
        self.assertIsNone(self.manager.confirmed_tools(profile_user_id="alice", server_id="demo",
                                                      server_config=self.config))
        self.assertEqual(self.manager.status(profile_user_id="alice", server_id="demo")["status"], "disabled")

    async def test_cancelled_waiter_does_not_cancel_another_waiters_discovery(self):
        abandoned = asyncio.create_task(self.discover())
        await self.transport.entered.wait()
        remaining = asyncio.create_task(self.discover())
        abandoned.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await abandoned
        self.transport.release.set()
        self.assertEqual((await remaining)["tools"][0]["description"], "Confirmed echo")
        self.assertEqual(self.transport.calls, 1)

    async def test_profile_cache_does_not_override_newer_shared_declaration(self):
        self.transport.release.set()
        await self.discover()
        newer = {**self.config, "tools": [{"name": "echo", "description": "Shared newer echo",
                                          "inputSchema": {"type": "object"}}]}
        self.assertIsNone(self.manager.confirmed_tools(profile_user_id="alice", server_id="demo",
                                                      server_config=newer))
        current = {**self.config, "tools": [{"name": "echo", "description": "Confirmed echo",
                                            "inputSchema": {"type": "object"}, "modelVisible": False}]}
        self.assertEqual(self.manager.confirmed_tools(profile_user_id="alice", server_id="demo",
                                                       server_config=current)[0]["description"], "Confirmed echo")
