"""Failed focus selection preserves existing context and stays owner scoped."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from companion_v01.store import MemoryStore
from companion_v01.workspace_files import WorkspaceFileService


class WorkspaceFocusContextTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / 'workspace'
        self.service = WorkspaceFileService(root_dir=self.root, store=MemoryStore(Path(temporary.name) / 'data'))
        (self.root / 'Inbox' / 'current.md').write_text('current-source-content', encoding='utf-8')
        self.focus(['workspace:/Inbox/current.md'])

    def focus(self, targets, *, action='set', user='owner', session='desktop'):
        return self.service.focus_items(profile_user_id=user, session_id=session,
            targets=targets, action=action, timestamp=100)

    def context(self, *, service=None, user='owner', session='desktop', max_chars=1000):
        return (service or self.service).build_prompt_context(profile_user_id=user, session_id=session,
            max_chars_per_file=max_chars)

    def test_set_with_only_missing_targets_preserves_existing_focus(self):
        failed = self.focus(['workspace:/Inbox/missing.md'])
        self.assertEqual(failed['status'], 'failed')
        self.assertEqual(failed['affected'], [])
        self.assertEqual(failed['focused'], ['workspace:/Inbox/current.md'])
        self.assertIn('current-source-content', self.context())

    def test_set_with_only_denied_targets_preserves_existing_focus(self):
        failed = self.focus(['C:/private/outside.md', 'workspace:/../outside.md'])
        self.assertEqual(failed['status'], 'failed')
        self.assertEqual(failed['focused'], ['workspace:/Inbox/current.md'])
        self.assertNotIn('C:/private', str(failed))
        self.assertNotIn(str(self.root), str(failed))

    def test_canonical_aliases_only_affect_a_file_once(self):
        result = self.focus(['workspace:/Inbox/current.md', 'workspace:Inbox/current.md', 'Inbox/current.md'])
        self.assertEqual(result['affected'], ['workspace:/Inbox/current.md'])
        self.assertEqual(result['focused'], ['workspace:/Inbox/current.md'])

    def test_explicit_empty_set_still_clears_focus(self):
        cleared = self.focus([])
        self.assertEqual(cleared['status'], 'ok')
        self.assertEqual(cleared['focused'], [])
        self.assertNotIn('current-source-content', self.context())
        self.assertTrue((self.root / 'Inbox' / 'current.md').exists())

    def test_existing_empty_directory_can_be_selected_to_clear_focus(self):
        (self.root / 'Empty').mkdir()
        result = self.focus(['workspace:/Empty'])
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['focused'], [])

    def test_setting_foreign_owner_or_session_does_not_clear_current_focus(self):
        self.focus([], user='other-owner')
        self.focus([], session='other-session')
        self.assertIn('current-source-content', self.context())
        self.assertNotIn('current-source-content', self.context(user='other-owner'))
        self.assertNotIn('current-source-content', self.context(session='other-session'))

    def test_context_after_service_restart_uses_current_file_content(self):
        reloaded = WorkspaceFileService(root_dir=self.root, store=self.service.store)
        (self.root / 'Inbox' / 'current.md').write_text('updated-source-content', encoding='utf-8')
        refreshed = self.context(service=reloaded)
        self.assertIn('updated-source-content', refreshed)
        self.assertNotIn('current-source-content', refreshed)

    def test_context_reports_bounded_content_and_removed_files_truthfully(self):
        path = self.root / 'Inbox' / 'current.md'
        path.write_text('开头' * 1000 + 'unread-tail-marker', encoding='utf-8')
        limited = self.context(max_chars=1000)
        self.assertNotIn('unread-tail-marker', limited)
        self.assertIn('被截断', limited)
        path.unlink()
        removed = self.context()
        self.assertIn('文件已由用户移走或删除', removed)
        self.assertNotIn('unread-tail-marker', removed)
        self.assertNotIn(str(self.root), removed)


if __name__ == '__main__':
    unittest.main()
