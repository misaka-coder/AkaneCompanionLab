"""Workspace layout and checked relative paths."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from .definitions import WORKSPACE_LAYER_NAMES, WorkspacePathError


class WorkspacePathsMixin:
    def ensure_layout(self) -> dict[str, Path]:
        if self.root_dir.exists() and not self.root_dir.is_dir():
            raise RuntimeError("configured Akane workspace root is not a directory")
        if self.root_dir.exists() and (self.root_dir.is_symlink() or self.root_dir.resolve() != self.root_dir):
            raise RuntimeError("configured Akane workspace root changed to a linked location")
        self.root_dir.mkdir(parents=True, exist_ok=True)
        layers: dict[str, Path] = {}
        for folder_name in WORKSPACE_LAYER_NAMES:
            folder = self.root_dir / folder_name
            if folder.exists() and not folder.is_dir():
                raise RuntimeError(f"workspace layer is not a directory: {folder_name}")
            if folder.exists() and (folder.is_symlink() or folder.resolve() != folder):
                raise RuntimeError(f"workspace layer changed to a linked location: {folder_name}")
            folder.mkdir(exist_ok=True)
            layers[folder_name] = folder
        return layers

    def layer_dir(self, layer_name: str) -> Path:
        normalized = str(layer_name or "").strip().lower()
        for folder_name in WORKSPACE_LAYER_NAMES:
            if folder_name.lower() == normalized:
                return self.ensure_layout()[folder_name]
        raise ValueError(f"unsupported workspace layer: {layer_name}")

    def resolve_uri(self, value: str) -> tuple[Path, str]:
        raw = str(value or "").strip()
        if not raw:
            raw = "workspace:/"
        normalized = raw.replace("\\", "/")
        has_workspace_scheme = normalized.lower().startswith("workspace:")
        if has_workspace_scheme:
            normalized = normalized[len("workspace:") :]
            normalized = normalized.lstrip("/")
        elif normalized.startswith("//") or self._looks_like_absolute_path(normalized):
            raise WorkspacePathError("absolute paths are not allowed; use workspace:/ relative paths")
        normalized = normalized.lstrip("/")
        pure_path = PurePosixPath(normalized or ".")
        if any(part in {"..", ""} for part in pure_path.parts):
            raise WorkspacePathError("path traversal is not allowed")
        candidate = self.root_dir.joinpath(*[part for part in pure_path.parts if part != "."])
        try:
            resolved = candidate.resolve(strict=False)
            resolved.relative_to(self.root_dir)
        except (OSError, ValueError):
            raise WorkspacePathError("path escapes the configured workspace") from None
        return resolved, self.to_uri(resolved)

    def to_uri(self, path: Path) -> str:
        try:
            relative = path.resolve(strict=False).relative_to(self.root_dir)
        except (OSError, ValueError):
            raise WorkspacePathError("path escapes the configured workspace") from None
        if not relative.parts:
            return "workspace:/"
        return f"workspace:/{relative.as_posix()}"

    def _normalize_target_list(self, values: list[str] | tuple[str, ...] | set[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            text = str(value or "").strip()
            if text and text not in normalized:
                normalized.append(text)
        return normalized

    def _looks_like_absolute_path(self, value: str) -> bool:
        if value.startswith(("/", "\\")):
            return True
        return len(value) >= 3 and value[0].isalpha() and value[1] == ":" and value[2] == "/"
