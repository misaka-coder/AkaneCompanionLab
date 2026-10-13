import asyncio
import json
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from companion_v01.cache_usage import cache_day, conversation_cache_scope, read_daily_cache_usage
from companion_v01.channels.qq.cards.cache import cache_report_text
from companion_v01.channels.qq.commands.handler import QQCommandHandler
from companion_v01.channels.qq.commands.parser import resolve_qq_command


class QQCacheCardTests(unittest.TestCase):
    def test_beijing_day_weighted_ratio_missing_usage_and_scope_isolation(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "llm_prompt_audit"
            folder.mkdir()
            scope = conversation_cache_scope("u", "s", "cecilia")
            base = {"record_type": "usage", "conversation_scope_hash": scope,
                    "client_mode": "qq_text", "instance_id": "one", "model": "test",
                    "reported_input_tokens": 100, "cache_read_tokens": 100, "cache_read_reported": True}
            rows = [
                {**base, "ts": "2026-10-01T15:59:59Z"},
                {**base, "ts": "2026-10-01T16:00:00Z"},
                {**base, "ts": "2026-10-02T00:00:00Z", "reported_input_tokens": 900, "cache_read_tokens": 0},
                {**base, "ts": "2026-10-02T00:10:00Z", "cache_read_reported": False, "cache_read_tokens": 0},
                {**base, "ts": "2026-10-02T00:20:00Z", "conversation_scope_hash": "other"},
                {**base, "ts": "2026-10-02T00:30:00Z", "instance_id": "other"},
                {**base, "ts": "2026-10-02T00:40:00Z", "client_mode": "desktop_pet"},
                {**base, "ts": "2026-10-02T16:00:00Z"},
            ]
            for utc_day in ("2026-10-01", "2026-10-02"):
                (folder / f"{utc_day}.jsonl").write_text("\n".join(json.dumps(r) for r in rows if r['ts'].startswith(utc_day)) + '\n{', encoding="utf-8")
            report = read_daily_cache_usage(root, day=date(2026, 10, 2), scope_hash=scope, instance_id="one")
            self.assertEqual((report['requests'], report['reported'], report['unknown']), (3, 2, 1))
            self.assertEqual(report['ratio'], .1)
            self.assertEqual(report['hours'][0]['ratio'], 1)
            self.assertEqual(report['hours'][8]['ratio'], 0)
            self.assertIsNone(report['hours'][9]['ratio'])
            self.assertIn("10.0%", cache_report_text(report))

    def test_old_usage_uses_unambiguous_request_audit_and_does_not_guess_changed_routes(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "cache_usage").mkdir()
            (root / "llm_prompt_audit").mkdir()
            record = {"record_type": "usage", "ts": "2026-10-02T18:00:01Z", "instance_id": "one",
                      "conversation_scope_hash": "a" * 64, "client_mode": "qq_text", "prompt_cache_key": "key",
                      "model": "gemini-pro-default", "reported_input_tokens": 1000,
                      "cache_read_tokens": 973, "cache_read_reported": True}
            ledger = root / "cache_usage" / "2026-10-02.jsonl"
            ledger.write_text(json.dumps(record) + '\n', encoding="utf-8")
            prompt = {"record_type": "prompt", "ts": "2026-10-02T18:00:00Z", "instance_id": "one",
                      "prompt_cache_key": "key", "model": "gemini-3.1-pro-high"}
            audit = root / "llm_prompt_audit" / "2026-10-02.jsonl"
            audit.write_text(json.dumps(prompt) + '\n' + json.dumps({**prompt, 'instance_id': 'other',
                             'model': 'different'}) + '\n{', encoding="utf-8")
            read = lambda: read_daily_cache_usage(root, day=date(2026, 10, 3), scope_hash="a" * 64, instance_id="one")
            self.assertEqual(read()['models'][0]['model'], "gemini-3.1-pro-high")
            with audit.open('a', encoding='utf-8') as stream:
                stream.write('\n' + json.dumps({**prompt, 'model': 'gemini-3.8-flash'}) + '\n')
            self.assertEqual(read()['models'][0]['model'], "gemini-pro-default")
            ledger.write_text(json.dumps({**record, 'requested_model': 'gemini-3.1-pro-high'}) + '\n', encoding='utf-8')
            self.assertEqual(read()['models'][0]['model'], "gemini-3.1-pro-high")
            self.assertEqual(read()['ratio'], .973)
            self.assertEqual(read()['requests'], 1)

    def test_date_command_defaults_and_rejects_bad_arguments(self):
        self.assertEqual(cache_day("", today=date(2026, 10, 3)), date(2026, 10, 3))
        self.assertEqual(cache_day("昨天", today=date(2026, 10, 3)), date(2026, 10, 2))
        self.assertEqual(resolve_qq_command("/缓存 2026-10-01").payload['day'], "2026-10-01")
        self.assertTrue(resolve_qq_command("/缓存 随便").error)
        self.assertTrue(resolve_qq_command("/缓存 2099-01-01").error)

    def test_command_sends_card_and_denies_other_users_without_reading_logs(self):
        with TemporaryDirectory() as tmp:
            handler = QQCommandHandler.__new__(QQCommandHandler)
            handler.engine = SimpleNamespace(llm=SimpleNamespace(log_dir=Path(tmp), instance_id="one"))
            handler.qq_gateway = SimpleNamespace(master_qq="123")
            handler.send_reply = AsyncMock(return_value={"ok": True})
            handler.cards = SimpleNamespace(render_and_deliver=AsyncMock(return_value=({"ok": True}, "image", "")))
            handler._local_command_response = lambda context, **kwargs: kwargs
            context = SimpleNamespace(user_id=123, profile_user_id="master", session_id="master", character_pack_id="cecilia",
                                      clean_message="/缓存", is_group=False)
            resolution = resolve_qq_command(context.clean_message)
            result = asyncio.run(handler.handle_early(context=context, command_context=context, request=None, started_at=0,
                                                     resolution=resolution))
            self.assertTrue(result['command_ok'])
            self.assertEqual(result['card_format'], "image")
            document = handler.cards.render_and_deliver.call_args.args[1]
            self.assertIsNone(document['report']['ratio'])
            with patch("companion_v01.cache_usage.read_daily_cache_usage", side_effect=PermissionError("private/path")):
                result = asyncio.run(handler.handle_early(context=context, command_context=context, request=None, started_at=0,
                                                         resolution=resolution))
            self.assertEqual(result['command_status'], "unavailable")
            self.assertEqual(result['failure_reason'], "PermissionError")
            self.assertNotIn("private/path", handler.send_reply.call_args.args[1])
            context.user_id = 456
            result = asyncio.run(handler.handle_early(context=context, command_context=context, request=None, started_at=0,
                                                     resolution=resolution))
            self.assertEqual(result['command_status'], "forbidden")
            self.assertEqual(handler.cards.render_and_deliver.call_count, 1)
