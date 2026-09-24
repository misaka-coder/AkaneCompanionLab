"""Story Loader: scans, compiles, caches, and hot-reloads story packs from filesystem directories."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any
import yaml

from ..contracts import StoryPack
from .compiler import compile_story_markdown, validate_story_pack_graph

logger = logging.getLogger(__name__)


class StoryLoader:
    def __init__(
        self,
        *,
        characters_dir: Path | None = None,
        user_stories_dir: Path | None = None,
    ):
        self.characters_dir = characters_dir
        self.user_stories_dir = user_stories_dir
        self._builtin_packs: dict[str, StoryPack] = {}
        # Cache map: file_path -> (mtime, StoryPack)
        self._file_cache: dict[Path, tuple[float, StoryPack]] = {}

    def register_builtin_pack(self, pack: StoryPack) -> None:
        """Register an in-memory/code-defined StoryPack."""
        self._builtin_packs[pack.story_id] = pack

    def get_pack(self, story_id: str, character_pack_id: str = "") -> StoryPack | None:
        """Find and load a pack by story_id within the given character or global context."""
        all_packs = self.list_packs(character_pack_id)
        return all_packs.get(story_id)

    def list_packs(self, character_pack_id: str = "") -> dict[str, StoryPack]:
        """List all story packs available for the given character, plus global and built-ins."""
        results: dict[str, StoryPack] = dict(self._builtin_packs)

        # 1. Global user stories
        if self.user_stories_dir and self.user_stories_dir.is_dir():
            for pack in self._scan_directory(self.user_stories_dir):
                results[pack.story_id] = pack

        # 2. Character-specific stories (takes precedence over global/built-in)
        if self.characters_dir and character_pack_id:
            try:
                target_pack_dir = (self.characters_dir / character_pack_id).resolve()
                characters_dir_resolved = self.characters_dir.resolve()
                target_pack_dir.relative_to(characters_dir_resolved)
                if target_pack_dir != characters_dir_resolved:
                    char_stories_dir = target_pack_dir / "stories"
                    if char_stories_dir.is_dir():
                        for pack in self._scan_directory(char_stories_dir):
                            results[pack.story_id] = pack
            except ValueError:
                pass

        return results

    def _scan_directory(self, directory: Path) -> list[StoryPack]:
        """Scan a directory for .md, .json, and .yaml files and load them with mtime caching."""
        packs: list[StoryPack] = []
        try:
            for path in directory.glob("*.*"):
                if path.suffix.lower() not in {".md", ".json", ".yaml", ".yml"}:
                    continue
                try:
                    pack = self._load_file(path)
                    if pack:
                        packs.append(pack)
                except Exception as exc:
                    logger.warning("Failed to load story pack from %s: %s", path, exc)
        except Exception as exc:
            logger.warning("Failed to scan story directory %s: %s", directory, exc)
        return packs

    def _load_file(self, path: Path) -> StoryPack | None:
        """Load and cache a story file, hot-reloading if mtime changed."""
        mtime = path.stat().st_mtime
        cached = self._file_cache.get(path)
        if cached and cached[0] == mtime:
            return cached[1]

        content = path.read_text(encoding="utf-8")
        pack: StoryPack | None = None
        ext = path.suffix.lower()

        if ext == ".md":
            pack = compile_story_markdown(content)
        elif ext == ".json":
            raw = json.loads(content)
            pack = StoryPack(**raw)
            validate_story_pack_graph(pack)
        elif ext in {".yaml", ".yml"}:
            raw = yaml.safe_load(content)
            pack = StoryPack(**raw)
            validate_story_pack_graph(pack)

        if pack:
            self._file_cache[path] = (mtime, pack)
        return pack

    def save_story(self, character_pack_id: str, file_name: str, content: str) -> StoryPack | None:
        """Validate and save a story or cover image file into character or user stories directory."""
        clean_name = Path(file_name).name
        suffix = Path(clean_name).suffix.lower()
        if suffix not in {".md", ".json", ".yaml", ".yml", ".png", ".jpg", ".jpeg", ".webp"}:
            raise ValueError("unsupported_story_format")

        if self.characters_dir and character_pack_id:
            target_pack_dir = (self.characters_dir / character_pack_id).resolve()
            characters_dir_resolved = self.characters_dir.resolve()
            try:
                target_pack_dir.relative_to(characters_dir_resolved)
            except ValueError:
                raise ValueError("invalid_character_pack_path")
            if target_pack_dir == characters_dir_resolved:
                raise ValueError("invalid_character_pack_path")
            target_dir = target_pack_dir / "stories"
        elif self.user_stories_dir:
            target_dir = self.user_stories_dir.resolve()
        else:
            raise ValueError("no_story_directory_configured")

        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = (target_dir / clean_name).resolve()
        try:
            target_path.relative_to(target_dir.resolve())
        except ValueError:
            raise ValueError("invalid_target_path")

        # Support direct cover image uploads (.png, .jpg, .jpeg, .webp)
        if suffix in {".png", ".jpg", ".jpeg", ".webp"}:
            import base64
            raw_b64 = content.split(",", 1)[-1] if "," in content else content
            data_bytes = base64.b64decode(raw_b64)
            temp_path = target_dir / f".tmp_{target_path.name}"
            temp_path.write_bytes(data_bytes)
            temp_path.replace(target_path)
            return None

        # Validate syntax/structure before writing scripts
        if suffix == ".md":
            pack = compile_story_markdown(content)
        elif suffix == ".json":
            pack = StoryPack(**json.loads(content))
            validate_story_pack_graph(pack)
        else:
            pack = StoryPack(**yaml.safe_load(content))
            validate_story_pack_graph(pack)

        temp_path = target_dir / f".tmp_{target_path.name}"
        temp_path.write_text(content, encoding="utf-8")
        temp_path.replace(target_path)

        self._file_cache[target_path] = (target_path.stat().st_mtime, pack)
        return pack
