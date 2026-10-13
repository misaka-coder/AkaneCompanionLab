"""Storage for AttachmentInboxService."""

from __future__ import annotations

from pathlib import Path
from typing import Any


class AttachmentStorageMixin:
    def resolve_storage_path(self, item: dict[str, Any]) -> Path | None:
        """Return the original stored file path for an inbox item, if safe.

        Generated-file tooling needs a public way to work from the original
        attachment bytes without reaching into the private resolver.
        """

        return self._resolve_storage_path(item)

    def _resolve_storage_path(self, item: dict[str, Any]) -> Path | None:
        relpath = str(item.get("storage_relpath") or "").strip()
        if not relpath:
            return None
        if relpath.lower().startswith("workspace:"):
            if self.workspace_uri_resolver is None:
                return None
            try:
                candidate = self.workspace_uri_resolver(relpath)
            except Exception:
                return None
            if isinstance(candidate, Path) and candidate.exists() and candidate.is_file():
                return candidate
            return None
        for candidate in self._managed_storage_candidates(item):
            if candidate.exists() and candidate.is_file():
                return candidate
        return None

    def _managed_storage_candidates(self, item: dict[str, Any]) -> list[Path]:
        relpath = str(item.get("storage_relpath") or "").strip()
        if not relpath or relpath.lower().startswith("workspace:"):
            return []
        storage_roots = [root for root in [self.base_dir, *self.legacy_base_dirs] if isinstance(root, Path)]
        candidates = []
        for storage_root in storage_roots:
            try:
                candidate = (storage_root / Path(relpath)).resolve()
                base = storage_root.resolve()
                candidate.relative_to(base)
            except Exception:
                continue
            candidates.append(candidate)
        return candidates
