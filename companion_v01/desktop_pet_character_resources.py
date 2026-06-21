from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

from .character_context_library import CharacterContextLibraryService
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
        self.context_libraries = CharacterContextLibraryService(
            characters_dir=self.characters_dir,
        )

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

    def list_character_packs(self) -> list[dict[str, str]]:
        """List installed Creator Kit character packs without exposing paths."""
        try:
            entries = sorted(self.characters_dir.iterdir(), key=lambda item: item.name.lower())
        except OSError:
            return []
        packs: list[dict[str, str]] = []
        for entry in entries:
            if not entry.is_dir():
                continue
            pack_id = sanitize_character_pack_id(entry.name)
            if not pack_id or pack_id != entry.name:
                continue
            if not (entry / "character.json").is_file():
                continue
            identity = _as_dict(_load_json(entry / "character.json").get("identity"))
            name = _clean_text(identity.get("name")) or pack_id
            app_name = _clean_text(identity.get("app_name")) or name
            user_title = _clean_text(identity.get("user_title")) or "用户"
            packs.append(
                {
                    "pack_id": pack_id,
                    "id": pack_id,
                    "name": name,
                    "app_name": app_name,
                    "user_title": user_title,
                }
            )
        return packs

    def build_character_identity(self, character_pack_id: str) -> dict[str, str]:
        """Return the resolved display identity for a character pack.

        Returns a dict with keys ``assistant_name``, ``user_label``,
        ``app_name``, and ``pack_id``.  Returns an empty dict when the
        pack id is invalid or the pack directory is missing.
        """
        pack_id = sanitize_character_pack_id(character_pack_id)
        if not pack_id:
            return {}
        pack_dir = self._resolve_pack_dir(pack_id)
        if pack_dir is None or not pack_dir.is_dir():
            return {}
        character = _load_json(pack_dir / "character.json")
        identity = _as_dict(character.get("identity"))
        name = _clean_text(identity.get("name")) or pack_id
        app_name = _clean_text(identity.get("app_name")) or name
        user_title = _clean_text(identity.get("user_title")) or "用户"
        character_id = _clean_text(identity.get("id")) or pack_id
        return {
            "character_id": character_id,
            "assistant_name": name,
            "user_label": user_title,
            "app_name": app_name,
            "pack_id": pack_id,
        }

    def build_character_voice_preference(self, character_pack_id: str) -> dict[str, str]:
        """Return the character pack's preferred voice provider hints.

        The values are declarative preferences only. They do not grant access to
        local model files and should be resolved by the capability registry
        before any runtime uses them.
        """
        pack_id = sanitize_character_pack_id(character_pack_id)
        if not pack_id:
            return {}
        pack_dir = self._resolve_pack_dir(pack_id)
        if pack_dir is None or not pack_dir.is_dir():
            return {}
        character = _load_json(pack_dir / "character.json")
        voice = _as_dict(character.get("voice"))
        provider = _clean_text(voice.get("provider"))
        profile_id = _clean_text(voice.get("profile_id") or voice.get("profileId"))
        notes = _truncate_text(_clean_text(voice.get("notes")), limit=160)
        if not (provider or profile_id or notes):
            return {}
        return {
            "packId": pack_id,
            "provider": provider,
            "profileId": profile_id,
            "notes": notes,
        }

    def build_persona_prompt_context(
        self,
        character_pack_id: str,
        *,
        resource_manifest: ResourceManifest | None = None,
        client_mode: str = "desktop_pet",
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
        mode = str(client_mode or "desktop_pet").strip().lower()

        character = _load_json(pack_dir / "character.json")
        identity = _as_dict(character.get("identity"))
        persona_form = _as_dict(character.get("persona_form"))
        appearance = _as_dict(character.get("appearance"))
        dialogue = _as_dict(character.get("dialogue"))
        emotion_aliases = _coerce_alias_map(character.get("emotion_aliases"))
        available_emotions = _collect_available_emotions(resource_manifest)

        name = _clean_text(identity.get("name")) or pack_id
        app_name = _clean_text(identity.get("app_name")) or name
        user_title = _clean_text(identity.get("user_title")) or "用户"
        self_reference = _clean_text(identity.get("self_reference"))
        relationship = _clean_text(identity.get("relationship"))
        identity_id = _clean_text(identity.get("id")) or pack_id
        default_outfit = _clean_text(appearance.get("default_outfit"))
        default_emotion = _clean_text(appearance.get("default_emotion"))
        music_emotion = _clean_text(appearance.get("music_emotion"))
        proactive_prompt = _clean_text(dialogue.get("proactive_wake_prompt"))

        if mode == "desktop_pet":
            system_lines = [
                "[DESKTOP CHARACTER PACK - desktop_pet only]",
                f"- 当前角色包：{app_name} / {name} (pack_id={pack_id}, identity.id={identity_id})",
                f"- 默认称呼用户：{user_title}",
                "- 这是 desktop_pet 客户端本轮选中的角色包；底层项目名、进程名或旧提示里的 Akane 只是项目代号，当前桌宠身份优先服从这个角色包。",
                "- emotion 必须从当前角色包资源清单里的可用表情中选择；如果想表达的情绪没有对应图片，选择语义最接近的可用表情，不要编造新的 emotion。",
                "- character.outfit 优先沿用当前服装；只有用户明确要求或资源清单确实支持时才切换。",
            ]
        elif mode == "qq_text":
            system_lines = [
                "[CHARACTER PACK - qq_text]",
                f"- 当前 QQ 聊天角色包：{app_name} / {name} (pack_id={pack_id}, identity.id={identity_id})",
                f"- 默认称呼用户：{user_title}",
                "- 这是 QQ 文字客户端本轮选中的角色包；底层项目名、进程名或旧提示里的 Akane 只是项目代号，当前聊天身份优先服从这个角色包。",
                "- QQ 端只发送文字、文件或工具结果，不渲染桌宠立绘、服装、场景或 BGM；保持角色语气和边界，不要规划视觉演出。",
                "- emotion 仍与桌宠共用当前角色包的表情图片变量，并且会先于 speech 生成；它必须使用现存图片文件名去掉扩展名后的稳定 id。",
            ]
        else:
            system_lines = [
                "[CHARACTER PACK]",
                f"- 当前角色包：{app_name} / {name} (pack_id={pack_id}, identity.id={identity_id})",
                f"- 默认称呼用户：{user_title}",
                "- 底层项目名、进程名或旧提示里的 Akane 只是项目代号；当前角色身份优先服从这个角色包。",
            ]
        if self_reference:
            system_lines.append(f"- 角色自称：{self_reference}")
        if relationship:
            system_lines.append(f"- 角色与用户关系：{relationship}")
        default_parts = []
        if default_outfit:
            default_parts.append(f"默认服装={default_outfit}")
        if default_emotion:
            default_parts.append(f"默认表情={default_emotion}")
        if music_emotion:
            default_parts.append(f"音乐表情={music_emotion}")
        if mode == "desktop_pet" and default_parts:
            system_lines.append(f"- 角色包默认值：{'; '.join(default_parts)}")
        if proactive_prompt:
            system_lines.append(f"- 主动搭话风格参考：{proactive_prompt}")

        if mode == "qq_text" and resource_manifest is not None:
            system_lines.append(resource_manifest.build_emotion_prompt_context())

        alias_lines = _format_alias_lines(emotion_aliases, available_emotions=available_emotions)
        if mode in {"desktop_pet", "qq_text"} and alias_lines:
            system_lines.append("- 常用情绪意图映射（语义标签 -> 当前角色包表情优先级）：")
            system_lines.extend(f"  - {line}" for line in alias_lines[:ALIAS_PROMPT_LIMIT])
        if mode in {"desktop_pet", "qq_text"}:
            context_library_prompt = self.context_libraries.build_prompt_context(pack_id)
            if context_library_prompt:
                system_lines.append(context_library_prompt)

        reference_sections: list[str] = []
        emotion_normalizer = (
            lambda value: resource_manifest.normalize_emotion_id(
                value,
                preferred_outfit=default_outfit,
            )
            if resource_manifest is not None
            else _clean_text(value)
        )
        persona_form_text = _format_persona_form(
            persona_form,
            emotion_normalizer=emotion_normalizer,
        )
        if persona_form_text:
            reference_sections.append("[角色包 persona_form]\n" + persona_form_text)

        persona_text = _read_text(pack_dir / "persona.md")
        if persona_text:
            reference_sections.append(
                "[角色包 persona.md]\n"
                + _truncate_text(persona_text, limit=PERSONA_PROMPT_CHAR_LIMIT)
            )

        click_lines = _coerce_click_lines(dialogue.get("local_click_lines"))
        if click_lines:
            rendered = [
                f"- {item['text']} (emotion={emotion_normalizer(item['emotion'])})"
                for item in click_lines[:CLICK_LINE_PROMPT_LIMIT]
            ]
            reference_sections.append("[本地点击台词风格参考]\n" + "\n".join(rendered))

        return {
            "system_context": "\n".join(system_lines).strip(),
            "reference_context": "\n\n".join(reference_sections).strip(),
            "active_id": identity_id,
        }

    def load_care_shop_items(self, character_pack_id: str) -> list[dict]:
        """Load care.shop_items from character.json. Returns [] if unavailable."""
        pack_id = sanitize_character_pack_id(character_pack_id)
        if not pack_id:
            return []
        pack_dir = self._resolve_pack_dir(pack_id)
        if pack_dir is None:
            return []
        try:
            data = _load_json(pack_dir / "character.json")
            items = data.get("care", {}).get("shop_items")
            if not isinstance(items, list):
                return []
            return [
                item for item in items
                if isinstance(item, dict) and item.get("id") and item.get("name")
            ]
        except Exception:
            return []

    def load_qq_delivery_config(self, character_pack_id: str) -> dict:
        """Load qq_delivery from character.json. Returns {} if unavailable."""
        pack_id = sanitize_character_pack_id(character_pack_id)
        if not pack_id:
            return {}
        pack_dir = self._resolve_pack_dir(pack_id)
        if pack_dir is None:
            return {}
        try:
            data = _load_json(pack_dir / "character.json")
            qq_delivery = data.get("qq_delivery")
            if not isinstance(qq_delivery, dict):
                return {}
            return _expand_qq_delivery_emotion_mface_aliases(
                qq_delivery,
                _coerce_alias_map(data.get("emotion_aliases")),
            )
        except Exception:
            return {}

    def resolve_emotion_image_file(self, character_pack_id: str, emotion: str) -> dict[str, str]:
        """Resolve a character pack emotion image to a local file for QQ image delivery."""
        manifest = self.get_manifest(character_pack_id)
        if manifest is None:
            return {}
        try:
            runtime_manifest = manifest.build_runtime_manifest()
            emotion_id = manifest.normalize_emotion_id(emotion)
        except Exception:
            return {}
        outfits = _as_dict(runtime_manifest.get("characters")).get("outfits")
        if not isinstance(outfits, list):
            return {}
        fallback: dict[str, Any] | None = None
        for outfit in outfits:
            if not isinstance(outfit, dict):
                continue
            for entry in outfit.get("emotions") or []:
                if not isinstance(entry, dict):
                    continue
                if fallback is None:
                    fallback = entry
                entry_id = _clean_text(entry.get("id"))
                entry_name = _clean_text(entry.get("name"))
                if emotion_id and entry_id != emotion_id and entry_name != emotion_id:
                    continue
                resolved = _resolve_manifest_asset_file(manifest, entry)
                if resolved:
                    return {
                        "path": str(resolved),
                        "emotion": entry_id or emotion_id,
                        "name": entry_name or entry_id or emotion_id,
                    }
        if fallback is not None:
            resolved = _resolve_manifest_asset_file(manifest, fallback)
            if resolved:
                fallback_id = _clean_text(fallback.get("id"))
                fallback_name = _clean_text(fallback.get("name"))
                return {
                    "path": str(resolved),
                    "emotion": fallback_id,
                    "name": fallback_name or fallback_id,
                }
        return {}

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


def _expand_qq_delivery_emotion_mface_aliases(
    qq_delivery: dict[str, Any],
    emotion_aliases: dict[str, list[str]],
) -> dict[str, Any]:
    if not isinstance(qq_delivery, dict):
        return {}
    delivery = copy.deepcopy(qq_delivery)
    emotion_mfaces = delivery.get("emotion_mfaces")
    if not isinstance(emotion_mfaces, dict):
        return delivery
    mapping = emotion_mfaces.get("map")
    if not isinstance(mapping, dict) or not mapping:
        return delivery

    expanded = dict(mapping)
    for raw_key, mface in mapping.items():
        for candidate in _emotion_alias_candidates(raw_key, emotion_aliases):
            expanded.setdefault(candidate, mface)
    emotion_mfaces["map"] = expanded
    return delivery


def _emotion_alias_candidates(value: Any, emotion_aliases: dict[str, list[str]]) -> list[str]:
    raw = _clean_text(value)
    if not raw:
        return []
    candidates: list[str] = [raw]
    raw_key = _emotion_lookup_key(raw)
    for alias_key, alias_values in (emotion_aliases or {}).items():
        group = [_clean_text(alias_key), *[_clean_text(item) for item in alias_values]]
        group = [item for item in group if item]
        if not group:
            continue
        if raw_key in {_emotion_lookup_key(item) for item in group}:
            candidates.extend(group)
    return list(dict.fromkeys(candidates))


def _emotion_lookup_key(value: Any) -> str:
    return re.sub(r"[-\s]+", "_", _clean_text(value).lower())


def _resolve_manifest_asset_file(manifest: ResourceManifest, entry: dict[str, Any]) -> Path | None:
    public_path = _clean_text(entry.get("path"))
    public_prefix = _clean_text(getattr(manifest, "public_prefix", ""))
    if not public_path or not public_prefix:
        return None
    prefix = public_prefix.rstrip("/") + "/"
    if not public_path.startswith(prefix):
        return None
    relative = public_path[len(prefix):].lstrip("/")
    try:
        candidate = (manifest.assets_dir / relative).resolve()
        assets_root = manifest.assets_dir.resolve()
    except OSError:
        return None
    if candidate == assets_root or assets_root not in candidate.parents:
        return None
    if not candidate.is_file():
        return None
    return candidate


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


def _format_persona_form(
    value: dict[str, Any],
    *,
    emotion_normalizer=None,
) -> str:
    if not value:
        return ""
    lines: list[str] = []
    fields = [
        ("personality_keywords", "性格关键词"),
        ("character_core", "角色核心"),
        ("behavior_style", "行为倾向"),
        ("speaking_style", "说话风格"),
        ("catchphrases", "常用表达"),
        ("boundaries", "边界与禁忌"),
        ("interaction_principles", "互动原则"),
        ("proactive_style", "主动搭话风格"),
        ("extra_setting", "补充设定"),
    ]
    for key, label in fields:
        raw = value.get(key)
        if isinstance(raw, list):
            text = "、".join(_coerce_string_list(raw))
        else:
            text = _clean_text(raw)
        if text:
            lines.append(f"- {label}: {text}")
    examples = []
    for item in value.get("example_lines") or []:
        if not isinstance(item, dict):
            continue
        text = _clean_text(item.get("text"))
        emotion = _clean_text(item.get("emotion"))
        if emotion and emotion_normalizer is not None:
            emotion = _clean_text(emotion_normalizer(emotion))
        if text:
            examples.append(f"- {text}" + (f" (emotion={emotion})" if emotion else ""))
    if examples:
        lines.append("示例台词:")
        lines.extend(examples[:CLICK_LINE_PROMPT_LIMIT])
    return "\n".join(lines).strip()


def _truncate_text(value: str, *, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"
