from __future__ import annotations

from types import SimpleNamespace
import unittest

from companion_v01.engine_services.tool_rounds import resolve_tool_handlers
from companion_v01.tool_readiness import ToolReadinessGate


class _StatusHandler:
    def __init__(self, status, *, raises: bool = False) -> None:
        self.status_value = status
        self.raises = raises
        self.calls: list[dict[str, str]] = []

    def capability_status(self, **context):
        self.calls.append(dict(context))
        if self.raises:
            raise RuntimeError("probe failed")
        return self.status_value


class ToolReadinessGateTests(unittest.TestCase):
    def test_gate_keeps_handlers_without_status_and_hides_unavailable_handlers(self) -> None:
        ready = object()
        unavailable = _StatusHandler({"enabled": False, "status": "unavailable", "reason": "upstream_unreachable"})
        gate = ToolReadinessGate()

        visible = gate.filter_handlers({"ready": ready, "dead": unavailable})

        self.assertEqual(visible, {"ready": ready})

    def test_gate_passes_runtime_context_and_caches_probe_result(self) -> None:
        handler = _StatusHandler({"enabled": True, "status": "ready", "reason": ""})
        gate = ToolReadinessGate()

        first = gate.filter_handlers(
            {"web_search": handler},
            profile_user_id="owner",
            session_id="session",
            client_mode="qq_text",
        )
        second = gate.filter_handlers(
            {"web_search": handler},
            profile_user_id="owner",
            session_id="session",
            client_mode="qq_text",
        )

        self.assertIn("web_search", first)
        self.assertIn("web_search", second)
        self.assertEqual(len(handler.calls), 1)
        self.assertEqual(
            handler.calls[0],
            {"profile_user_id": "owner", "session_id": "session", "client_mode": "qq_text"},
        )

    def test_probe_exception_fails_closed(self) -> None:
        handler = _StatusHandler({}, raises=True)

        visible = ToolReadinessGate().filter_handlers({"broken": handler})

        self.assertNotIn("broken", visible)

    def test_engine_resolver_applies_readiness_to_model_visible_handlers(self) -> None:
        ready = object()
        unavailable = _StatusHandler({"enabled": False, "status": "disabled", "reason": "off"})
        engine = SimpleNamespace(tool_handlers={"ready_tool": ready, "dead_tool": unavailable})

        visible = resolve_tool_handlers(engine)

        self.assertEqual(visible, {"ready_tool": ready})


if __name__ == "__main__":
    unittest.main()
