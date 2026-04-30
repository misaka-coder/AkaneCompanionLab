from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .resource_manifest import ResourceManifest


PACK_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")
PERSONA_PROMPT_CHAR_LIMIT = 2600
ALIAS_PROMPT_LIMIT = 14
CLICK_LINE_PROMPT_LIMIT = 8


class DesktopPetCharacterResourceService:
    def __init__(
        self,
        *,
        characters_dir: Path,
        public_prefix: str = "/desktop-pet-character-packs",
    ) -> None:
        self.characters_dir = Path(characters_dir)
        self.public_prefix = f"/{str(public_prefix or '').strip('/')}"
        self._cache: dict[str, tuple[ResourceManifest, float]] = {}

    def get_manifest(self, character_pack_id: str) -> ResourceManifest | None:
        pack_id = sanitize_character_pack_id(character_pack_id)
        if not pack_id:
            return None

        pack_dir = self._resolve_pack_dir(pack_id)
        if pack_dir is None or not pack_dir.is_dir():
            return None

        assets_dir = pack_dir / "assets"
        if not assets_dir.is_dir():
            return None

        metadata_mtime = _file_mtime(pack_dir / "character.json")
        cached = self._cache.get(pack_id)
        if cached is not None:
            cached_manifest, cached_metadata_mtime = cached
            if cached_manifest.assets_dir == assets_dir and cached_metadata_mtime == metadata_mtime:
                return cached_manifest

        manifest = ResourceManifest(
            assets_dir,
            public_prefix=f"{self.public_prefix}/{pack_id}/assets",
            emotion_aliases=self._load_emotion_aliases(pack_dir),
        )
        self._cache[pack_id] = (manifest, metadata_mtime)
        return manifest

    def build_runtime_manifest(self, character_pack_id: str) -> dict | None:
        manifest = self.get_manifest(character_pack_id)
        if manifest is None:
            return None
        manifest.refresh()
        return manifest.build_runtime_manifest()

    def build_persona_prompt_context(
        self,
        character_pack_id: str,
        *,
        resource_manifest: ResourceManifest | None = None,
    ) -> dict[str, str]:
        pack_id = sanitize_character_pack_id(character_pack_id)
        if not pack_id:
            return {"system_context": "", "reference_context": "", "active_id": ""}

        pack_dir = self._resolve_pack_dir(pack_id)
        if pack_dir is None or not pack_dir.is_dir():
            return {"system_context": "", "reference_context": "", "active_id": ""}
        resource_manifest = self._resolve_prompt_manifest(
            pack_id=pack_id,
            pack_dir=pack_dir,
            resource_manifest=resource_manifest,
        )

        character = _load_json(pack_dir / "character.json")
        identity = _as_dict(character.get("identity"))
        appearance = _as_dict(character.get("appearance"))
        dialogue = _as_dict(character.get("dialogue"))
        emotion_aliases = _coerce_alias_map(character.get("emotion_aliases"))
        available_emotions = _collect_available_emotions(resource_manifest)

        name = _clean_text(identity.get("name")) or pack_id
        app_name = _clean_text(identity.get("app_name")) or name
        user_title = _clean_text(identity.get("user_title")) or "用户"
        identity_id = _clean_text(identity.get("id")) or pack_id
        default_outfit = _clean_text(appearance.get("default_outfit"))
        default_emotion = _clean_text(appearance.get("default_emotion"))
        music_emotion = _clean_text(appearance.get("music_emotion"))
        proactive_prompt = _clean_text(dialogue.get("proactive_wake_prompt"))

        system_lines = [
            "[DESKTOP CHARACTER PACK - desktop_pet only]",
            f"- 当前角色包：{app_name} / {name} (pack_id={pack_id}, identity.id={identity_id})",
            f"- 默认称呼用户：{user_title}",
            "- 这是 desktop_pet 客户端本轮选中的角色包；底层项目名、进程名或旧提示里的 Akane 只是项目代号，当前桌宠身份优先服从这个角色包。",
            "- emotion 必须从当前角色包资源清单里的可用表情中选择；如果想表达的情绪没有对应图片，选择语义最接近的可用表情，不要编造新的 emotion。",
            "- character.outfit 优先沿用当前服装；只有用户明确要求或资源清单确实支持时才切换。",
        ]
        default_parts = []
        if default_outfit:
            default_parts.append(f"默认服装={default_outfit}")
        if default_emotion:
            default_parts.append(f"默认表情={default_emotion}")
        if music_emotion:
            default_parts.append(f"音乐表情={music_emotion}")
        if default_parts:
            system_lines.append(f"- 角色包默认值：{'; '.join(default_parts)}")
        if proactive_prompt:
            system_lines.append(f"- 主动搭话风格参考：{proactive_prompt}")

        alias_lines = _format_alias_lines(emotion_aliases, available_emotions=available_emotions)
        if alias_lines:
            system_lines.append("- 常用情绪意图映射（语义标签 -> 当前角色包表情优先级）：")
            system_lines.extend(f"  - {line}" for line in alias_lines[:ALIAS_PROMPT_LIMIT])

        reference_sections: list[str] = []
        persona_text = _read_text(pack_dir / "persona.md")
        if persona_text:
            reference_sections.append(
                "[角色包 persona.md]\n"
                + _truncate_text(persona_text, limit=PERSONA_PROMPT_CHAR_LIMIT)
            )

        click_lines = _coerce_click_lines(dialogue.get("local_click_lines"))
        if click_lines:
            rendered = [
                f"- {item['text']} (emotion={item['emotion']})"
                for item in click_lines[:CLICK_LINE_PROMPT_LIMIT]
            ]
            reference_sections.append("[本地点击台词风格参考]\n" + "\n".join(rendered))

        return {
            "system_context": "\n".join(system_lines).strip(),
            "reference_context": "\n\n".join(reference_sections).strip(),
            "active_id": "",
        }

    def _resolve_pack_dir(self, pack_id: str) -> Path | None:
        base = self.characters_dir.resolve()
        target = (base / pack_id).resolve()
        if target != base and base in target.parents:
            return target
        return None

    def _load_emotion_aliases(self, pack_dir: Path) -> dict[str, list[str]]:
        return _coerce_alias_map(_load_json(pack_dir / "character.json").get("emotion_aliases"))

    def _resolve_prompt_manifest(
        self,
        *,
        pack_id: str,
        pack_dir: Path,
        resource_manifest: ResourceManifest | None,
    ) -> ResourceManifest | None:
        expected_assets_dir = (pack_dir / "assets").resolve()
        if resource_manifest is not None:
            try:
                if resource_manifest.assets_dir.resolve() == expected_assets_dir:
                    return resource_manifest
            except OSError:
                pass
        return self.get_manifest(pack_id)


def sanitize_character_pack_id(value: str) -> str:
    pack_id = str(value or "").strip()
    if not pack_id or not PACK_ID_PATTERN.fullmatch(pack_id):
        return ""
    return pack_id


def _file_mtime(path: Path) -> float:
    try:
        return float(path.stat().st_mtime)
    except OSError:
        return 0.0


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _read_text(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _coerce_string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return []


def _coerce_alias_map(value: Any) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        return {}
    aliases: dict[str, list[str]] = {}
    for key, raw_values in value.items():
        alias_key = _clean_text(key)
        if not alias_key:
            continue
        values = _coerce_string_list(raw_values)
        if values:
            aliases[alias_key] = list(dict.fromkeys(values))
    return aliases


def _collect_available_emotions(resource_manifest: ResourceManifest | None) -> set[str]:
    if resource_manifest is None:
        return set()
    try:
        runtime_manifest = resource_manifest.build_runtime_manifest()
    except Exception:
        return set()
    outfits = _as_dict(runtime_manifest.get("characters")).get("outfits")
    if not isinstance(outfits, list):
        return set()
    emotions: set[str] = set()
    for outfit in outfits:
        if not isinstance(outfit, dict):
            continue
        for emotion in outfit.get("emotions") or []:
            if isinstance(emotion, dict):
                emotion_id = _clean_text(emotion.get("id"))
                emotion_name = _clean_text(emotion.get("name"))
                if emotion_id:
                    emotions.add(emotion_id)
                if emotion_name:
                    emotions.add(emotion_name)
    return emotions


def _format_alias_lines(
    aliases: dict[str, list[str]],
    *,
    available_emotions: set[str],
) -> list[str]:
    lines: list[str] = []
    for key, values in aliases.items():
        filtered = [value for value in values if not available_emotions or value in available_emotions]
        if not filtered and not available_emotions:
            filtered = values
        if not filtered:
            continue
        lines.append(f"{key} -> {', '.join(filtered[:5])}")
    return lines


def _coerce_click_lines(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    items: list[dict[str, str]] = []
    for raw in value:
        if not isinstance(raw, dict):
            continue
        text = _clean_text(raw.get("text"))
        emotion = _clean_text(raw.get("emotion"))
        if text and emotion:
            items.append({"text": text, "emotion": emotion})
    return items


def _truncate_text(value: str, *, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"
