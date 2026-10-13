"""Concurrent MCP requests must share one Host event loop."""

from concurrent.futures import ThreadPoolExecutor
import threading
import time
import unittest
from unittest.mock import patch

from companion_v01.mcp_host_manager import McpHostManager


class McpHostManagerStartupTests(unittest.TestCase):
    def test_concurrent_start_requests_create_one_worker(self) -> None:
        manager = McpHostManager()
        starts: list[int] = []
        barrier = threading.Barrier(8)

        def delayed_worker() -> None:
            starts.append(1)
            time.sleep(0.05)
            manager._ready.set()

        def request_start(_index: int) -> None:
            barrier.wait()
            manager._ensure_started()

        with patch.object(manager, "_run_loop", side_effect=delayed_worker):
            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(request_start, range(8)))

        self.assertEqual(len(starts), 1)
        self.assertTrue(manager.close())


if __name__ == "__main__":
    unittest.main()
