"""Bounded workspace document windows report actual remaining content."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from companion_v01.store import MemoryStore
from companion_v01.tool_handlers.core import ToolExecutionContext
from companion_v01.tool_handlers.workspace import ReadWorkspaceToolHandler
from companion_v01.workspace_files import WorkspaceFileService


def write_pdf(path: Path, texts: list[str]) -> None:
    """A real PDF with a Helvetica text stream on each page, without test stubs."""
    page_ids = [4 + index * 2 for index in range(len(texts))]
    kids = ' '.join(f'{number} 0 R' for number in page_ids)
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>',
        f'<< /Type /Pages /Count {len(texts)} /Kids [{kids}] >>'.encode('ascii'),
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
    for page_id, text in zip(page_ids, texts):
        content = text.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')
        stream = f'BT /F1 12 Tf 50 700 Td ({content}) Tj ET'.encode('ascii')
        objects.append((f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] '
            f'/Resources << /Font << /F1 3 0 R >> >> /Contents {page_id + 1} 0 R >>').encode('ascii'))
        objects.append(f'<< /Length {len(stream)} >>\nstream\n'.encode('ascii') + stream + b'\nendstream')
    payload = bytearray(b'%PDF-1.4\n')
    offsets = [0]
    for number, item in enumerate(objects, 1):
        offsets.append(len(payload))
        payload.extend(f'{number} 0 obj\n'.encode('ascii') + item + b'\nendobj\n')
    xref = len(payload)
    payload.extend(f'xref\n0 {len(offsets)}\n0000000000 65535 f \n'.encode('ascii'))
    for offset in offsets[1:]:
        payload.extend(f'{offset:010d} 00000 n \n'.encode('ascii'))
    payload.extend((f'trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\n'
        f'startxref\n{xref}\n%%EOF\n').encode('ascii'))
    path.write_bytes(payload)


class WorkspaceDocumentReaderTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / 'workspace'
        self.service = WorkspaceFileService(root_dir=self.root, store=MemoryStore(Path(temporary.name) / 'data'))

    def test_one_large_word_paragraph_cannot_overfill_the_window(self):
        from docx import Document

        path = self.root / 'Inbox' / 'large.docx'
        document = Document()
        document.add_paragraph('长' * 10_000)
        document.save(path)
        content, capped = self.service._read_docx(path, max_chars=1000, capped_flag=True)
        self.assertEqual(content, '长' * 1001)
        self.assertTrue(capped)

    def test_exact_word_window_is_not_falsely_capped(self):
        from docx import Document

        path = self.root / 'Inbox' / 'exact.docx'
        document = Document()
        document.add_paragraph('字' * 1000)
        document.save(path)
        content, capped = self.service._read_docx(path, max_chars=1000, capped_flag=True)
        self.assertEqual(content, '字' * 1000)
        self.assertFalse(capped)

    def test_text_prefix_read_does_not_materialize_the_entire_file(self):
        path = self.root / 'Inbox' / 'text.md'
        text = '甲' * 998 + '🎉中' + '尾部' * 40_000
        path.write_bytes(text.encode('utf-8'))
        original_open = Path.open
        read_sizes = []

        class TrackedRead:
            def __init__(self, stream):
                self.stream = stream
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.stream.close()
            def read(self, size=-1):
                read_sizes.append(size)
                return self.stream.read(size)

        def open_tracked(source, *args, **kwargs):
            stream = original_open(source, *args, **kwargs)
            return TrackedRead(stream) if source == path else stream

        with patch.object(Path, 'open', open_tracked):
            result = self.service._read_file(path, max_chars=1000)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['content'], text[:1000])
        self.assertTrue(result['truncated'])
        self.assertTrue(read_sizes)
        self.assertTrue(all(0 < size <= 4100 for size in read_sizes), read_sizes)

    def test_bounded_prefix_preserves_gb18030_text(self):
        path = self.root / 'Inbox' / 'gb.txt'
        text = '汉字内容' * 2000
        path.write_bytes(text.encode('gb18030'))
        result = self.service._read_file(path, max_chars=1000)
        self.assertEqual(result['content'], text[:1000])
        self.assertTrue(result['truncated'])

    def test_pdf_without_a_text_layer_is_not_a_successful_body_read(self):
        path = self.root / 'Inbox' / 'scan.pdf'
        path.write_bytes(b'%PDF-fixture')
        reader = SimpleNamespace(pages=[SimpleNamespace(extract_text=lambda: '')])
        with patch('companion_v01.workspace_files.importlib.util.find_spec', return_value=object()):
            with patch.dict(sys.modules, {'pypdf': SimpleNamespace(PdfReader=lambda _path: reader)}):
                result = self.service._read_file_paged(path)
        self.assertEqual(result['status'], 'read_failed')
        self.assertNotIn('content', result)

    def test_actual_pdf_parser_reads_each_page_and_rejects_a_blank_body(self):
        path = self.root / 'Inbox' / 'actual.pdf'
        write_pdf(path, ['FIRST-PAGE-SOURCE', 'SECOND-PAGE-SOURCE'])
        read = self.service._read_file_paged(path)
        self.assertEqual(read['status'], 'ok')
        self.assertIn('## Page 1\nFIRST-PAGE-SOURCE', read['content'])
        self.assertIn('## Page 2\nSECOND-PAGE-SOURCE', read['content'])
        self.assertFalse(read['extraction_capped'])
        blank = self.root / 'Inbox' / 'blank.pdf'
        write_pdf(blank, [''])
        failed = self.service._read_file_paged(blank)
        self.assertEqual(failed['status'], 'read_failed')
        self.assertNotIn('content', failed)

    def test_tool_feedback_does_not_claim_a_failed_pdf_was_fully_read(self):
        path = self.root / 'Inbox' / 'blank.pdf'
        write_pdf(path, [''])
        context = ToolExecutionContext(profile_user_id='owner', session_id='desktop', now_ts=100, visual_payload={})
        result = ReadWorkspaceToolHandler(workspace_service=self.service).execute(
            call={'targets': ['workspace:/Inbox/blank.pdf']}, context=context)
        self.assertIn('read_failed', result.followup_context)
        self.assertNotIn('已读取本次目标范围', result.followup_context)
        self.assertTrue(result.followup_envelope.complete)
        self.assertIsNone(result.followup_envelope.continuation)
        self.assertNotIn(str(self.root), result.followup_context)

    def test_excel_continuations_parse_the_workbook_once(self):
        from openpyxl import Workbook

        path = self.root / 'Inbox' / 'report.xlsx'
        workbook = Workbook()
        workbook.active.title = 'Sheet2026'
        second = workbook.create_sheet('尾页')
        for sheet in workbook.worksheets:
            for row in range(350):
                sheet.append([row, '说明' * 100])
        second.append(['LAST-ROW'])
        workbook.save(path)
        workbook.close()
        chunks = []
        cursor = None
        with patch.object(self.service, '_read_xlsx', wraps=self.service._read_xlsx) as parse:
            for _ in range(20):
                result = self.service.read_items_paged(profile_user_id='owner', session_id='desktop',
                    targets=[] if cursor else ['workspace:/Inbox/report.xlsx'], cursor=cursor)
                chunks.extend(str(item.get('content') or '') for item in result['items'])
                cursor = result['next_cursor']
                if not cursor:
                    break
            else:
                self.fail('Excel pages did not finish')
        rendered = ''.join(chunks)
        self.assertIn('## Sheet: Sheet2026', rendered)
        self.assertIn('## Sheet: 尾页', rendered)
        self.assertIn('LAST-ROW', rendered)
        self.assertTrue(result['complete'])
        self.assertEqual(parse.call_count, 1)


if __name__ == '__main__':
    unittest.main()
