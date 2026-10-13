"""Workspace file read dispatch and bounded text prefixes."""

from __future__ import annotations

import codecs
import mimetypes
from pathlib import Path
from typing import Any

from .definitions import (
    DOCUMENT_EXTRACTION_CHAR_LIMIT, TEXT_EXTENSIONS, WorkspacePathError,
    WorkspaceReadLimitExceeded, WorkspaceReaderUnavailable,
)


def file_kind(path: Path) -> str:
    suffix = path.suffix.lower().lstrip('.')
    if suffix in TEXT_EXTENSIONS or (mimetypes.guess_type(path.name)[0] or '').startswith('text/'):
        return 'text'
    return suffix if suffix in {'docx', 'xlsx', 'pdf', 'zip'} else ''


def decode_text(payload: bytes, *, final: bool = True) -> str:
    # A bounded prefix can end between bytes of a valid UTF-8/GB18030 character.
    for encoding in ('utf-8-sig', 'utf-8', 'gb18030', 'cp936', 'latin-1'):
        try:
            return codecs.getincrementaldecoder(encoding)(errors='strict').decode(payload, final=final)
        except UnicodeDecodeError:
            continue
    return payload.decode('utf-8', errors='replace')


class WorkspaceReadingMixin:
    def read_items(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        targets: list[str],
        max_chars: int = 1_000_000,
    ) -> dict[str, Any]:
        requested_targets = self._normalize_target_list(targets)
        char_limit = max(1000, min(4_000_000, int(max_chars or 1_000_000)))
        state_map = self._state_map(profile_user_id=profile_user_id, session_id=session_id)
        items: list[dict[str, Any]] = []
        for requested in requested_targets:
            try:
                path, uri = self.resolve_uri(requested)
            except WorkspacePathError as exc:
                items.append(
                    {
                        "requested": "(invalid workspace path)",
                        "status": "denied",
                        "reason": str(exc),
                    }
                )
                continue
            if not path.exists():
                items.append({"uri": uri, "status": "missing", "reason": "workspace item does not exist"})
                continue
            if not path.is_file():
                items.append({"uri": uri, "status": "not_file", "reason": "target is not a file"})
                continue
            item = self._describe_path(path, state_map=state_map)
            item.update(self._read_file(path, max_chars=char_limit))
            items.append(item)
        return {
            "status": "ok" if any(item.get("status") == "ok" for item in items) else "failed",
            "items": items,
        }

    def _read_file_paged(self, path: Path) -> dict[str, Any]:
        """Extract text within the byte limit, or a bounded document window."""
        result = self._extract_workspace_content(path, max_chars=DOCUMENT_EXTRACTION_CHAR_LIMIT, full_text=True)
        if result.get('status') == 'ok' and result.get('source_kind') != 'text':
            result['content'] = result['content'][:DOCUMENT_EXTRACTION_CHAR_LIMIT]
        return result

    def _read_file(self, path: Path, *, max_chars: int) -> dict[str, Any]:
        result = self._extract_workspace_content(path, max_chars=max_chars, full_text=False)
        if result.get('status') != 'ok':
            return result
        return {
            'status': 'ok', 'content': result['content'][:max_chars],
            'truncated': bool(result['extraction_capped']), 'source_kind': result['source_kind'],
        }

    def _extract_workspace_content(self, path: Path, *, max_chars: int, full_text: bool) -> dict[str, Any]:
        """One dispatch and failure boundary shared by preview and paged reads."""
        try:
            size = int(path.stat().st_size)
        except OSError:
            return {"status": "read_failed", "reason": "file metadata could not be read"}
        if size > self.max_read_bytes:
            return {
                "status": "too_large",
                "reason": f"file exceeds workspace read limit ({self.max_read_bytes} bytes)",
            }

        suffix = path.suffix.lower().lstrip(".")
        source_kind = file_kind(path)
        try:
            if source_kind == 'text':
                content = self._read_plain_text(path) if full_text else self._read_plain_text_prefix(path, max_chars=max_chars)
                capped = not full_text and len(content) > max_chars
            elif source_kind:
                readers = {'docx': self._read_docx, 'xlsx': self._read_xlsx,
                           'pdf': self._read_pdf, 'zip': self._read_zip_manifest}
                content, capped = readers[source_kind](path, max_chars=max_chars, capped_flag=True)
            else:
                return {
                    "status": "unsupported_binary",
                    "reason": "binary file requires a specialized media or document tool",
                    "source_kind": suffix or "binary",
                }
        except WorkspaceReadLimitExceeded:
            return {'status': 'too_large', 'reason': f'file exceeds workspace read limit ({self.max_read_bytes} bytes)'}
        except WorkspaceReaderUnavailable as exc:
            return {"status": "read_failed", "reason": str(exc)[:160], "source_kind": suffix}
        except Exception:
            return {
                "status": "read_failed",
                "reason": "file content could not be read",
                "source_kind": suffix,
            }

        return {
            "status": "ok",
            "content": content,
            "extraction_capped": bool(capped),
            "source_kind": source_kind,
        }

    def _read_plain_text(self, path: Path) -> str:
        with path.open('rb') as handle:
            payload = handle.read(self.max_read_bytes + 1)
        if len(payload) > self.max_read_bytes:
            raise WorkspaceReadLimitExceeded()
        return decode_text(payload)

    def _read_plain_text_prefix(self, path: Path, *, max_chars: int) -> str:
        byte_limit = min(self.max_read_bytes + 1, (max(0, int(max_chars)) + 1) * 4 + 4)
        with path.open('rb') as handle:
            payload = handle.read(byte_limit)
        if len(payload) > self.max_read_bytes:
            raise WorkspaceReadLimitExceeded()
        return decode_text(payload, final=len(payload) < byte_limit)[:max_chars + 1]
