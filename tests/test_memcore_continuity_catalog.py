"""Known card provenance and concise raw-match navigation through real tools."""

import json
import unittest
from dataclasses import replace

from memcore import EntryOrigin, TurnRole, TimelineEntryInput

from companion_v01.tool_handlers.memory import BrowseMemoryToolHandler, ReadMemoryTimelineToolHandler
from tests import test_memcore_sources_batch as source_fixture


class MemcoreContinuityCatalogSmokeTests(unittest.TestCase):
    setUp = source_fixture.MemcoreSourcesBatchSmokeTests.setUp
    context = source_fixture.MemcoreSourcesBatchSmokeTests.context
    seed = source_fixture.MemcoreSourcesBatchSmokeTests.seed
    execute = source_fixture.MemcoreSourcesBatchSmokeTests.execute
    metadata = staticmethod(source_fixture.MemcoreSourcesBatchSmokeTests.metadata)

    def catalog(self):
        handler = BrowseMemoryToolHandler(timeline_service=self.service, read_policy_provider=lambda: self.policy)
        return handler.execute(call={"type": "browse_memory", "conversation": "group:200", "keywords": ["原话"]}, context=self.origin)

    def test_selected_foreign_card_keeps_scope_without_repeating_conversation(self):
        self.seed("one")
        found = self.catalog()
        payload = self.metadata(found)
        self.assertEqual(payload["conversation"], "group:200")
        card = payload["cards"][0]
        self.assertEqual(card["matched_source_count"], 2)
        for key in ("conversation", "source_label", "period_start_ts", "participant_refs", "topic_headings", "source_ids"):
            self.assertNotIn(key, card)
        self.origin.request_context["_host_visible_tool_history"] = [{"role": "tool", "content": found.followup_context}]
        opened = self.execute(memory_id="one", view="sources")
        self.assertEqual(self.metadata(opened)["conversation"], "group:200")
        self.assertIn("one-raw-0 的原话", opened.followup_context)
        self.assertNotIn("memory_not_found_or_out_of_scope", opened.followup_context)
        conflict = self.execute(memory_id="one", view="sources", conversation="group:100")
        self.assertIn("memory_conversation_conflict", conflict.followup_context)
        # A user quotation alone must not redirect the default scope.
        self.origin.request_context["_host_visible_tool_history"] = [{"role": "user", "content": found.followup_context}]
        default = self.execute(memory_id="one", view="sources")
        self.assertEqual(self.metadata(default)["conversation"], "group:100")

    def test_signed_keyword_reference_is_reloadable_and_authorized_again(self):
        self.seed("one")
        card = self.metadata(self.catalog())["cards"][0]
        reference = card["match_ref"]
        self.assertTrue(reference.startswith("mrc2."))
        opened = self.execute(match_ref=reference)
        payload = self.metadata(opened)
        self.assertEqual(payload["source_mode"], "keyword_filtered")
        self.assertEqual(payload["returned_source_count"], 2)
        self.assertEqual(payload["conversation"], "group:200")
        self.assertNotIn("未选择的其他人发言", opened.followup_context)
        self.assertEqual(opened.followup_context.count("one-raw-0 的原话"), 1)
        self.assertNotIn("messages", payload)
        foreign = self.execute(match_ref=reference, context=self.context(kind="group", number=100, actor=111))
        self.assertIn("cursor_invalid", foreign.followup_context)
        self.cross_enabled = False
        denied = self.execute(match_ref=reference)
        self.assertIn("cross_conversation_disabled", denied.followup_context)

    def test_failed_lookup_cannot_poison_successful_card_conversation(self):
        self.seed("one")
        found = self.catalog()
        failed = self.service.render_open_memory_context({
            "backend": "memcore", "ok": False, "status": "empty",
            "reason": "memory_not_found_or_out_of_scope", "conversation": "group:100",
            "memory_id": "one", "view": "content", "result": None,
        })
        self.origin.request_context["_host_visible_tool_history"] = [
            {"role": "tool", "content": failed},
            {"role": "tool", "content": found.followup_context},
        ]
        for options in ({"memory_id": "one"}, {"memory_id": "one", "conversation": "group:200"},
                        {"memory_ids": ["one"], "conversation": "group:200"}):
            with self.subTest(options=options):
                opened = self.execute(view="content", **options)
                self.assertNotIn("ambiguous_memory_conversation", opened.followup_context)
                payload = self.metadata(opened)
                self.assertEqual(payload["status"], "ok", payload)
                self.assertEqual(payload["conversation"], "group:200")
                self.assertIn("不应重复进入来源结果的摘要", opened.followup_context)

        # Genuine conflicting successful observations still require resolution.
        other_success = self.metadata(found)
        other_success["conversation"] = "group:100"
        self.origin.request_context["_host_visible_tool_history"].append({
            "role": "tool", "content": "【MemCore 记忆目录】\n" + json.dumps(other_success, ensure_ascii=False),
        })
        rejected = self.execute(memory_id="one", view="content", conversation="group:200")
        self.assertIn("ambiguous_memory_conversation", rejected.followup_context)

    def test_partial_timeline_marks_read_scope_and_keeps_complete_continuation(self):
        self.seed("one")
        handler = ReadMemoryTimelineToolHandler(timeline_service=self.service, read_policy_provider=lambda: self.policy)
        call = {"type": "read_memory_timeline", "conversation": "group:200",
                "date_from": "1970-01-01", "page_token_budget": 1}
        seen = set()
        pages = []
        for _ in range(4):
            page = handler.execute(call=call, context=self.origin)
            data = self.metadata(page)
            self.assertEqual(data["conversation"], "group:200")
            self.assertNotIn("returned_logical_unit_ids", data["coverage"])
            self.assertIn("returned_start", data["coverage"])
            self.assertIn("returned_end", data["coverage"])
            ids = set(page.trace_receipt["returned_logical_unit_ids"])
            self.assertTrue(ids)
            self.assertFalse(ids & seen)
            seen.update(ids)
            pages.append(page.followup_context)
            if page.followup_envelope.complete:
                self.assertNotIn("当前证据不完整", page.followup_context)
                break
            self.assertIn("当前证据不完整", page.followup_context)
            cursor = data["next_cursor"]
            self.assertTrue(cursor.startswith("mrc1."))
            self.assertEqual(page.followup_envelope.continuation, {"cursor": cursor})
            call = {"type": "read_memory_timeline", "cursor": cursor}
        else:
            self.fail("Timeline continuation did not finish")
        self.assertEqual(len(seen), 4)
        self.assertEqual("\n".join(pages).count("one-raw-0 的原话"), 1)
        self.assertEqual("\n".join(pages).count("one-raw-1 的原话"), 1)

    def test_terminal_compression_readback_preserves_foreign_card_provenance(self):
        self.seed("one")
        found = self.catalog()
        card = self.metadata(found)["cards"][0]
        self.manager._memory_config = replace(self.manager._memory_config, operation_projection_policy="compact_after_terminal")
        local = self.manager._get_system(profile_user_id="qq_group_shared_100", session_id="qq_group_shared_100", character_pack_id="char-a")
        turn = local.begin_turn(stimuli=[TimelineEntryInput(kind="message.user", origin=EntryOrigin.USER,
            turn_role=TurnRole.STIMULUS, semantic_text="看看群 B 的目录", timestamp=100)])
        exchange = local.record_tool_exchange(turn_id=turn.turn_id, tool_name="browse_memory", tool_call_id="catalog-call",
            tool_input={"conversation": "group:200", "keywords": ["原话"]}, result=found.followup_context, timestamp=101)
        local.build_context_projection(provider_profile="openai_chat")
        completed = local.complete_turn(turn_id=turn.turn_id, semantic_text="已找到", memory_annotation={},
            provider_output_raw="已找到", timestamp=104, provider_profile="openai_chat",
            provider_projection={"role": "assistant", "content": "已找到"})
        self.assertTrue(completed.completed)
        wire = local.build_context_projection(provider_profile="openai_chat").payloads
        self.assertTrue(any("[compact_reloadable]" in str(item.get("content")) for item in wire))
        self.assertFalse(any(found.followup_context in str(item.get("content")) for item in wire))
        reload_result = self.execute(memory_id=exchange["tool_result"]["source_id"], view="content")
        self.origin.request_context["_host_visible_tool_history"] = [{"role": "tool", "content": reload_result.followup_context}]
        opened = self.execute(memory_id=card["memory_id"], view="sources")
        self.assertEqual(self.metadata(opened)["conversation"], "group:200")
        self.assertEqual(opened.followup_context.count("one-raw-0 的原话"), 1)
        matched = self.execute(match_ref=card["match_ref"])
        self.assertEqual(self.metadata(matched)["returned_source_count"], 2)
        quote = local.begin_turn(stimuli=[TimelineEntryInput(kind="message.user", origin=EntryOrigin.USER,
            turn_role=TurnRole.STIMULUS, semantic_text=found.followup_context, timestamp=106)])
        quoted_read = self.execute(memory_id=quote.stimuli[0].source_id, view="content")
        self.origin.request_context["_host_visible_tool_history"] = [{"role": "tool", "content": quoted_read.followup_context}]
        rejected_quote = self.execute(memory_id=card["memory_id"], view="sources")
        self.assertEqual(self.metadata(rejected_quote)["conversation"], "group:100")
        local.abort_turn(quote.turn_id, reason="isolated test complete")
