from __future__ import annotations

import threading
import unittest
from contextlib import contextmanager

from companion_v01.capability_registry import (
    CapabilityModule,
    CapabilityRegistry,
    CapabilitySelection,
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


class _BlockedFirstProbe:
    def __init__(self, status, *, following_status=None) -> None:
        self.status_value = status
        self.following_status = following_status
        self.calls: list[dict[str, str]] = []
        self.started = threading.Event()
        self.release = threading.Event()
        self.completed = threading.Event()

    def capability_status(self, **context):
        self.calls.append(dict(context))
        first = len(self.calls) == 1
        captured = self.status_value if first or self.following_status is None else self.following_status
        if first:
            self.started.set()
            if not self.release.wait(3):
                raise RuntimeError("test probe was not released")
        return captured


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

    def test_configured_offer_keeps_execution_available_while_probe_is_checking(self) -> None:
        handler = _StatusHandler(
            {"offered": True, "enabled": True, "status": "checking", "reason": "probe_pending"}
        )
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

        self.assertEqual(selection.tool_names, ("web_search",))
        self.assertEqual(selection.schema_tool_names, ("web_search",))
        self.assertIn("ready", {item.state for item in selection.disclosures})

    def test_explicitly_disabled_offer_stays_unavailable(self) -> None:
        handler = _StatusHandler(
            {"offered": False, "enabled": False, "status": "disabled", "reason": "search_disabled"}
        )
        index = ServerLocalOfferIndex()
        index.register("web_search", handler)

        self.assertFalse(index.is_offered("web_search"))

    def test_ready_and_unavailable_results_expire_at_their_separate_deadlines(self) -> None:
        now = [0.0]
        handler = _StatusHandler(True)
        index = ServerLocalOfferIndex(clock=lambda: now[0])
        index.register("tool", handler)
        self.assertTrue(index.is_offered("tool"))
        handler.status_value = False
        now[0] = 14.9
        self.assertTrue(index.is_offered("tool"))
        now[0] = 15.0
        self.assertFalse(index.is_offered("tool"))
        handler.status_value = True
        now[0] = 19.9
        self.assertFalse(index.is_offered("tool"))
        now[0] = 20.0
        self.assertTrue(index.is_offered("tool"))
        self.assertEqual(len(handler.calls), 3)

    def test_client_modes_have_independent_cache_entries(self) -> None:
        class ModeHandler(_StatusHandler):
            def capability_status(self, **context):
                self.calls.append(dict(context))
                return context["client_mode"] == "desktop_pet"

        handler = ModeHandler(None)
        index = ServerLocalOfferIndex()
        index.register("tool", handler)
        scope = {"profile_user_id": "owner", "session_id": "same-session"}
        self.assertTrue(index.is_offered("tool", client_mode="desktop_pet", **scope))
        self.assertFalse(index.is_offered("tool", client_mode="qq_text", **scope))
        self.assertTrue(index.is_offered("tool", client_mode="desktop_pet", **scope))
        self.assertEqual(len(handler.calls), 2)

    def test_broken_probe_property_fails_closed(self) -> None:
        class BrokenHandler:
            @property
            def capability_status(self):
                raise RuntimeError("probe unavailable")

        index = ServerLocalOfferIndex()
        index.register("broken", BrokenHandler())
        self.assertFalse(index.is_offered("broken"))


class ServerLocalOfferConcurrencyTests(unittest.TestCase):
    @contextmanager
    def running_probe(self, index, handler, **scope):
        results = []
        errors = []

        def probe():
            try:
                results.append(index.is_offered("tool", **scope))
            except Exception as exc:
                errors.append(type(exc).__name__)
            finally:
                handler.completed.set()

        thread = threading.Thread(target=probe, daemon=True)
        thread.start()
        try:
            self.assertTrue(handler.started.wait(3), "first probe never started")
            yield results
        finally:
            handler.release.set()
            thread.join(3)
            self.assertFalse(thread.is_alive(), "probe did not finish after release")
            self.assertEqual(errors, [])

    def test_late_ready_probe_cannot_reopen_a_replaced_disabled_handler(self) -> None:
        old = _BlockedFirstProbe(True)
        current = _StatusHandler(False)
        index = ServerLocalOfferIndex()
        index.register("tool", old)
        with self.running_probe(index, old) as results:
            index.register("tool", current)
            self.assertFalse(index.is_offered("tool"))
        self.assertEqual(results, [False])
        self.assertFalse(index.is_offered("tool"))
        self.assertEqual(len(current.calls), 1)

    def test_late_unavailable_probe_cannot_hide_a_replacement_that_is_ready(self) -> None:
        old = _BlockedFirstProbe(False)
        current = _StatusHandler(True)
        index = ServerLocalOfferIndex()
        index.register("tool", old)
        with self.running_probe(index, old) as results:
            index.replace_handlers({"tool": current})
            self.assertTrue(index.is_offered("tool"))
        self.assertEqual(results, [False])
        self.assertTrue(index.is_offered("tool"))
        self.assertEqual(len(current.calls), 1)

    def test_removing_a_handler_rejects_its_unfinished_probe(self) -> None:
        old = _BlockedFirstProbe(True)
        index = ServerLocalOfferIndex()
        index.register("tool", old)
        with self.running_probe(index, old) as results:
            index.replace_handlers({})
            self.assertFalse(index.is_offered("tool"))
        self.assertEqual(results, [False])
        self.assertFalse(index.is_offered("tool"))

    def test_reinstalling_the_same_object_cannot_reuse_an_old_probe(self) -> None:
        old = _BlockedFirstProbe(True)
        index = ServerLocalOfferIndex()
        index.register("tool", old)
        with self.running_probe(index, old) as results:
            index.replace_handlers({"tool": _StatusHandler(False)})
            old.status_value = False
            index.replace_handlers({"tool": old})
        self.assertEqual(results, [False])
        self.assertFalse(index.is_offered("tool"))

    def test_explicit_reregistration_invalidates_a_probe_of_the_same_handler(self) -> None:
        handler = _BlockedFirstProbe(True)
        index = ServerLocalOfferIndex()
        index.register("tool", handler)
        with self.running_probe(index, handler) as results:
            handler.status_value = False
            index.register("tool", handler)
        self.assertEqual(results, [False])
        self.assertFalse(index.is_offered("tool"))

    def test_unchanged_map_refresh_keeps_inflight_probes_and_cached_offers(self) -> None:
        handler = _BlockedFirstProbe(True)
        index = ServerLocalOfferIndex()
        index.replace_handlers({"tool": handler})
        with self.running_probe(index, handler) as results:
            index.replace_handlers({"tool": handler, "other": object()})
            self.assertTrue(index.is_offered("other"))
        self.assertEqual(results, [True])
        self.assertTrue(index.is_offered("tool"))
        index.replace_handlers({"tool": handler, "other": object()})
        self.assertTrue(index.is_offered("tool"))
        self.assertEqual(len(handler.calls), 1)

    def test_older_probe_cannot_overwrite_a_newer_result_for_the_same_scope(self) -> None:
        for old_status, new_status in ((False, True), (True, False)):
            with self.subTest(old=old_status, new=new_status):
                handler = _BlockedFirstProbe(old_status, following_status=new_status)
                index = ServerLocalOfferIndex()
                index.register("tool", handler)
                with self.running_probe(index, handler) as results:
                    self.assertEqual(index.is_offered("tool"), new_status)
                self.assertEqual(results, [new_status])
                self.assertEqual(index.is_offered("tool"), new_status)
                self.assertEqual(len(handler.calls), 2)

    def test_superseded_probe_fails_closed_until_the_newer_probe_finishes(self) -> None:
        first = _BlockedFirstProbe(True)
        second = _BlockedFirstProbe(True)
        pending = [first, second]
        allocation_lock = threading.Lock()

        class Handler:
            def capability_status(self, **context):
                with allocation_lock:
                    probe = pending.pop(0)
                return probe.capability_status(**context)

        index = ServerLocalOfferIndex()
        index.register("tool", Handler())
        with self.running_probe(index, first) as first_results:
            with self.running_probe(index, second) as second_results:
                first.release.set()
                self.assertTrue(first.completed.wait(3), "superseded probe did not finish")
                self.assertEqual(first_results, [False])
            self.assertEqual(second_results, [True])
        self.assertTrue(index.is_offered("tool"))

    def test_probes_in_other_profiles_sessions_and_modes_do_not_supersede_each_other(self) -> None:
        scope = {"profile_user_id": "alice", "session_id": "session", "client_mode": "desktop_pet"}
        for changed in ({"profile_user_id": "bob"}, {"session_id": "other"}, {"client_mode": "qq_text"}):
            with self.subTest(changed=changed):
                handler = _BlockedFirstProbe(False, following_status=True)
                index = ServerLocalOfferIndex()
                index.register("tool", handler)
                other_scope = {**scope, **changed}
                with self.running_probe(index, handler, **scope) as results:
                    self.assertTrue(index.is_offered("tool", **other_scope))
                self.assertEqual(results, [False])
                self.assertFalse(index.is_offered("tool", **scope))
                self.assertTrue(index.is_offered("tool", **other_scope))
                self.assertEqual(len(handler.calls), 2)


class CapabilityRegistryProfileTests(unittest.TestCase):
    def test_excluded_browser_contract_does_not_probe_its_executor_receipt(self) -> None:
        class Offers:
            def __init__(self):
                self.calls = []

            def resolve_receipt(self, spec):
                self.calls.append(spec.capability_id)
                return None

        for allowed, hidden in (((), ()), (("retrieve_memory",), ()),
                                (("open_browser",), ("open_browser",))):
            with self.subTest(allowed=allowed, hidden=hidden):
                offers = Offers()
                registry = CapabilityRegistry(offer_source=offers)
                registry.select(CapabilitySnapshot(client_mode=ClientMode.QQ_TEXT),
                                allowed_tool_names=allowed, hidden_tool_names=hidden)
                self.assertEqual(offers.calls, [])

    def test_hidden_browser_keeps_other_configured_executor_contracts(self) -> None:
        calls = []

        class Offers:
            def resolve_receipt(self, spec):
                calls.append(spec.capability_id)
                return None

        selection = CapabilityRegistry(offer_source=Offers()).select(
            CapabilitySnapshot(client_mode=ClientMode.DESKTOP_PET), hidden_tool_names=("open_browser",),
        )
        self.assertNotIn("open_browser", calls)
        self.assertNotIn("open_browser", selection.schema_tool_names)
        self.assertTrue(calls)
        self.assertEqual(calls, [spec.capability_id for spec in selection.tool_specs])

    def test_explicit_empty_profile_does_not_restore_default_modules(self) -> None:
        registry = CapabilityRegistry(modules=())
        self.assertEqual(registry.modules, ())
        for mode in ClientMode:
            with self.subTest(mode=mode):
                self.assertEqual(registry.tool_names_for_mode(mode), ())
                selection = registry.select(CapabilitySnapshot(client_mode=mode))
                self.assertEqual(selection, CapabilitySelection(light_hints=(), tool_names=(), module_names=()))


if __name__ == "__main__":
    unittest.main()
