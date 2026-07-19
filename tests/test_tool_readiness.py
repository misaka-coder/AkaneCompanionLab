from __future__ import annotations

import unittest

from companion_v01.capability_registry import (
    CapabilityModule,
    CapabilityRegistry,
    CapabilitySnapshot,
    ServerLocalOfferIndex,
)
from companion_v01.client_protocol import ClientMode


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


class ServerLocalOfferIndexTests(unittest.TestCase):
    def test_static_handler_is_ready_but_unknown_tool_fails_closed(self) -> None:
        index = ServerLocalOfferIndex()
        index.register("ready", object())

        self.assertTrue(index.is_offered("ready"))
        self.assertFalse(index.is_offered("unknown"))

    def test_probe_receives_runtime_context_and_caches_result(self) -> None:
        handler = _StatusHandler({"enabled": True, "status": "ready", "reason": ""})
        index = ServerLocalOfferIndex()
        index.register("web_search", handler)

        first = index.is_offered(
            "web_search",
            profile_user_id="owner",
            session_id="session",
            client_mode="qq_text",
        )
        second = index.is_offered(
            "web_search",
            profile_user_id="owner",
            session_id="session",
            client_mode="qq_text",
        )

        self.assertTrue(first)
        self.assertTrue(second)
        self.assertEqual(len(handler.calls), 1)
        self.assertEqual(
            handler.calls[0],
            {"profile_user_id": "owner", "session_id": "session", "client_mode": "qq_text"},
        )

    def test_probe_exception_and_unavailable_status_fail_closed(self) -> None:
        index = ServerLocalOfferIndex()
        index.register("broken", _StatusHandler({}, raises=True))
        index.register(
            "disabled",
            _StatusHandler({"enabled": False, "status": "unavailable", "reason": "upstream_unreachable"}),
        )

        self.assertFalse(index.is_offered("broken"))
        self.assertFalse(index.is_offered("disabled"))

    def test_malformed_probe_results_fail_closed(self) -> None:
        index = ServerLocalOfferIndex()
        index.register("missing_status", _StatusHandler({"enabled": True}))
        index.register("missing_enabled", _StatusHandler({"status": "ready"}))
        index.register("string_status", _StatusHandler("ready"))

        self.assertFalse(index.is_offered("missing_status"))
        self.assertFalse(index.is_offered("missing_enabled"))
        self.assertFalse(index.is_offered("string_status"))

    def test_replacing_handler_map_invalidates_old_offer(self) -> None:
        index = ServerLocalOfferIndex()
        index.replace_handlers({"tool": _StatusHandler({"enabled": True, "status": "ready"})})
        self.assertTrue(index.is_offered("tool"))

        index.replace_handlers({"tool": _StatusHandler({"enabled": False, "status": "unavailable"})})

        self.assertFalse(index.is_offered("tool"))

    def test_readiness_cache_is_partitioned_by_profile_and_session(self) -> None:
        class ProfileHandler:
            def capability_status(self, *, profile_user_id: str, session_id: str, client_mode: str):
                del client_mode
                return {
                    "enabled": profile_user_id == "alice" and session_id == "shared",
                    "status": "ready" if profile_user_id == "alice" else "unavailable",
                }

        index = ServerLocalOfferIndex()
        index.register("profile_tool", ProfileHandler())

        self.assertTrue(
            index.is_offered(
                "profile_tool",
                profile_user_id="alice",
                session_id="shared",
                client_mode="desktop_pet",
            )
        )
        self.assertFalse(
            index.is_offered(
                "profile_tool",
                profile_user_id="bob",
                session_id="shared",
                client_mode="desktop_pet",
            )
        )

    def test_transient_unavailable_status_does_not_remove_configured_tool_schema(self) -> None:
        handler = _StatusHandler({"enabled": False, "status": "checking", "reason": "probe_pending"})
        index = ServerLocalOfferIndex()
        index.replace_handlers({"web_search": handler})
        registry = CapabilityRegistry(
            modules=(
                CapabilityModule(
                    name="internet_access",
                    layer="web",
                    modes=(ClientMode.QQ_TEXT,),
                    tools=("web_search",),
                    light_hint="联网搜索",
                    trigger=lambda _snapshot: True,
                    unavailable_reason="搜索服务正在检查。",
                    recovery_hint="检查完成后即可执行。",
                ),
            ),
            server_offer_index=index,
        )

        selection = registry.select(
            CapabilitySnapshot(client_mode=ClientMode.QQ_TEXT),
            allowed_tool_names=("web_search",),
            profile_user_id="owner",
            session_id="conversation",
        )

        self.assertEqual(selection.tool_names, ())
        self.assertEqual(selection.schema_tool_names, ("web_search",))
        self.assertIn("unavailable", {item.state for item in selection.disclosures})


if __name__ == "__main__":
    unittest.main()
