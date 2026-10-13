from __future__ import annotations

import ast
import unittest
from pathlib import Path
from types import SimpleNamespace

from companion_v01.channels.qq.commands import card_results as qq_command_cards
from companion_v01.channels.qq.commands import handler as qq_command_handlers
from companion_v01.channels.qq.commands import workspace as qq_workspace_messages
from companion_v01.channels.qq.delivery import artifacts as qq_artifact_delivery
from companion_v01.channels.qq.delivery import replies as qq_reply_delivery
from companion_v01.channels.qq.delivery import stream as qq_stream_delivery
from companion_v01.channels.qq.inbound import attachment_context as qq_attachment_context
from companion_v01.channels.qq.inbound import observations as qq_passive_messages
from companion_v01.channels.qq.inbound import plugin_input as qq_plugin_input
from companion_v01.channels.qq.inbound import prepare as qq_inbound_media
from companion_v01.channels.qq.inbound import references as qq_references
from companion_v01.channels.qq.inbound import router as qq_inbound_events
from companion_v01.channels.qq.turns import attention as qq_attention_runtime
from companion_v01.channels.qq.turns import followups as qq_followups
from companion_v01.channels.qq.turns import plugin_turns as qq_plugin_turns
from companion_v01.channels.qq.turns import queue as qq_session_queue
from companion_v01.channels.qq.turns import reply_audio as qq_voice_delivery
from companion_v01.channels.qq.turns import runner as qq_turn_runtime
from companion_v01.channels.qq.turns import streaming as qq_turn_streaming
from companion_v01.channels.qq.turns import work as qq_session_work
from companion_v01.channels.qq.turns.attention_state import QQGroupAttentionState
from companion_v01.routes import qq


class QQDeliveryModuleTests(unittest.TestCase):
    def test_helpers_live_with_their_owners_instead_of_the_http_route(self):
        owners = {
            qq_reply_delivery: ("_send_qq_delivery", "_resolve_delivery_medium", "_send_qq_emotion_image_fallback"),
            qq_voice_delivery: ("_synthesize_qq_voice_file", "_voice_cache_key", "_prune_voice_cache"),
            qq_stream_delivery: ("_filter_unsent_reply_messages", "_StageSpeechReplay", "_send_pending_stage_messages"),
            qq_artifact_delivery: ("_qq_file_delivery_event_identity", "_hydrate_plugin_managed_artifact_events"),
            qq_references: ("_qq_quoted_message_reference",),
            qq_attachment_context: ("_merge_qq_attachments",),
            qq_turn_streaming: ("_process_qq_turn_streaming",),
            qq_workspace_messages: ("_parse_qq_workspace_command", "_build_qq_workspace_list_reply"),
        }
        for owner, names in owners.items():
            for name in names:
                with self.subTest(module=owner.__name__, helper=name):
                    self.assertFalse(hasattr(qq, name))
                    self.assertEqual(getattr(owner, name).__module__, owner.__name__)
        self.assertIs(qq_reply_delivery.prepare_qq_reply_audio, qq_voice_delivery.prepare_qq_reply_audio)

    def test_delivery_modules_do_not_depend_on_routes_or_each_other_cyclically(self):
        owners = (qq_reply_delivery, qq_stream_delivery, qq_voice_delivery, qq_artifact_delivery,
                  qq_attachment_context, qq_references, qq_turn_streaming, qq_workspace_messages,
                  qq_command_cards, qq_command_handlers, qq_attention_runtime, qq_followups,
                  qq_plugin_input, qq_plugin_turns, qq_session_queue, qq_session_work, qq_turn_runtime,
                  qq_inbound_events, qq_inbound_media, qq_passive_messages)
        names = {module.__name__ for module in owners}
        graph = {name: set() for name in names}
        for module in owners:
            name = module.__name__
            source = Path(module.__file__).read_text(encoding="utf-8")
            for node in ast.walk(ast.parse(source)):
                if isinstance(node, ast.ImportFrom):
                    imports = [node.module or ""]
                elif isinstance(node, ast.Import):
                    imports = [alias.name for alias in node.names]
                else:
                    continue
                for target in imports:
                    self.assertNotIn("routes", target.split("."), name)
                    dependency = target
                    if dependency in names:
                        graph[name].add(dependency)
        self.assertEqual(graph[qq_voice_delivery.__name__], set())
        self.assertEqual(graph[qq_stream_delivery.__name__], set())
        self.assertEqual(graph[qq_reply_delivery.__name__], {qq_voice_delivery.__name__})
        visited = set()

        def check_acyclic(name, path):
            self.assertNotIn(name, path, f"cyclic QQ module dependency: {path}")
            if name in visited:
                return
            for dependency in graph[name]:
                check_acyclic(dependency, path + (name,))
            visited.add(name)

        for name in graph:
            check_acyclic(name, ())

    def test_pending_attention_images_stay_with_their_bot_runtime(self):
        def runtime():
            return qq_attention_runtime.QQGroupAttentionRuntime(
                engine=None, config_module=SimpleNamespace(QQ_GROUP_ATTENTION_TTL_SECONDS=120),
                qq_gateway=None, logger=None, log_event=lambda *args, **kwargs: None,
                turn_coordinator=None, state=QQGroupAttentionState(),
                queue_has_work=lambda key: False, run_turn=None, schedule_task=None,
            )

        first, second = runtime(), runtime()
        context = SimpleNamespace(profile_user_id="same-group", session_id="same-session",
                                  user_id=42, group_id=87, sender_label="member")
        event = {"time": 100}
        first.stage_media(context, event, [{"kind": "image", "attachment_id": "first-bot-image"}],
                          timeline_source_id="source-first-bot")
        self.assertEqual(second._take_media(context, event), {"attachment_ids": (), "timeline_source_ids": ()})
        self.assertEqual(first._take_media(context, event), {
            "attachment_ids": ("first-bot-image",), "timeline_source_ids": ("source-first-bot",),
        })


if __name__ == "__main__":
    unittest.main()
