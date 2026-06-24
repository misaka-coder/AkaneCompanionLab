from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PACK_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")
MARKDOWN_SUFFIX = ".md"


@dataclass(frozen=True)
class CharacterContextFile:
    target: str
    library_folder: str
    library_name: str
    name: str
    path: Path


class CharacterContextLibraryService:
    """Read creator-authored context files declared by a character pack."""

    def __init__(self, *, characters_dir: Path) -> None:
        self.characters_dir = Path(characters_dir)

    def has_available_context(self, character_pack_id: str) -> bool:
        return any(library["files"] for library in self.build_manifest(character_pack_id)["libraries"])

    def build_manifest(self, character_pack_id: str) -> dict[str, Any]:
        pack_dir = self._resolve_pack_dir(character_pack_id)
        if pack_dir is None or not pack_dir.is_dir():
            return {"pack_id": "", "libraries": []}

        character = _load_json(pack_dir / "character.json")
        raw_libraries = character.get("context_libraries")
        if not isinstance(raw_libraries, list):
            return {"pack_id": pack_dir.name, "libraries": []}

        libraries: list[dict[str, Any]] = []
        seen_folders: set[str] = set()
        for raw_library in raw_libraries:
            if not isinstance(raw_library, dict):
                continue
            folder = _clean_text(raw_library.get("folder"))
            if not _is_safe_folder_name(folder) or folder in seen_folders:
                continue
            seen_folders.add(folder)
            alias_map = _coerce_alias_map(raw_library.get("aliases"))

            library_dir = _safe_direct_child(pack_dir, folder)
            if library_dir is None or not library_dir.is_dir():
                files: list[dict[str, str]] = []
            else:
                files = self._scan_library_files(
                    library_dir=library_dir,
                    folder=folder,
                    library_name=_clean_text(raw_library.get("name")) or folder,
                    alias_map=alias_map,
                )

            libraries.append(
                {
                    "folder": folder,
                    "name": _clean_text(raw_library.get("name")) or folder,
                    "description": _clean_text(raw_library.get("description")),
                    "load_when": _clean_text(raw_library.get("load_when")),
                    "files": files,
                }
            )

        return {"pack_id": pack_dir.name, "libraries": libraries}

    def build_prompt_context(self, character_pack_id: str) -> str:
        manifest = self.build_manifest(character_pack_id)
        libraries = [library for library in manifest["libraries"] if library["files"]]
        if not libraries:
            return ""

        lines = [
            "[CHARACTER CONTEXT LIBRARIES]",
            "以下资料库由当前角色包声明，内容是你本来就知道的角色设定。读取资料是内部聚焦，不要向用户解释自己在读取文件。",
        ]
        for library in libraries:
            lines.append(f"- {library['name']}（library={library['folder']}）")
            if library["description"]:
                lines.append(f"  用途：{library['description']}")
            if library["load_when"]:
                lines.append(f"  何时读取：{library['load_when']}")
            targets = "、".join(_format_prompt_target(file) for file in library["files"])
            lines.append(f"  可读取目标：{targets}")
        lines.extend(
            [
                "- 当当前对话直接涉及清单中的内容，或可靠回答需要其中细节时，主动使用 load_character_context；一次可以批量读取多个目标。",
                '- 调用格式：{"type":"load_character_context","targets":["library/文件名","library/另一个文件名"]}。',
                "- targets 只能填写上面明确列出的目标，不要猜测目录、路径或扩展名。",
                "- 当前消息直接出现文件名时，程序可能已经把正文放进本轮已读取资料；已读取的目标不要重复调用。",
                "- 尚未读取具体正文时，不要根据印象补写其中的具体事实；读取后自然作答，不要复述文件结构或工具过程。",
            ]
        )
        return "\n".join(lines)

    def build_automatic_context(self, character_pack_id: str, text: str) -> str:
        result = self.load_automatic_context(character_pack_id, text)
        return str(result.get("followup_context") or "").strip()

    def load_automatic_context(self, character_pack_id: str, text: str) -> dict[str, Any]:
        message = _clean_text(text)
        if not message:
            return _empty_load_result()
        manifest = self.build_manifest(character_pack_id)
        targets: list[str] = []
        matches: list[dict[str, Any]] = []
        folded_message = message.casefold()
        for library in manifest.get("libraries") or []:
            if not isinstance(library, dict):
                continue
            for file in library.get("files") or []:
                if not isinstance(file, dict):
                    continue
                name = _clean_text(file.get("name"))
                target = _clean_text(file.get("target"))
                terms = _dedupe_strings([name, *(file.get("aliases") or [])])
                matched_terms = [
                    term
                    for term in terms
                    if term.casefold() in folded_message
                ]
                if target and matched_terms:
                    targets.append(target)
                    matches.append(
                        {
                            "target": target,
                            "matched_terms": matched_terms,
                        }
                    )
        if not targets:
            return _empty_load_result()
        result = self.load_context(character_pack_id, targets)
        result["matches"] = matches
        return result

    def load_context(self, character_pack_id: str, targets: list[str]) -> dict[str, Any]:
        manifest = self.build_manifest(character_pack_id)
        index = self._build_file_index(manifest)
        results: list[dict[str, str]] = []

        for requested_target in _dedupe_strings(targets):
            target = _normalize_target(requested_target)
            entry = index.get(target)
            if entry is None:
                results.append(
                    {
                        "target": requested_target,
                        "status": "not_found",
                        "reason": "target_not_in_character_manifest",
                    }
                )
                continue
            try:
                content = entry.path.read_text(encoding="utf-8-sig").strip()
            except Exception:
                results.append(
                    {
                        "target": entry.target,
                        "status": "read_failed",
                        "reason": "file_unavailable",
                    }
                )
                continue
            if not content:
                results.append(
                    {
                        "target": entry.target,
                        "status": "empty",
                        "reason": "file_empty",
                    }
                )
                continue
            results.append(
                {
                    "target": entry.target,
                    "status": "loaded",
                    "library": entry.library_name,
                    "name": entry.name,
                    "content": content,
                }
            )

        loaded = [item for item in results if item["status"] == "loaded"]
        failed = [item for item in results if item["status"] != "loaded"]
        return {
            "status": "loaded" if loaded and not failed else "partial" if loaded else "unavailable",
            "loaded": loaded,
            "failed": failed,
            "followup_context": _build_followup_context(loaded, failed),
        }

    def _scan_library_files(
        self,
        *,
        library_dir: Path,
        folder: str,
        library_name: str,
        alias_map: dict[str, list[str]],
    ) -> list[dict[str, Any]]:
        try:
            entries = sorted(library_dir.iterdir(), key=lambda item: item.name.casefold())
        except OSError:
            return []

        files: list[dict[str, Any]] = []
        resolved_library_dir = library_dir.resolve()
        for entry in entries:
            if entry.suffix.lower() != MARKDOWN_SUFFIX or not entry.is_file():
                continue
            try:
                resolved_entry = entry.resolve()
            except OSError:
                continue
            if resolved_entry.parent != resolved_library_dir:
                continue
            name = entry.stem.strip()
            if not name:
                continue
            files.append(
                {
                    "target": f"{folder}/{name}",
                    "library": library_name,
                    "name": name,
                    "aliases": alias_map.get(name, []),
                }
            )
        return files

    def _build_file_index(self, manifest: dict[str, Any]) -> dict[str, CharacterContextFile]:
        pack_dir = self._resolve_pack_dir(str(manifest.get("pack_id") or ""))
        if pack_dir is None:
            return {}

        index: dict[str, CharacterContextFile] = {}
        for library in manifest.get("libraries") or []:
            if not isinstance(library, dict):
                continue
            folder = _clean_text(library.get("folder"))
            library_dir = _safe_direct_child(pack_dir, folder)
            if library_dir is None:
                continue
            for file in library.get("files") or []:
                if not isinstance(file, dict):
                    continue
                target = _clean_text(file.get("target"))
                name = _clean_text(file.get("name"))
                if not target or not name:
                    continue
                path = _safe_direct_child(library_dir, f"{name}{MARKDOWN_SUFFIX}")
                if path is None or not path.is_file():
                    continue
                index[target] = CharacterContextFile(
                    target=target,
                    library_folder=folder,
                    library_name=_clean_text(library.get("name")) or folder,
                    name=name,
                    path=path,
                )
        return index

    def _resolve_pack_dir(self, character_pack_id: str) -> Path | None:
        pack_id = _clean_text(character_pack_id)
        if not pack_id or not PACK_ID_PATTERN.fullmatch(pack_id):
            return None
        return _safe_direct_child(self.characters_dir, pack_id)


def _safe_direct_child(parent: Path, name: str) -> Path | None:
    if not _is_safe_folder_name(name):
        return None
    try:
        resolved_parent = parent.resolve()
        resolved_target = (resolved_parent / name).resolve()
    except OSError:
        return None
    if resolved_target.parent != resolved_parent:
        return None
    return resolved_target


def _is_safe_folder_name(value: str) -> bool:
    text = _clean_text(value)
    if not text or text in {".", ".."} or text.lower() == "_local":
        return False
    return "/" not in text and "\\" not in text and "\x00" not in text


def _normalize_target(value: str) -> str:
    target = _clean_text(value).replace("\\", "/")
    if target.lower().endswith(MARKDOWN_SUFFIX):
        target = target[: -len(MARKDOWN_SUFFIX)]
    return target


def _coerce_alias_map(value: Any) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        return {}
    aliases: dict[str, list[str]] = {}
    for raw_name, raw_aliases in value.items():
        name = _clean_text(raw_name)
        if not name:
            continue
        if isinstance(raw_aliases, str):
            candidates = re.split(r"[,，;；、\n]+", raw_aliases)
        elif isinstance(raw_aliases, (list, tuple, set)):
            candidates = list(raw_aliases)
        else:
            continue
        normalized = [
            alias
            for alias in _dedupe_strings([str(item or "") for item in candidates])
            if alias != name
        ]
        if normalized:
            aliases[name] = normalized
    return aliases


def _format_prompt_target(file: dict[str, Any]) -> str:
    target = _clean_text(file.get("target"))
    aliases = _dedupe_strings([str(item or "") for item in file.get("aliases") or []])
    if not aliases:
        return target
    return f"{target}（别名：{'、'.join(aliases)}）"


def _dedupe_strings(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _clean_text(value)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _empty_load_result() -> dict[str, Any]:
    return {
        "status": "unavailable",
        "loaded": [],
        "failed": [],
        "matches": [],
        "followup_context": "",
    }


def _build_followup_context(
    loaded: list[dict[str, str]],
    failed: list[dict[str, str]],
) -> str:
    lines = ["【本轮已读取的角色资料】"]
    if loaded:
        for item in loaded:
            lines.extend(
                [
                    f"[{item['library']} / {item['name']}]",
                    item["content"],
                    "",
                ]
            )
    else:
        lines.append("(没有成功读取任何资料。)")
    if failed:
        lines.append("【未读取的目标】")
        for item in failed:
            lines.append(f"- {item['target']}：{item['reason']}")
    lines.append("请把已读取内容作为当前角色设定自然运用，不要向用户描述文件、路径或读取过程。")
    return "\n".join(lines).strip()


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _clean_text(value: Any) -> str:
    return str(value or "").strip()
