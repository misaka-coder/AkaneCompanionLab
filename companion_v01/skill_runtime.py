"""Hot-reloadable, progressively disclosed Akane skills.

Skills are instruction packages, not executable plugins and not a second tool
registry.  The registry exposes deterministic name/description metadata to the
prompt, while ``load_skill`` opens the full instructions only when requested.
Scripts remain ordinary files executed through the existing ``exec_run`` tool.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import threading
from typing import Any, Iterable
import uuid

import yaml


SKILL_FILE_NAME = "SKILL.md"
SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
SKILL_TOOL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
SKILL_MAX_DESCRIPTION_CHARS = 512
SKILL_MAX_INSTRUCTION_BYTES = 256 * 1024
SKILL_MAX_RESOURCE_BYTES = 256 * 1024
SKILL_MAX_PACKAGE_BYTES = 8 * 1024 * 1024
SKILL_MAX_PACKAGE_FILES = 256
SKILL_MAX_PROMPT_ITEMS = 100
SKILL_MAX_PROMPT_CHARS = 16_000
SKILL_MAX_LISTED_FILES = 80
SKILL_DRAFT_ROOT = "skill_drafts"
SKILL_MANAGED_MOUNT = "skills"
SKILL_BUNDLED_MOUNT = "bundled_skills"


class SkillError(ValueError):
    """Structured skill validation/discovery failure."""

    def __init__(self, reason: str, *, detail: str = "") -> None:
        self.reason = str(reason or "skill_error")
        self.detail = str(detail or "")
        super().__init__(self.reason if not self.detail else f"{self.reason}:{self.detail}")


@dataclass(frozen=True)
class SkillEntry:
    name: str
    description: str
    source: str
    root: Path
    instructions: str
    instruction_sha256: str
    files: tuple[str, ...]
    required_tools: tuple[str, ...] = ()

    @property
    def revision(self) -> str:
        return self.instruction_sha256[:16]

    @property
    def execution_cwd(self) -> str:
        return f"alias:{SKILL_MANAGED_MOUNT if self.source == 'managed' else SKILL_BUNDLED_MOUNT}"


@dataclass(frozen=True)
class SkillSnapshot:
    entries: tuple[SkillEntry, ...]
    catalog_revision: str
    diagnostics: tuple[dict[str, str], ...] = ()

    def by_name(self) -> dict[str, SkillEntry]:
        return {entry.name: entry for entry in self.entries}


@dataclass(frozen=True)
class SkillReadResult:
    status: str
    name: str = ""
    revision: str = ""
    resource: str = ""
    content: str = ""
    execution_cwd: str = ""
    execution_path: str = ""
    files: tuple[str, ...] = ()
    reason: str = ""
    available: tuple[str, ...] = ()


@dataclass(frozen=True)
class SkillPublishResult:
    status: str
    name: str = ""
    revision: str = ""
    replaced: bool = False
    reason: str = ""
    catalog_revision: str = ""


def _clean_description(value: Any) -> str:
    text = str(value or "").replace("`", " ")
    text = "".join(" " if ord(char) < 32 else char for char in text)
    text = " ".join(text.split())
    if not text:
        raise SkillError("skill_description_required")
    if len(text) > SKILL_MAX_DESCRIPTION_CHARS:
        raise SkillError("skill_description_too_long")
    return text


def _parse_required_tools(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise SkillError("skill_required_tools_list_required")
    tools: list[str] = []
    seen: set[str] = set()
    for raw in value:
        if not isinstance(raw, str):
            raise SkillError("skill_required_tool_invalid")
        tool_name = raw.strip()
        if not SKILL_TOOL_NAME_RE.fullmatch(tool_name):
            raise SkillError("skill_required_tool_invalid", detail=tool_name[:80])
        if tool_name not in seen:
            seen.add(tool_name)
            tools.append(tool_name)
    return tuple(tools)


def _parse_skill_markdown(path: Path) -> tuple[str, str, str, tuple[str, ...]]:
    try:
        raw_bytes = path.read_bytes()
    except OSError as exc:
        raise SkillError("skill_read_failed", detail=type(exc).__name__) from exc
    if len(raw_bytes) > SKILL_MAX_INSTRUCTION_BYTES:
        raise SkillError("skill_instructions_too_large")
    try:
        raw = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SkillError("skill_instructions_not_utf8") from exc
    lines = raw.splitlines()
    if not lines or lines[0].strip() != "---":
        raise SkillError("skill_frontmatter_required")
    end = next((index for index in range(1, len(lines)) if lines[index].strip() == "---"), None)
    if end is None:
        raise SkillError("skill_frontmatter_unclosed")
    try:
        metadata = yaml.safe_load("\n".join(lines[1:end])) or {}
    except yaml.YAMLError as exc:
        raise SkillError("skill_frontmatter_invalid") from exc
    if not isinstance(metadata, dict):
        raise SkillError("skill_frontmatter_object_required")
    name = str(metadata.get("name") or "").strip()
    if not SKILL_NAME_RE.fullmatch(name):
        raise SkillError("skill_name_invalid")
    description = _clean_description(metadata.get("description"))
    skill_metadata = metadata.get("metadata")
    if skill_metadata is None:
        skill_metadata = {}
    if not isinstance(skill_metadata, dict):
        raise SkillError("skill_metadata_object_required")
    required_tools = _parse_required_tools(skill_metadata.get("required_tools"))
    body = "\n".join(lines[end + 1 :]).strip()
    if not body:
        raise SkillError("skill_instructions_required")
    digest = hashlib.sha256(raw_bytes).hexdigest()
    return name, description, body, required_tools


def _safe_relative_file(root: Path, relative: str) -> Path:
    raw = str(relative or "").strip().replace("\\", "/")
    if not raw or raw.startswith("/") or ".." in Path(raw).parts:
        raise SkillError("skill_resource_path_invalid")
    candidate = (root / raw).resolve(strict=False)
    resolved_root = root.resolve(strict=True)
    try:
        candidate.relative_to(resolved_root)
    except ValueError as exc:
        raise SkillError("skill_resource_path_escape") from exc
    if candidate.is_symlink():
        raise SkillError("skill_resource_symlink_not_allowed")
    if not candidate.is_file():
        raise SkillError("skill_resource_not_found")
    return candidate


def _iter_package_files(root: Path) -> Iterable[tuple[Path, str]]:
    count = 0
    total = 0
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix().casefold()):
        if path.is_symlink():
            raise SkillError("skill_package_symlink_not_allowed")
        if not path.is_file():
            continue
        count += 1
        if count > SKILL_MAX_PACKAGE_FILES:
            raise SkillError("skill_package_too_many_files")
        try:
            total += path.stat().st_size
        except OSError as exc:
            raise SkillError("skill_package_stat_failed") from exc
        if total > SKILL_MAX_PACKAGE_BYTES:
            raise SkillError("skill_package_too_large")
        yield path, path.relative_to(root).as_posix()


def _entry_from_dir(root: Path, *, source: str, enforce_directory_name: bool = True) -> SkillEntry:
    if root.is_symlink() or not root.is_dir():
        raise SkillError("skill_directory_invalid")
    skill_path = root / SKILL_FILE_NAME
    if not skill_path.is_file() or skill_path.is_symlink():
        raise SkillError("skill_file_missing")
    name, description, body, required_tools = _parse_skill_markdown(skill_path)
    if enforce_directory_name and name != root.name:
        raise SkillError("skill_name_directory_mismatch", detail=f"{root.name}!={name}")
    files = tuple(
        relative
        for _path, relative in _iter_package_files(root)
        if relative not in {SKILL_FILE_NAME, ".akane-skill.json"}
    )
    return SkillEntry(
        name=name,
        description=description,
        source=source,
        root=root.resolve(strict=True),
        instructions=body,
        instruction_sha256=hashlib.sha256(skill_path.read_bytes()).hexdigest(),
        files=files,
        required_tools=required_tools,
    )


class SkillRegistry:
    """Deterministic request-time discovery with last-good reload semantics."""

    def __init__(
        self,
        *,
        bundled_root: str | Path,
        managed_root: str | Path,
        execution_workspace_root: str | Path,
    ) -> None:
        self.bundled_root = Path(bundled_root).resolve(strict=False)
        self.managed_root = Path(managed_root).resolve(strict=False)
        self.execution_workspace_root = Path(execution_workspace_root).resolve(strict=False)
        self.managed_root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._last_good: dict[tuple[str, str], SkillEntry] = {}

    def mount_paths(self) -> dict[str, Path]:
        paths = {SKILL_MANAGED_MOUNT: self.managed_root}
        if self.bundled_root.is_dir():
            paths[SKILL_BUNDLED_MOUNT] = self.bundled_root
        return paths

    @staticmethod
    def _child_dirs(root: Path) -> list[Path]:
        if not root.is_dir():
            return []
        try:
            return sorted(
                (item for item in root.iterdir() if item.is_dir() and not item.name.startswith(".")),
                key=lambda item: item.name.casefold(),
            )
        except OSError:
            return []

    def snapshot(self) -> SkillSnapshot:
        with self._lock:
            discovered: dict[tuple[str, str], SkillEntry] = {}
            diagnostics: list[dict[str, str]] = []
            for source, root in (("bundled", self.bundled_root), ("managed", self.managed_root)):
                for child in self._child_dirs(root):
                    key = (source, child.name)
                    try:
                        entry = _entry_from_dir(child, source=source)
                    except SkillError as exc:
                        previous = self._last_good.get(key)
                        if previous is not None:
                            discovered[key] = previous
                        diagnostics.append(
                            {
                                "name": child.name,
                                "source": source,
                                "reason": exc.reason,
                                "fallback": "last_good" if previous is not None else "ignored",
                            }
                        )
                        continue
                    discovered[key] = entry
            self._last_good = discovered
            merged: dict[str, SkillEntry] = {}
            for source in ("bundled", "managed"):
                for key, entry in discovered.items():
                    if key[0] == source:
                        merged[entry.name] = entry
            entries = tuple(sorted(merged.values(), key=lambda item: item.name.casefold()))
            catalog_material = json.dumps(
                [
                    (entry.name, entry.description, entry.source, entry.required_tools)
                    for entry in entries
                ],
                ensure_ascii=False,
                separators=(",", ":"),
            )
            revision = hashlib.sha256(catalog_material.encode("utf-8")).hexdigest()[:16]
            return SkillSnapshot(entries=entries, catalog_revision=revision, diagnostics=tuple(diagnostics))

    def prompt_catalog(self, *, available_tool_names: Iterable[str] | None = None) -> str:
        snapshot = self.snapshot()
        available = (
            None
            if available_tool_names is None
            else {str(item).strip() for item in available_tool_names if str(item).strip()}
        )
        visible_entries = tuple(
            entry
            for entry in snapshot.entries
            if available is None or set(entry.required_tools).issubset(available)
        )
        lines = [
            f"【可按需加载的 Skills（目录版本 {snapshot.catalog_revision}）】",
            "Skill 是任务操作手册，不会增加权限或自动执行代码。任务明确匹配某项描述时，先调用 load_skill 读取完整说明；不要一次加载所有 Skill。",
        ]
        used = len("\n".join(lines))
        included = 0
        for entry in visible_entries:
            line = f"- {entry.name}：{entry.description}"
            if included >= SKILL_MAX_PROMPT_ITEMS or used + len(line) + 1 > SKILL_MAX_PROMPT_CHARS:
                break
            lines.append(line)
            used += len(line) + 1
            included += 1
        if not snapshot.entries:
            lines.append("- 当前没有已安装的 Skill。")
        elif not visible_entries:
            lines.append("- 当前工具集合没有可加载的 Skill。")
        elif included < len(visible_entries):
            lines.append(f"- 目录已截到 {included}/{len(visible_entries)} 项；可用 load_skill 按已知名称打开。")
        if snapshot.diagnostics:
            fallback_count = sum(item.get("fallback") == "last_good" for item in snapshot.diagnostics)
            ignored_count = len(snapshot.diagnostics) - fallback_count
            lines.append(
                f"- 热重载诊断：{fallback_count} 项继续使用上一有效版本，{ignored_count} 项无效更新暂未加载；可用 manage_skill(validate) 检查草稿。"
            )
        return "\n".join(lines)

    def load(self, name: str, *, resource: str = "") -> SkillReadResult:
        snapshot = self.snapshot()
        clean_name = str(name or "").strip()
        entry = snapshot.by_name().get(clean_name)
        if entry is None:
            return SkillReadResult(
                status="not_found",
                name=clean_name,
                reason="skill_not_found",
                available=tuple(item.name for item in snapshot.entries),
            )
        relative = str(resource or "").strip().replace("\\", "/")
        if relative:
            try:
                path = _safe_relative_file(entry.root, relative)
                raw = path.read_bytes()
            except SkillError as exc:
                return SkillReadResult(status="error", name=entry.name, reason=exc.reason)
            except OSError:
                return SkillReadResult(status="error", name=entry.name, reason="skill_resource_read_failed")
            if len(raw) > SKILL_MAX_RESOURCE_BYTES:
                return SkillReadResult(status="error", name=entry.name, reason="skill_resource_too_large")
            try:
                content = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
            except UnicodeDecodeError:
                return SkillReadResult(status="error", name=entry.name, reason="skill_resource_not_utf8")
            resource_revision = hashlib.sha256(raw).hexdigest()[:16]
            return SkillReadResult(
                status="loaded",
                name=entry.name,
                revision=resource_revision,
                resource=relative,
                content=content,
                execution_cwd=entry.execution_cwd,
                execution_path=f"{entry.name}/{relative}",
                files=entry.files[:SKILL_MAX_LISTED_FILES],
            )
        return SkillReadResult(
            status="loaded",
            name=entry.name,
            revision=entry.revision,
            resource=SKILL_FILE_NAME,
            content=entry.instructions,
            execution_cwd=entry.execution_cwd,
            execution_path=f"{entry.name}/{SKILL_FILE_NAME}",
            files=entry.files[:SKILL_MAX_LISTED_FILES],
        )

    def _resolve_draft(self, draft_path: str) -> Path:
        raw = str(draft_path or "").strip().replace("\\", "/")
        parts = Path(raw).parts
        if not raw or Path(raw).is_absolute() or ".." in parts:
            raise SkillError("skill_draft_path_invalid")
        candidate = (self.execution_workspace_root / raw).resolve(strict=False)
        try:
            candidate.relative_to(self.execution_workspace_root)
        except ValueError as exc:
            raise SkillError("skill_draft_path_escape") from exc
        if candidate.is_symlink() or not candidate.is_dir():
            raise SkillError("skill_draft_directory_missing")
        return candidate

    def validate_draft(self, draft_path: str) -> SkillPublishResult:
        with self._lock:
            try:
                draft = self._resolve_draft(draft_path)
                entry = _entry_from_dir(draft, source="managed")
            except SkillError as exc:
                return SkillPublishResult(status="invalid", reason=exc.reason)
            return SkillPublishResult(status="valid", name=entry.name, revision=entry.revision)

    def publish(self, draft_path: str, *, replace: bool = False) -> SkillPublishResult:
        with self._lock:
            try:
                draft = self._resolve_draft(draft_path)
                draft_entry = _entry_from_dir(draft, source="managed")
            except SkillError as exc:
                return SkillPublishResult(status="invalid", reason=exc.reason)
            destination = self.managed_root / draft_entry.name
            existed = destination.exists()
            if existed and not replace:
                return SkillPublishResult(status="conflict", name=draft_entry.name, reason="skill_exists")
            token = uuid.uuid4().hex
            staging = self.managed_root / f".{draft_entry.name}.tmp-{token}"
            backup = self.managed_root / f".{draft_entry.name}.old-{token}"
            try:
                shutil.copytree(draft, staging, symlinks=True)
                staged_entry = _entry_from_dir(staging, source="managed", enforce_directory_name=False)
                marker = {
                    "schema_version": 1,
                    "name": staged_entry.name,
                    "revision": staged_entry.revision,
                }
                (staging / ".akane-skill.json").write_text(
                    json.dumps(marker, ensure_ascii=False, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                if existed:
                    os.replace(destination, backup)
                try:
                    os.replace(staging, destination)
                except Exception:
                    if existed and backup.exists():
                        os.replace(backup, destination)
                    raise
                if backup.exists():
                    shutil.rmtree(backup, ignore_errors=True)
            except (OSError, SkillError) as exc:
                if staging.exists():
                    shutil.rmtree(staging, ignore_errors=True)
                reason = exc.reason if isinstance(exc, SkillError) else "skill_publish_failed"
                return SkillPublishResult(status="error", name=draft_entry.name, reason=reason)
            snapshot = self.snapshot()
            published = snapshot.by_name().get(draft_entry.name)
            if published is None:
                return SkillPublishResult(status="error", name=draft_entry.name, reason="skill_reload_failed")
            return SkillPublishResult(
                status="published",
                name=published.name,
                revision=published.revision,
                replaced=existed,
                catalog_revision=snapshot.catalog_revision,
            )


def resolve_skill_roots(config_module: Any) -> tuple[Path, Path, Path]:
    """Return bundled, managed and execution-workspace roots without leaking them."""

    base_dir = Path(getattr(config_module, "BASE_DIR", Path(__file__).resolve().parent.parent))
    data_root = Path(getattr(config_module, "DATA_ROOT", base_dir / "users_data"))
    configured_workspace = str(getattr(config_module, "EXECUTION_WORKSPACE_ROOT", "") or "").strip()
    execution_workspace = Path(configured_workspace) if configured_workspace else data_root / "execution_workspace"
    return base_dir / "skills", data_root / "skills", execution_workspace
