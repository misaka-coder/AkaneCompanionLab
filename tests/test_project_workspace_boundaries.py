"""File identities and invalid paths must be checked before a project mutation."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from companion_v01.project_workspace import ProjectWorkspaceError, ProjectWorkspaceService
from companion_v01.store import MemoryStore
from companion_v01.tool_handlers.core import ToolExecutionContext
from companion_v01.tool_handlers.project_workspace import WorkspaceWriteToolHandler


class ProjectWorkspaceBoundaryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.service = ProjectWorkspaceService(
            store=MemoryStore(self.root / 'data'), execution_workspace_root=self.root / 'execution',
        )
        self.scope = self.service.scope_for(profile_user_id='alice', session_id='private-a', client_mode='qq')
        record = self.service.create(scope=self.scope, display_name='Boundary fixture')
        self.project = self.service.projects_root / record['workspace_id']

    def assert_rejected_before_write(self, text, reason='duplicate_patch_target', **kwargs):
        with patch.object(self.service, '_atomic_write', wraps=self.service._atomic_write) as writer:
            with self.assertRaises(ProjectWorkspaceError) as error:
                self.service.patch(scope=self.scope, patch_text=text, **kwargs)
            self.assertEqual(error.exception.reason, reason)
            writer.assert_not_called()

    def test_write_root_only_paths_are_structured_rejections(self):
        for value in ('.', './', ' .\\ '):
            with self.subTest(path=value):
                with self.assertRaises(ProjectWorkspaceError) as error:
                    self.service.write(scope=self.scope, path=value, content='must not write')
                self.assertEqual(error.exception.reason, 'invalid_relative_path')
        self.assertEqual(list(self.project.iterdir()), [])

    def test_patch_root_only_target_is_rejected_before_other_changes(self):
        text = ('*** Begin Patch\n*** Add File: valid.txt\n+valid\n'
                '*** Add File: ./\n+invalid\n*** End Patch\n')
        self.assert_rejected_before_write(text, reason='invalid_relative_path')
        self.assertEqual(list(self.project.iterdir()), [])

    def test_root_only_expected_hash_path_is_a_structured_rejection(self):
        text = '*** Begin Patch\n*** Add File: valid.txt\n+valid\n*** End Patch\n'
        self.assert_rejected_before_write(text, reason='invalid_relative_path', expected_files={'.': '0' * 64})
        self.assertEqual(list(self.project.iterdir()), [])

    def test_write_handler_returns_rejection_for_root_only_file_path(self):
        handler = WorkspaceWriteToolHandler(service=self.service)
        context = ToolExecutionContext(profile_user_id='alice', session_id='private-a', now_ts=0,
                                       visual_payload={}, client_mode='qq')
        result = handler.execute(call={'type': 'workspace_write', 'path': '.', 'content': 'invalid'}, context=context)
        self.assertEqual(result.stream_events[0]['status'], 'rejected')
        self.assertEqual(result.stream_events[0]['reason'], 'invalid_relative_path')
        self.assertEqual(list(self.project.iterdir()), [])

    def test_duplicate_normalized_creates_are_rejected_before_mutation(self):
        text = ('*** Begin Patch\n*** Add File: new.txt\n+first\n'
                '*** Add File: ./new.txt\n+second\n*** End Patch\n')
        self.assert_rejected_before_write(text)
        self.assertFalse((self.project / 'new.txt').exists())

    def test_duplicate_slash_alias_updates_preserve_original_content(self):
        target = self.project / 'nested' / 'source.txt'
        target.parent.mkdir()
        target.write_bytes(b'before\r\n')
        text = ('*** Begin Patch\n*** Update File: nested/source.txt\n@@\n-before\n+first\n'
                '*** Update File: nested\\source.txt\n@@\n-before\n+second\n*** End Patch\n')
        self.assert_rejected_before_write(text)
        self.assertEqual(target.read_bytes(), b'before\r\n')

    def test_rename_source_cannot_be_updated_again_through_an_alias(self):
        source = self.project / 'source.txt'
        source.write_bytes(b'before\n')
        text = ('*** Begin Patch\n*** Update File: source.txt\n*** Move to: moved.txt\n'
                '*** Update File: ./source.txt\n@@\n-before\n+second\n*** End Patch\n')
        self.assert_rejected_before_write(text)
        self.assertEqual(source.read_bytes(), b'before\n')
        self.assertFalse((self.project / 'moved.txt').exists())

    def test_rename_destination_cannot_be_created_again_through_an_alias(self):
        source = self.project / 'source.txt'
        source.write_bytes(b'before\n')
        text = ('*** Begin Patch\n*** Update File: source.txt\n*** Move to: moved.txt\n'
                '*** Add File: ./moved.txt\n+second\n*** End Patch\n')
        self.assert_rejected_before_write(text)
        self.assertEqual(source.read_bytes(), b'before\n')
        self.assertFalse((self.project / 'moved.txt').exists())

    def test_case_alias_conflicts_follow_platform_path_semantics(self):
        text = ('*** Begin Patch\n*** Add File: Case.txt\n+first\n'
                '*** Add File: case.txt\n+second\n*** End Patch\n')
        if os.name == 'nt':
            self.assert_rejected_before_write(text)
            self.assertEqual(list(self.project.iterdir()), [])
        else:
            self.service.patch(scope=self.scope, patch_text=text)
            self.assertEqual((self.project / 'Case.txt').read_bytes(), b'first\n')
            self.assertEqual((self.project / 'case.txt').read_bytes(), b'second\n')

    def test_distinct_normalized_paths_keep_mixed_operations_and_newlines(self):
        self.service.write(scope=self.scope, path='existing.txt', content='before\r\n')
        text = ('*** Begin Patch\n*** Update File: ./existing.txt\n@@\n-before\n+after\n'
                '*** Add File: nested\\new.txt\n+new\n*** End Patch\n')
        result = self.service.patch(scope=self.scope, patch_text=text)
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual([item['path'] for item in result['files']], ['existing.txt', 'nested/new.txt'])
        self.assertEqual((self.project / 'existing.txt').read_bytes(), b'after\r\n')
        self.assertEqual((self.project / 'nested' / 'new.txt').read_bytes(), b'new\n')

    def test_direct_execution_root_uses_the_same_collision_checks(self):
        text = ('*** Begin Patch\n*** Add File: new.txt\n+first\n'
                '*** Add File: ./new.txt\n+second\n*** End Patch\n')
        with patch.object(self.service, '_atomic_write', wraps=self.service._atomic_write) as writer:
            with self.assertRaises(ProjectWorkspaceError) as error:
                self.service.patch(scope=None, operation_root=self.service.execution_workspace_root, patch_text=text)
            self.assertEqual(error.exception.reason, 'duplicate_patch_target')
            writer.assert_not_called()
        self.assertFalse((self.service.execution_workspace_root / 'new.txt').exists())


if __name__ == '__main__':
    unittest.main()
