from __future__ import annotations

import unittest
from types import SimpleNamespace

from companion_v01.engine import AkaneMemoryEngine
from companion_v01.llm_runtime import ChatJSONResult


class ProviderRawResultTests(unittest.TestCase):
    @staticmethod
    def _exhaust(generator):
        events = []
        while True:
            try:
                events.append(next(generator))
            except StopIteration as exc:
                return events, exc.value

    def test_sync_final_builder_carries_raw_only_in_private_internal_field(self) -> None:
        raw_text = '{"memory_metadata":{},  "speech":"同步原文"}'
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        engine.llm = SimpleNamespace(
            call_chat_json_result=lambda **_kwargs: ChatJSONResult(
                parsed={"speech": "同步原文", "memory_metadata": {}},
                raw_text=raw_text,
                metadata_status="accepted_model",
                metadata_present=True,
            )
        )
        engine._prepare_final_response_context = lambda **_kwargs: {
            "system_prompt": "system",
            "user_prompt": "user",
            "fallback": {"speech": "fallback"},
            "visual_defaults": {},
            "debug_enabled": False,
            "allow_tool_call": False,
            "native_tools": [],
            "native_tool_choice": "",
            "system_extra_blocks": [],
            "history_turns": [],
            "post_user_turns": [],
            "prompt_audit_sections": [],
        }
        engine._normalize_final_output = lambda **kwargs: dict(kwargs["result"])
        engine._attach_tool_execution_receipts = lambda *_args, **_kwargs: None

        output = engine._build_final_response(
            session_id="s",
            profile_user_id="u",
            user_message="hello",
            recent_raw=[],
            recent_episodic_summaries=[],
            recent_semantic_summaries=[],
            confirmed_snippets=[],
            now_ts=0,
        )

        self.assertEqual(output.pop("_provider_output_raw"), raw_text)
        self.assertEqual(output.pop("_memory_annotation_status"), "accepted_model")
        self.assertTrue(output.pop("_memory_metadata_present"))
        self.assertEqual(output, {"speech": "同步原文", "memory_metadata": {}})
        self.assertNotIn(raw_text, repr(output))

    def test_stream_final_builder_carries_exact_joined_raw_internally(self) -> None:
        raw_text = '{"speech":"流式原文",  "memory_metadata":{}}'
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)

        def stream_chat_json(**_kwargs):
            if False:
                yield {}
            return SimpleNamespace(
                parsed={"speech": "流式原文", "memory_metadata": {}},
                raw_text=raw_text,
                error="",
                latest_emotion="",
                latest_speech="",
                latest_reply_medium="",
                native_preface_text="",
                metadata_status="accepted_model",
                metadata_present=True,
            )

        engine.llm = SimpleNamespace(stream_chat_json=stream_chat_json)
        engine._prepare_final_response_context = lambda **_kwargs: {
            "system_prompt": "system",
            "user_prompt": "user",
            "fallback": {"speech": "fallback"},
            "visual_defaults": {},
            "debug_enabled": False,
            "allow_tool_call": False,
            "native_tools": [],
            "native_tool_choice": "",
            "system_extra_blocks": [],
            "history_turns": [],
            "post_user_turns": [],
            "prompt_audit_sections": [],
        }
        engine._resolve_turn_speaker_identity = lambda *_args, **_kwargs: {"assistant_name": "Akane"}
        engine._normalize_final_output = lambda **kwargs: dict(kwargs["result"])
        engine._attach_tool_execution_receipts = lambda *_args, **_kwargs: None

        events, output = self._exhaust(
            engine._stream_final_response(
                session_id="s",
                profile_user_id="u",
                user_message="hello",
                recent_raw=[],
                recent_episodic_summaries=[],
                recent_semantic_summaries=[],
                confirmed_snippets=[],
                now_ts=0,
            )
        )

        self.assertEqual(events, [{"type": "turn_start", "speaker": "Akane"}])
        self.assertEqual(output.pop("_provider_output_raw"), raw_text)
        self.assertEqual(output.pop("_memory_annotation_status"), "accepted_model")
        self.assertTrue(output.pop("_memory_metadata_present"))
        self.assertEqual(output, {"speech": "流式原文", "memory_metadata": {}})

    def test_missing_metadata_keeps_sync_reply_and_marks_annotation_missing(self) -> None:
        raw_text = '{"speech":"不影响回复"}'
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        engine.llm = SimpleNamespace(
            call_chat_json_result=lambda **_kwargs: ChatJSONResult(
                parsed={"speech": "不影响回复"},
                raw_text=raw_text,
                metadata_status="missing",
                metadata_present=False,
            )
        )
        engine._prepare_final_response_context = lambda **_kwargs: {
            "system_prompt": "system",
            "user_prompt": "user",
            "fallback": {"speech": "fallback"},
            "visual_defaults": {},
            "debug_enabled": False,
            "allow_tool_call": False,
            "native_tools": [],
            "native_tool_choice": "",
            "system_extra_blocks": [],
            "history_turns": [],
            "post_user_turns": [],
            "prompt_audit_sections": [],
        }
        engine._normalize_final_output = lambda **kwargs: {
            **dict(kwargs["result"]),
            "memory_metadata": {},
        }
        engine._attach_tool_execution_receipts = lambda *_args, **_kwargs: None

        output = engine._build_final_response(
            session_id="s",
            profile_user_id="u",
            user_message="hello",
            recent_raw=[],
            recent_episodic_summaries=[],
            recent_semantic_summaries=[],
            confirmed_snippets=[],
            now_ts=0,
        )

        self.assertEqual(output["speech"], "不影响回复")
        self.assertEqual(output["_provider_output_raw"], raw_text)
        self.assertEqual(output["_memory_annotation_status"], "missing")
        self.assertFalse(output["_memory_metadata_present"])

    def test_host_normalized_memory_signal_keeps_recall_admission(self) -> None:
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        output = {
            "speech": "记住了。",
            "memory_metadata": {"keywords": ["无糖可乐"]},
        }

        engine._attach_memory_annotation_truth(
            output,
            result=SimpleNamespace(metadata_status="missing", metadata_present=False),
            raw_result={"speech": "记住了。", "memory_tags": ["无糖可乐"]},
        )

        self.assertEqual(output["speech"], "记住了。")
        self.assertEqual(output["_memory_annotation_status"], "accepted_host")

    def test_complete_turn_receives_exact_raw_without_logging_or_reshaping(self) -> None:
        raw_text = '{ "speech":"顺序不变", "memory_metadata":{} }'
        captured = {}
        manager = SimpleNamespace(
            complete_input_turn=lambda **kwargs: captured.update(kwargs) or {"ok": True, "status": "completed"}
        )
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        engine._memcore_manager_if_enabled = lambda: manager
        engine._warn_memcore_write_result = lambda *_args, **_kwargs: None

        result = engine._complete_memcore_input_turn(
            turn_id="turn-1",
            assistant_record={"source_id": "assistant-1", "content": "顺序不变"},
            memory_metadata={},
            provider_output_raw=raw_text,
            annotation_status="missing",
            profile_user_id="u",
            session_id="s",
            character_pack_id="char",
        )

        self.assertTrue(result["ok"])
        self.assertEqual(captured["provider_output_raw"], raw_text)
        self.assertEqual(captured["annotation_status"], "missing")

    def test_failed_final_does_not_mark_broken_raw_as_persistable_final(self) -> None:
        broken_raw = "not valid json"
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        engine.llm = SimpleNamespace(
            call_chat_json_result=lambda **_kwargs: ChatJSONResult(
                parsed={"speech": "fallback"},
                raw_text=broken_raw,
            )
        )
        engine._prepare_final_response_context = lambda **_kwargs: {
            "system_prompt": "system",
            "user_prompt": "user",
            "fallback": {"speech": "fallback"},
            "visual_defaults": {},
            "debug_enabled": False,
            "allow_tool_call": False,
            "native_tools": [],
            "native_tool_choice": "",
            "system_extra_blocks": [],
            "history_turns": [],
            "post_user_turns": [],
            "prompt_audit_sections": [],
        }
        engine._normalize_final_output = lambda **kwargs: dict(kwargs["result"])
        engine._attach_tool_execution_receipts = lambda *_args, **_kwargs: None
        engine._is_retryable_final_output = lambda *_args, **_kwargs: True

        output = engine._build_final_response(
            session_id="s",
            profile_user_id="u",
            user_message="hello",
            recent_raw=[],
            recent_episodic_summaries=[],
            recent_semantic_summaries=[],
            confirmed_snippets=[],
            now_ts=0,
        )

        self.assertTrue(output["_transient_final_failure"])
        self.assertNotIn("_provider_output_raw", output)
        self.assertNotIn(broken_raw, repr(output))


if __name__ == "__main__":
    unittest.main()
