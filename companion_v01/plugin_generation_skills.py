"""Path-free Skill projection for one isolated PluginHost generation.

The worker freezes already validated plugin Skills into a generation-private
mount tree.  The protocol publishes only Skill names and opaque mount aliases;
the parent reconstructs filesystem roots from its own work directory and
validates the copies again before exposing them to ``SkillRegistry``.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import uuid
from pathlib import Path
from typing import Iterable, Mapping

from .skill_runtime import (
    ContributedSkillRoot,
    SkillError,
    validate_contributed_skill_root,
)


_MOUNT_RE = re.compile(r"^plugin_skill_[a-f0-9]{16}$")


class PluginGenerationSkillError(ValueError):
    """A Skill snapshot cannot cross the generation boundary safely."""


def generation_skill_export_dir(work_dir: Path, generation_id: str) -> Path:
    return (Path(work_dir).resolve() / "skill-mounts" / generation_id).resolve()


def export_generation_skills(
    roots: Iterable[ContributedSkillRoot],
    *,
    export_dir: Path,
    generation_id: str,
) -> list[dict[str, str]]:
    """Freeze validated roots and return a path-free ready projection."""

    contributions = tuple(roots)
    if not contributions:
        return []
    target = Path(export_dir).resolve()
    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)
    staging = parent / f".{target.name}.tmp-{uuid.uuid4().hex}"
    records: list[dict[str, str]] = []
    seen_names: set[str] = set()
    seen_mounts: set[str] = set()
    try:
        staging.mkdir(parents=False, exist_ok=False)
        for contribution in contributions:
            if not isinstance(contribution, ContributedSkillRoot):
                raise PluginGenerationSkillError("plugin_skill_projection_invalid")
            try:
                entry = validate_contributed_skill_root(contribution.root)
            except (OSError, SkillError, TypeError, ValueError) as exc:
                raise PluginGenerationSkillError(
                    "plugin_skill_projection_invalid"
                ) from exc
            mount_name = _generation_mount_name(
                contribution.mount_name,
                generation_id=generation_id,
            )
            if entry.name in seen_names or mount_name in seen_mounts:
                raise PluginGenerationSkillError("plugin_skill_projection_invalid")
            destination = staging / mount_name / entry.name
            shutil.copytree(entry.root, destination)
            try:
                copied = validate_contributed_skill_root(destination)
            except (OSError, SkillError, TypeError, ValueError) as exc:
                raise PluginGenerationSkillError(
                    "plugin_skill_projection_invalid"
                ) from exc
            if (
                copied.name != entry.name
                or copied.instruction_sha256 != entry.instruction_sha256
            ):
                raise PluginGenerationSkillError("plugin_skill_projection_invalid")
            seen_names.add(entry.name)
            seen_mounts.add(mount_name)
            records.append({"name": entry.name, "mount_name": mount_name})
        if target.exists():
            raise PluginGenerationSkillError("plugin_skill_projection_invalid")
        staging.replace(target)
    except PluginGenerationSkillError:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    except Exception as exc:
        shutil.rmtree(staging, ignore_errors=True)
        raise PluginGenerationSkillError("plugin_skill_projection_failed") from exc
    records.sort(key=lambda item: item["name"].casefold())
    return records


def decode_generation_skill_roots(
    value: object,
    *,
    export_dir: Path,
    plugin_id: str,
    declared_names: Iterable[str],
) -> tuple[ContributedSkillRoot, ...]:
    """Restore and verify the worker's immutable Skill mount projection."""

    if not isinstance(value, list):
        raise PluginGenerationSkillError("plugin_skill_projection_invalid")
    raw_declared_names = tuple(declared_names)
    if any(not isinstance(item, str) or not item for item in raw_declared_names):
        raise PluginGenerationSkillError("plugin_skill_projection_invalid")
    expected_names = tuple(sorted(set(raw_declared_names), key=str.casefold))
    roots: list[ContributedSkillRoot] = []
    seen_names: set[str] = set()
    seen_mounts: set[str] = set()
    base = Path(export_dir).resolve()
    for raw in value:
        if not isinstance(raw, Mapping):
            raise PluginGenerationSkillError("plugin_skill_projection_invalid")
        name = raw.get("name")
        mount_name = raw.get("mount_name")
        if (
            not isinstance(name, str)
            or not name
            or not isinstance(mount_name, str)
            or _MOUNT_RE.fullmatch(mount_name) is None
            or name in seen_names
            or mount_name in seen_mounts
        ):
            raise PluginGenerationSkillError("plugin_skill_projection_invalid")
        root = (base / mount_name / name).resolve(strict=False)
        try:
            root.relative_to(base)
            entry = validate_contributed_skill_root(root)
        except (OSError, SkillError, TypeError, ValueError) as exc:
            raise PluginGenerationSkillError(
                "plugin_skill_projection_invalid"
            ) from exc
        if entry.name != name:
            raise PluginGenerationSkillError("plugin_skill_projection_invalid")
        seen_names.add(name)
        seen_mounts.add(mount_name)
        roots.append(
            ContributedSkillRoot(
                source=f"plugin:{plugin_id}",
                root=root,
                mount_name=mount_name,
            )
        )
    if tuple(sorted(seen_names, key=str.casefold)) != expected_names:
        raise PluginGenerationSkillError("plugin_skill_projection_invalid")
    roots.sort(key=lambda item: item.root.name.casefold())
    return tuple(roots)


def _generation_mount_name(base_mount: str, *, generation_id: str) -> str:
    clean_base = str(base_mount or "").strip()
    clean_generation = str(generation_id or "").strip()
    if not clean_base or not clean_generation:
        raise PluginGenerationSkillError("plugin_skill_projection_invalid")
    material = f"{clean_base}\0{clean_generation}".encode("utf-8")
    return f"plugin_skill_{hashlib.sha256(material).hexdigest()[:16]}"


__all__ = [
    "PluginGenerationSkillError",
    "decode_generation_skill_roots",
    "export_generation_skills",
    "generation_skill_export_dir",
]
