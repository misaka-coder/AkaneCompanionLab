"""Background progress is observable, but is never a completed receipt."""
from __future__ import annotations

import asyncio
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from companion_v01.durable_session_queue import DurableSessionWorkQueue
from companion_v01.routes.desktop_turn_events import DesktopTurnEventRelay, process_background_desktop_turn
from companion_v01.routes.think import build_think_router
from companion_v01.session_inbox import SessionInboxStore
from companion_v01.turn_coordination import cancellation_scope
from tests.test_plugin_agent_events import turn_intent

PAYLOAD = {"user_id": "desk", "real_user_id": "owner", "character_pack_id": "cecilia", "message": "work"}
REFERENCE = {"channel": "desktop_pet", "kind": "direct", "recipient": "desktop:desk",
             "session": "desk", "profile": "owner", "character": "cecilia"}


def public_events(frames):
    return [event for frame in frames for event in frame.get("_desktop_turn_event", {}).get("events", [])]


class DesktopBackgroundProgressTests(unittest.IsolatedAsyncioTestCase):
    async def deliver_to(self, frames, frame):
        frames.append(frame)
        return {"ok": True, "status": "queued"}

    def relay(self, frames, **kwargs):
        return DesktopTurnEventRelay(PAYLOAD, lambda frame: self.deliver_to(frames, frame), **kwargs)

    async def test_preface_and_tool_state_reach_ui_before_next_tool_step(self):
        frames, executed, closed = [], [], []

        def stream(_payload):
            try:
                yield {"type": "turn_start"}
                yield {"type": "speech_segment", "index": 0, "text": "我先检查。", "_debug": "private"}
                yield {"type": "assistant_working", "message": "检查中。", "tool_type": "project_inspect"}
                self.assertEqual(public_events(frames)[-1]["type"], "assistant_working")
                self.assertIn("我先检查。", [event.get("text") for event in public_events(frames)])
                executed.append(True)
                yield {"type": "final", "payload": {"speech": "完成。", "_debug": {"private": True}}}
            finally:
                closed.append(True)

        relay = self.relay(frames)
        async with relay:
            frame = await process_background_desktop_turn(SimpleNamespace(process_turn_stream=stream), PAYLOAD, relay)
            self.assertNotIn("final", [event["type"] for event in public_events(frames)])
            await self.deliver_to(frames, relay.final_presentation({"speech": frame["speech"]}))
        self.assertEqual(executed, [True])
        self.assertEqual(closed, [True])
        self.assertEqual([event["type"] for event in public_events(frames)][-2:], ["final", "stream_end"])
        self.assertTrue(all("_debug" not in event for event in public_events(frames)))
        envelopes = [frame["_desktop_turn_event"] for frame in frames]
        self.assertEqual([item["sequence"] for item in envelopes], list(range(len(frames))))
        self.assertEqual(len({item["stream_id"] for item in envelopes}), 1)
        self.assertTrue(all(item["session_id"] == "desk" and item["profile_user_id"] == "owner" for item in envelopes))

    async def test_heartbeat_does_not_reexecute_a_blocked_step(self):
        frames, calls = [], []

        def stream(_payload):
            calls.append(True)
            time.sleep(0.04)
            yield {"type": "final", "payload": {"speech": "完成。"}}

        relay = self.relay(frames)
        with patch("companion_v01.routes.think_stream.STREAM_PROGRESS_INTERVAL_SECONDS", 0.005):
            async with relay:
                await process_background_desktop_turn(SimpleNamespace(process_turn_stream=stream), PAYLOAD, relay)
        self.assertEqual(calls, [True])
        self.assertTrue(any(event["type"] == "turn_progress" for event in public_events(frames)))

    async def test_incomplete_or_transient_stream_never_announces_final_success(self):
        for transient in (False, True):
            with self.subTest(transient=transient):
                frames = []

                def stream(_payload):
                    yield {"type": "speech_segment", "index": 0, "text": "检查中。"}
                    if transient:
                        yield {"type": "final", "payload": {"speech": "无法完成。", "_transient_final_failure": True}}

                relay = self.relay(frames)
                with self.assertRaisesRegex(RuntimeError, "desktop_session_turn_incomplete"):
                    async with relay:
                        await process_background_desktop_turn(SimpleNamespace(process_turn_stream=stream), PAYLOAD, relay)
                events = public_events(frames)
                self.assertNotIn("final", [event["type"] for event in events])
                self.assertEqual(events[-1], {"type": "stream_end", "status": "error"})
                self.assertEqual(sum(event["type"] == "stream_error" for event in events), 1)

    async def test_progress_delivery_failure_closes_engine_before_side_effect(self):
        executed, closed = [], []

        async def failed(frame):
            return {"ok": False, "status": "unavailable"}

        def stream(_payload):
            try:
                yield {"type": "speech_segment", "index": 0, "text": "即将执行。"}
                executed.append(True)
                yield {"type": "final", "payload": {"speech": "完成。"}}
            finally:
                closed.append(True)

        relay = DesktopTurnEventRelay(PAYLOAD, failed)
        relay.started = True
        with self.assertRaisesRegex(RuntimeError, "desktop_progress_delivery_failed"):
            await process_background_desktop_turn(SimpleNamespace(process_turn_stream=stream), PAYLOAD, relay)
        self.assertEqual(executed, [])
        self.assertEqual(closed, [True])

    async def test_single_message_event_keeps_research_text_private(self):
        frames = []
        events = [{"type": "speech_segment", "index": 0, "text": "不要单独推送。"},
                  {"type": "voice_segment", "index": 0, "unit_index": 0, "text": "private"},
                  {"type": "assistant_working", "message": "处理中。"},
                  {"type": "final", "payload": {"speech": "唯一结果。"}}]
        relay = self.relay(frames)
        async with relay:
            await process_background_desktop_turn(SimpleNamespace(process_turn_stream=lambda p: iter(events)),
                                                   {**PAYLOAD, "plugin_text_delivery": "single_message"}, relay)
        self.assertNotIn("speech_segment", [event["type"] for event in public_events(frames)])
        self.assertNotIn("voice_segment", [event["type"] for event in public_events(frames)])
        self.assertIn("assistant_working", [event["type"] for event in public_events(frames)])

    async def test_revoked_scope_closes_stream_and_does_not_emit_stale_text(self):
        frames, executed, closed = [], [], []
        revoked = [False]

        def stream(_payload):
            try:
                yield {"type": "speech_segment", "index": 0, "text": "检查中。"}
                revoked[0] = True
                yield {"type": "speech_segment", "index": 1, "text": "过期文字。"}
                executed.append(True)
            finally:
                closed.append(True)

        relay = self.relay(frames)
        with cancellation_scope(lambda: revoked[0]):
            async with relay:
                result = await process_background_desktop_turn(SimpleNamespace(process_turn_stream=stream), PAYLOAD, relay)
        self.assertEqual(result["status"], "stopped")
        self.assertEqual(executed, [])
        self.assertEqual(closed, [True])
        self.assertNotIn("过期文字。", [event.get("text") for event in public_events(frames)])

    async def test_nonqueued_plugin_event_uses_stream_and_existing_final_policy(self):
        frames, handlers, processed = [], {}, []

        def stream(payload):
            processed.append(payload)
            yield {"type": "speech_segment", "index": 0, "text": "先检查。"}
            self.assertEqual(public_events(frames)[-1]["type"], "speech_segment")
            yield {"type": "final", "payload": {"speech": "已完成。", "emotion": "normal", "_debug": {"hidden": True}}}

        async def deliver(frame):
            return await self.deliver_to(frames, frame)

        build_think_router(engine=SimpleNamespace(process_turn_stream=stream), public_guard=SimpleNamespace(),
                           runtime_metrics=SimpleNamespace(), log_event=lambda *a, **k: None,
                           plugin_agent_event_handler_registrar=handlers.__setitem__,
                           desktop_agent_frame_delivery=deliver, desktop_agent_event_delivery=deliver,
                           desktop_agent_event_available=lambda: True)
        result = await handlers["desktop_pet"](turn_intent(reference="opaque", source="akane.timer"), REFERENCE)
        self.assertTrue(result.ok, result)
        self.assertEqual(len(processed), 1)
        final = next(event["payload"] for event in public_events(frames) if event["type"] == "final")
        self.assertEqual(final["speech"], "已完成。")
        self.assertNotIn("_debug", final)

    async def run_queue(self, path, *, fail=False, recovered=False, offline=False):
        frames, calls = [], []
        store = SessionInboxStore(path)
        item = store.enqueue(session_key="owner\0desk", profile_user_id="owner", session_id="desk", kind="turn",
                             source="desktop_pet", source_event_id="input-1", payload={"turn_payload": PAYLOAD})
        if recovered:
            claim = store.claim_next("owner\0desk", worker_id="previous-process")
            self.assertTrue(store.settle_claims([claim["item"]], status="queued", error="worker_cancelled")["ok"])
            store = SessionInboxStore(path)
        queue = DurableSessionWorkQueue(store)

        def stream(payload):
            calls.append(payload)
            yield {"type": "speech_segment", "index": 0, "text": "中途反馈。"}
            self.assertNotEqual(store.get(item["item_id"]).status, "committed")
            self.assertIn("中途反馈。", [event.get("text") for event in public_events(frames)])
            if fail:
                raise ValueError("private implementation details must not reach UI")
            yield {"type": "final", "payload": {"speech": "最终结果。", "_debug": {"hidden": True}}}

        async def deliver(frame):
            return await self.deliver_to(frames, frame)

        build_think_router(engine=SimpleNamespace(process_turn_stream=stream), public_guard=SimpleNamespace(),
                           runtime_metrics=SimpleNamespace(), log_event=lambda *a, **k: None,
                           session_work_queue=queue, desktop_agent_frame_delivery=deliver,
                           desktop_agent_event_delivery=deliver, desktop_agent_event_available=lambda: not offline)
        try:
            await queue.schedule_session("owner\0desk")
            async with asyncio.timeout(3):
                if offline:
                    while store.get(item["item_id"]).last_error != "desktop_client_unavailable":
                        await asyncio.sleep(0.005)
                else:
                    while queue.has_work("owner\0desk"):
                        await asyncio.sleep(0.005)
            return store.get(item["item_id"]), frames, calls
        finally:
            await queue.close()

    async def test_recovered_queue_delivers_midturn_and_commits_only_after_final(self):
        with tempfile.TemporaryDirectory() as temp:
            item, frames, calls = await self.run_queue(Path(temp) / "inbox.db", recovered=True)
        self.assertEqual(item.status, "committed")
        self.assertEqual(item.attempts, 2)
        self.assertEqual(len(calls), 1)
        events = public_events(frames)
        self.assertEqual(events[0], {"type": "stream_start", "phase": "resuming"})
        self.assertEqual([event["type"] for event in events][-2:], ["final", "stream_end"])
        self.assertEqual(events[-2]["payload"]["speech"], "最终结果。")
        self.assertNotIn("_debug", events[-2]["payload"])

    async def test_queue_failure_does_not_commit_or_leak_exception_as_reply(self):
        with tempfile.TemporaryDirectory() as temp:
            item, frames, calls = await self.run_queue(Path(temp) / "inbox.db", fail=True)
        self.assertEqual(item.status, "failed")
        self.assertEqual(len(calls), 1)
        self.assertNotIn("final", [event["type"] for event in public_events(frames)])
        self.assertEqual(public_events(frames)[-1]["status"], "error")
        self.assertNotIn("private implementation", str(frames))

    async def test_withdrawn_completion_sends_no_initial_progress(self):
        from tests.test_plugin_completion_revocation import DesktopCompletionRevocationTests
        real_build = build_think_router

        def wired(**kwargs):
            kwargs["desktop_agent_event_delivery"] = kwargs["desktop_agent_frame_delivery"]
            return real_build(**kwargs)

        with patch("companion_v01.routes.think.build_think_router", side_effect=wired):
            await DesktopCompletionRevocationTests().check_waiting_revocation(revoke_all=True)

    async def test_revoked_presentation_sends_only_terminal_control_after_scope_unwinds(self):
        frames = []
        revoked = [False]
        relay = self.relay(frames)
        async with relay:
            with cancellation_scope(lambda: revoked[0]):
                revoked[0] = True
                await relay.end(stopped=True)
                self.assertEqual([event["type"] for event in public_events(frames)], ["stream_start"])
        self.assertEqual([event["type"] for event in public_events(frames)],
                         ["stream_start", "turn_stopped", "stream_end"])
        self.assertEqual(public_events(frames)[-1]["status"], "stopped")

    async def test_unavailable_desktop_defers_without_starting_model(self):
        with tempfile.TemporaryDirectory() as temp:
            item, frames, calls = await self.run_queue(Path(temp) / "inbox.db", offline=True)
        self.assertEqual(item.status, "queued")
        self.assertEqual(item.last_error, "desktop_client_unavailable")
        self.assertEqual(frames, [])
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
