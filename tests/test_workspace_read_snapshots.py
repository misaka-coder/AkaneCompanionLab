"""Workspace paging keeps its wire contract while reusing verified extraction."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from companion_v01.store import MemoryStore
from companion_v01.tool_handlers.core import ToolExecutionContext
from companion_v01.tool_handlers.workspace import ReadWorkspaceToolHandler
from companion_v01.workspace_files import WorkspaceFileService


class WorkspaceReadSnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / 'workspace'
        self.store = MemoryStore(Path(self.temporary.name) / 'data')
        self.service = WorkspaceFileService(root_dir=self.root, store=self.store)

    def write(self, name: str, text: str) -> Path:
        path = self.root / 'Inbox' / name
        path.write_bytes(text.encode('utf-8'))
        return path

    def read(self, *, name: str = 'report.md', cursor=None, service=None, user='owner', session='desktop'):
        return (service or self.service).read_items_paged(
            profile_user_id=user, session_id=session,
            targets=[] if cursor else [f'workspace:/Inbox/{name}'], cursor=cursor,
        )

    def collect(self, first):
        chunks = [str(item.get('content') or '') for item in first['items']]
        current = first
        for _ in range(30):
            if not current.get('next_cursor'):
                return ''.join(chunks), current
            current = self.read(cursor=current['next_cursor'])
            self.assertEqual(current['status'], 'ok')
            chunks.extend(str(item.get('content') or '') for item in current['items'])
        self.fail('Paging did not finish')

    def test_pages_extract_once_and_preserve_utf8_and_crlf(self):
        text = '开头🎉\r\n' + '\r\n'.join(f'第{i}行：' + '资料' * 30 for i in range(1800))
        self.write('report.md', text)
        with patch.object(self.service, '_read_file_paged', wraps=self.service._read_file_paged) as extract:
            joined, last = self.collect(self.read())
        self.assertEqual(joined, text)
        self.assertTrue(last['complete'])
        self.assertEqual(extract.call_count, 1)

    def test_repeated_first_page_reuses_verified_extraction(self):
        self.write('report.md', 'A' * 80_000)
        with patch.object(self.service, '_read_file_paged', wraps=self.service._read_file_paged) as extract:
            first = self.read()
            repeated = self.read()
        self.assertEqual(first, repeated)
        self.assertEqual(extract.call_count, 1)

    def test_fresh_read_refreshes_same_size_same_mtime_changed_source(self):
        path = self.write('report.md', 'A' * 80_000)
        first = self.read()
        stat = path.stat()
        path.write_bytes(b'B' * 80_000)
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        fresh = self.read()
        self.assertTrue(first['items'][0]['content'].startswith('A'))
        self.assertTrue(fresh['items'][0]['content'].startswith('B'))
        stale = self.read(cursor=first['next_cursor'])
        self.assertEqual(stale['status'], 'stale_cursor')

    def test_restart_resumes_existing_cursor_without_a_persisted_cache(self):
        self.write('report.md', '资料🎉\n' * 25_000)
        first = self.read()
        expected = self.read(cursor=first['next_cursor'])
        reloaded = WorkspaceFileService(root_dir=self.root, store=self.store)
        resumed = self.read(cursor=first['next_cursor'], service=reloaded)
        self.assertEqual(resumed, expected)

    def test_eviction_reextracts_and_preserves_cursor(self):
        from companion_v01.workspace_files_runtime.read_cache import WorkspaceReadCache

        self.service._read_cache = WorkspaceReadCache(max_entries=1)
        self.write('report.md', 'A' * 80_000)
        self.write('other.md', 'B' * 80_000)
        with patch.object(self.service, '_read_file_paged', wraps=self.service._read_file_paged) as extract:
            first = self.read()
            self.read(name='other.md')
            resumed = self.read(cursor=first['next_cursor'])
        self.assertEqual(resumed['status'], 'ok')
        self.assertTrue(resumed['items'][0]['content'].startswith('A'))
        self.assertEqual(extract.call_count, 3)

    def test_expired_snapshot_is_recreated_for_the_same_cursor(self):
        self.write('report.md', 'A' * 80_000)
        with patch('companion_v01.workspace_files_runtime.read_cache.time.monotonic', return_value=100):
            first = self.read()
        with patch('companion_v01.workspace_files_runtime.read_cache.time.monotonic', return_value=500):
            with patch.object(self.service, '_read_file_paged', wraps=self.service._read_file_paged) as extract:
                resumed = self.read(cursor=first['next_cursor'])
        self.assertEqual(resumed['status'], 'ok')
        self.assertEqual(extract.call_count, 1)

    def test_cache_is_separate_for_each_user_and_session(self):
        self.write('report.md', 'A' * 80_000)
        with patch.object(self.service, '_read_file_paged', wraps=self.service._read_file_paged) as extract:
            self.read()
            self.read(user='another-owner')
            self.read(session='another-session')
            self.read()
        self.assertEqual(extract.call_count, 3)

    def test_foreign_cursor_is_denied_before_opening_files(self):
        self.write('report.md', 'A' * 80_000)
        first = self.read()
        with patch.object(Path, 'open', side_effect=AssertionError('foreign cursor opened a file')):
            foreign = self.read(cursor=first['next_cursor'], session='another-session')
        self.assertEqual(foreign['status'], 'cursor_invalid')

    def test_legacy_cursor_without_the_optional_capped_flag_still_resumes(self):
        from companion_v01 import paged_reading

        self.write('report.md', 'A' * 80_000)
        first = self.read()
        binding = paged_reading.cursor_binding('read_workspace', 'owner', 'desktop')
        payload = paged_reading.parse_json_payload(paged_reading.parse_paged_cursor(
            first['next_cursor'], tool='ws', binding=binding))
        payload.pop('c', None)
        legacy = paged_reading.make_paged_cursor(tool='ws', binding=binding,
            payload=paged_reading.json_payload(payload))
        self.assertEqual(self.read(cursor=legacy), self.read(cursor=first['next_cursor']))

    def test_snapshot_larger_than_cache_budget_still_pages_losslessly(self):
        from companion_v01.workspace_files_runtime.read_cache import WorkspaceReadCache

        self.service._read_cache = WorkspaceReadCache(max_chars=1000)
        text = '资料' * 40_000
        self.write('report.md', text)
        joined, last = self.collect(self.read())
        self.assertEqual(joined, text)
        self.assertTrue(last['complete'])

    def test_character_budget_evicts_old_material_and_repr_hides_bodies(self):
        from companion_v01.workspace_files_runtime.read_cache import WorkspaceReadCache, WorkspaceReadSnapshot

        cache = WorkspaceReadCache(max_chars=100, max_entries=8)
        first = WorkspaceReadSnapshot(text='private-material' * 4, fingerprint='first',
            source_stamp='private-source-stamp', source_kind='text', extraction_capped=False)
        second = WorkspaceReadSnapshot(text='second-material' * 4, fingerprint='second',
            source_stamp='another-source-stamp', source_kind='text', extraction_capped=False)
        cache.put(('first',), first)
        cache.put(('second',), second)
        self.assertIsNone(cache.get(('first',)))
        self.assertIs(cache.get(('second',)), second)
        self.assertNotIn('private-material', repr(first))
        self.assertNotIn('private-source-stamp', repr(first))

    def test_multifile_page_does_not_overfill_the_remaining_character_budget(self):
        from companion_v01 import paged_reading

        self.write('report.md', 'A' * (paged_reading.FIRST_PAGE_BUDGET_CHARS - 100))
        self.write('other.md', 'B' * 80_000)
        first = self.service.read_items_paged(profile_user_id='owner', session_id='desktop',
            targets=['workspace:/Inbox/report.md', 'workspace:/Inbox/other.md'])
        self.assertLessEqual(sum(len(item.get('content', '')) for item in first['items']),
            paged_reading.FIRST_PAGE_BUDGET_CHARS)
        self.assertTrue(first['next_cursor'])
        resumed = self.read(cursor=first['next_cursor'])
        self.assertEqual(resumed['items'][0]['uri'], 'workspace:/Inbox/other.md')
        self.assertTrue(resumed['items'][0]['content'].startswith('B'))

    def test_source_changed_during_extraction_does_not_publish_old_body(self):
        path = self.write('report.md', 'A' * 80_000)
        original = self.service._read_file_paged

        def mutate(source):
            result = original(source)
            path.write_bytes(b'B' * 80_000)
            return result

        with patch.object(self.service, '_read_file_paged', side_effect=mutate):
            raced = self.read()
        self.assertEqual(raced['status'], 'stale_cursor')
        self.assertEqual(raced['items'], [])
        self.assertTrue(self.read()['items'][0]['content'].startswith('B'))

    def test_source_deleted_during_extraction_does_not_publish_old_body(self):
        path = self.write('report.md', 'A' * 80_000)
        original = self.service._read_file_paged

        def remove(source):
            result = original(source)
            path.unlink()
            return result

        with patch.object(self.service, '_read_file_paged', side_effect=remove):
            raced = self.read()
        self.assertEqual(raced['status'], 'source_missing')
        self.assertEqual(raced['items'], [])

    def test_failed_source_validation_does_not_return_cached_body(self):
        self.write('report.md', 'private-body\n' * 10_000)
        first = self.read()
        with patch.object(Path, 'open', side_effect=PermissionError('private-path-not-for-output')):
            failed = self.read(cursor=first['next_cursor'])
        self.assertNotIn('private-body', str(failed))
        self.assertNotIn('private-path-not-for-output', str(failed))
        self.assertEqual(failed['items'][0]['status'], 'read_failed')

    def test_capped_window_is_incomplete_without_an_invalid_continuation(self):
        self.write('report.docx', 'document-fixture')
        window = {'status': 'ok', 'content': 'visible-window', 'source_kind': 'docx', 'extraction_capped': True}
        with patch.object(self.service, '_read_file_paged', return_value=window):
            result = self.read(name='report.docx')
            context = ToolExecutionContext(profile_user_id='owner', session_id='desktop', now_ts=100, visual_payload={})
            delivered = ReadWorkspaceToolHandler(workspace_service=self.service).execute(
                call={'targets': ['workspace:/Inbox/report.docx']}, context=context,
            )
        self.assertFalse(result['complete'])
        self.assertFalse(result['items'][0]['file_complete'])
        self.assertIsNone(result['next_cursor'])
        self.assertFalse(delivered.followup_envelope.complete)
        self.assertIsNone(delivered.followup_envelope.continuation)
        self.assertNotIn('已读取本次目标范围', delivered.followup_context)

    def test_capped_flag_survives_paging_into_the_next_file(self):
        self.write('report.docx', 'document-fixture')
        self.write('report.md', 'B' * 80_000)
        original = self.service._read_file_paged

        def extract(path):
            if path.suffix == '.docx':
                return {'status': 'ok', 'content': 'visible-window', 'source_kind': 'docx', 'extraction_capped': True}
            return original(path)

        with patch.object(self.service, '_read_file_paged', side_effect=extract):
            current = self.service.read_items_paged(profile_user_id='owner', session_id='desktop',
                targets=['workspace:/Inbox/report.docx', 'workspace:/Inbox/report.md'])
            self.assertTrue(current['next_cursor'])
            _, last = self.collect(current)
        self.assertFalse(last['complete'])
        self.assertTrue(last['extraction_capped'])
        self.assertIsNone(last['next_cursor'])


if __name__ == '__main__':
    unittest.main()
