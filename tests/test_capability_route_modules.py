from __future__ import annotations

import ast
import asyncio
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from fastapi import FastAPI

from companion_v01.routes.capabilities import build_capabilities_router, _run_provider_tts_test


class CapabilityRouteModuleTests(unittest.TestCase):
    def test_implementation_imports_are_acyclic_and_do_not_import_the_facade(self):
        package = Path(__file__).resolve().parents[1] / "companion_v01/routes/capabilities_runtime"
        graph = {path.stem: set() for path in package.glob("*.py")}
        for path in package.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    self.assertNotIn(node.module, {"capabilities", "companion_v01.routes.capabilities"}, str(path))
                    if node.level == 1 and node.module in graph:
                        graph[path.stem].add(node.module)
                elif isinstance(node, ast.Import):
                    self.assertNotIn("companion_v01.routes.capabilities", [alias.name for alias in node.names])
        active, done = set(), set()

        def visit(module):
            self.assertNotIn(module, active, "dependency cycle")
            if module in done:
                return
            active.add(module)
            for dependency in graph[module]:
                visit(dependency)
            active.remove(module)
            done.add(module)

        for module in graph:
            visit(module)


class CapabilityRouteCallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_synchronous_environment_probe_keeps_the_event_loop_responsive(self):
        started, release = threading.Event(), threading.Event()

        def probe():
            started.set()
            if not release.wait(3):
                raise TimeoutError("probe_release_timeout")
            return {"status": "ready", "services": []}

        with tempfile.TemporaryDirectory() as directory:
            app = FastAPI()
            app.include_router(build_capabilities_router(
                engine=SimpleNamespace(), capability_config_base_dir=directory, local_environment_probe=probe,
            ))
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                task = asyncio.create_task(client.post("/capabilities/local-environment-check"))
                try:
                    self.assertTrue(await asyncio.to_thread(started.wait, 2))
                    self.assertFalse(task.done(), "the probe blocked the event loop until its timeout")
                    release.set()
                    response = await asyncio.wait_for(task, 5)
                    self.assertEqual(response.json()["status"], "ready")
                finally:
                    release.set()
                    await task

    async def test_environment_probe_failure_keeps_details_out_of_response_and_log(self):
        sentinel = "private-service-path-and-token-fixture"
        events = []

        def probe():
            raise RuntimeError(sentinel)

        def log_event(event, **fields):
            events.append((event, fields))

        with tempfile.TemporaryDirectory() as directory:
            app = FastAPI()
            app.include_router(build_capabilities_router(
                engine=SimpleNamespace(), capability_config_base_dir=directory,
                local_environment_probe=probe, log_event=log_event,
            ))
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post("/capabilities/local-environment-check")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(sentinel, response.text)
        self.assertNotIn(sentinel, str(events))
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["reason"], "local_environment_check_failed")
        self.assertEqual(payload["error"], "RuntimeError")
        self.assertFalse(payload["autoEnable"])
        self.assertEqual(events[-1][1]["error"], "RuntimeError")

    async def test_sync_tts_runner_uses_a_worker_and_async_runner_stays_on_the_loop(self):
        loop_thread = threading.get_ident()
        observed = []

        def sync_runner(**_kwargs):
            observed.append(threading.get_ident())
            return b"audio-fixture"

        async def async_runner(**_kwargs):
            self.assertEqual(threading.get_ident(), loop_thread)
            return b"audio-fixture"

        for runner in (sync_runner, async_runner):
            with self.subTest(runner=runner.__name__):
                result = await _run_provider_tts_test(
                    base_dir=None, profile_user_id="fixture", provider_id="provider.tts.gpt_sovits.local",
                    payload={"endpoint": "http://127.0.0.1:9880", "text": "测试"}, runner=runner,
                )
                self.assertTrue(result["ok"])
        self.assertNotEqual(observed, [loop_thread])

    async def test_synchronous_mcp_discovery_uses_a_worker(self):
        from companion_v01.routes.capabilities_runtime.mcp import _discover_mcp_server_tools

        observed = []

        def discoverer(**_kwargs):
            observed.append(threading.get_ident())
            return {"tools": []}

        with (
            patch("companion_v01.routes.capabilities_runtime.mcp.get_mcp_server_runtime_config", return_value={"serverId": "fixture", "enabled": True}),
            patch("companion_v01.routes.capabilities_runtime.mcp.save_mcp_server_discovery", return_value={"ok": True}) as persist,
        ):
            result = await _discover_mcp_server_tools(
                base_dir=None, profile_user_id="fixture", server_id="fixture", discoverer=discoverer,
            )
        self.assertTrue(result["ok"])
        self.assertNotEqual(observed, [threading.get_ident()])
        self.assertEqual(persist.call_args.kwargs["payload"], {"tools": []})


if __name__ == "__main__":
    unittest.main()
