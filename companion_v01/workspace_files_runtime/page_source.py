"""Validate live workspace sources and reuse bounded, owner-scoped extraction."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .definitions import WorkspacePathError, WorkspaceReadLimitExceeded
from .read_cache import WorkspaceReadSnapshot
from .reading import file_kind


class WorkspaceSourceChanged(RuntimeError):
    pass


class WorkspacePageSourceMixin:
    def _read_page_source(self, path: Path, *, profile_user_id: str, session_id: str) -> dict[str, Any]:
        # Unsupported binaries need no content hashing or text snapshot.
        if not file_kind(path):
            return self._read_file_paged(path)
        key = None
        try:
            key = (profile_user_id, session_id, self.to_uri(path), str(self.max_read_bytes))
            cached = self._read_cache.get(key)
            source_stamp = self._workspace_source_stamp(path)
            if cached is not None and cached.source_stamp == source_stamp:
                return cached.read_result()
            self._read_cache.discard(key)
            read = self._read_file_paged(path)
            if read.get('status') != 'ok':
                return read
            if source_stamp != self._workspace_source_stamp(path):
                raise WorkspaceSourceChanged()
            content = str(read.get('content') or '')
            cached = WorkspaceReadSnapshot(text=content, source_stamp=source_stamp,
                fingerprint=self._content_fingerprint(content), source_kind=str(read.get('source_kind') or ''),
                extraction_capped=bool(read.get('extraction_capped')))
            self._read_cache.put(key, cached)
            return cached.read_result()
        except FileNotFoundError:
            failure = {'status': 'source_missing', 'reason': 'workspace source disappeared during reading'}
        except (WorkspaceSourceChanged, WorkspacePathError):
            failure = {'status': 'stale_cursor', 'reason': 'workspace source changed during reading'}
        except WorkspaceReadLimitExceeded:
            failure = {'status': 'too_large', 'reason': f'file exceeds workspace read limit ({self.max_read_bytes} bytes)'}
        except OSError:
            failure = {'status': 'read_failed', 'reason': 'workspace source could not be verified'}
        if key is not None:
            self._read_cache.discard(key)
        return failure

    def _workspace_source_stamp(self, path: Path) -> str:
        self.to_uri(path)
        before = path.stat()
        if before.st_size > self.max_read_bytes:
            raise WorkspaceReadLimitExceeded()
        digest = hashlib.sha256()
        bytes_read = 0
        with path.open('rb') as handle:
            while chunk := handle.read(64 * 1024):
                bytes_read += len(chunk)
                if bytes_read > self.max_read_bytes:
                    raise WorkspaceReadLimitExceeded()
                digest.update(chunk)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns, before.st_ino) != (
                after.st_size, after.st_mtime_ns, after.st_ctime_ns, after.st_ino):
            raise WorkspaceSourceChanged()
        self.to_uri(path)
        return digest.hexdigest()
