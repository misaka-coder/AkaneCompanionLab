from __future__ import annotations

import json
from pathlib import Path
from typing import Any


IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
AUDIO_EXTS = {".mp3", ".ogg", ".wav", ".m4a", ".flac"}
NOTE_FILENAMES = (
    "ai.md",
    "ai.txt",
    "prompt.md",
    "prompt.txt",
    "说明.md",
    "说明.txt",
    "readme.md",
    "readme.txt",
    "notes.md",
    "notes.txt",
)
SIDECAR_NOTE_SUFFIXES = (".md", ".txt", ".note.md", ".note.txt")
META_NOTE_KEYS = ("notes", "note", "prompt", "ai_hint", "ai_prompt", "usage")
PROMPT_NOTE_LIMIT = 240
EMOTION_ALIASES = {
    "normal": "normal",
    "shy": "shy",
    "smug": "smug",
    "sumg": "smug",
    "cry": "cry",
}
EMOTION_FALLBACK_CANDIDATES = {
    "normal": ["正常", "normal"],
    "idle": ["正常", "normal"],
    "quiet": ["正常", "normal"],
    "happy": ["开心", "卖萌", "得意", "smug", "normal"],
    "joy": ["开心", "卖萌", "得意", "smug", "normal"],
    "smile": ["开心", "卖萌", "得意", "smug", "normal"],
    "smug": ["得意", "smug", "开心", "normal"],
    "sumg": ["得意", "smug", "开心", "normal"],
    "shy": ["脸红", "求摸摸", "shy", "normal"],
    "embarrassed": ["脸红", "shy", "normal"],
    "cry": ["困困", "无语", "cry", "sad", "normal"],
    "sad": ["困困", "无语", "cry", "normal"],
    "angry": ["气鼓鼓", "angry", "normal"],
    "thinking": ["思考中", "困惑", "normal"],
    "confused": ["困惑", "思考中", "normal"],
    "listening": ["侧耳听", "正常", "normal"],
    "music": ["听歌中", "开心", "normal"],
    "sleepy": ["困困", "打哈欠", "normal"],
    "tired": ["困困", "打哈欠", "normal"],
    "pet": ["被摸头", "求摸摸", "开心", "normal"],
}
BACKGROUND_ALIASES = {
    "morning": "morning",
    "sunrise": "morning",
    "dawn": "morning",
    "day": "morning",
    "daytime": "morning",
    "清晨": "morning",
    "早晨": "morning",
    "早上": "morning",
    "白天": "morning",
    "afternoon": "afternoon",
    "noon": "afternoon",
    "午后": "afternoon",
    "下午": "afternoon",
    "evening": "evening",
    "dusk": "evening",
    "sunset": "evening",
    "黄昏": "evening",
    "傍晚": "evening",
    "晚上": "evening",
    "night": "night",
    "midnight": "night",
    "深夜": "night",
    "夜晚": "night",
    "夜里": "night",
}
BACKGROUND_LABELS = {
    "morning": "清晨",
    "afternoon": "午后",
    "evening": "黄昏",
    "night": "深夜",
}
EMOTION_LABELS = {
    "normal": "平静",
    "shy": "害羞",
    "smug": "得意",
    "cry": "哭哭",
}
EMOTION_PRIORITY = {
    "normal": 0,
    "shy": 1,
    "smug": 2,
    "cry": 3,
}
DEFAULT_OUTFIT_PRIORITY = {
    "default": 0,
}
BACKGROUND_PRIORITY = {
    "evening": 0,
    "morning": 1,
    "afternoon": 2,
    "night": 3,
}


def _is_image(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in IMAGE_EXTS


def _is_audio(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in AUDIO_EXTS


def _is_ignored_dir(path: Path) -> bool:
    return path.is_dir() and path.name.lower() in {"backup", "backups", "备份", "__pycache__"}


def _load_meta(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _load_sidecar_meta(path: Path) -> dict[str, Any]:
    return _load_meta(path.with_suffix(".meta.json"))


def _load_text(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def _normalize_key(name: Any) -> str:
    return str(name or "").strip().lower().replace("-", "_").replace(" ", "_")


def _coerce_priority(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _humanize(name: str) -> str:
    if name == "default":
        return "默认"
    if name in BACKGROUND_LABELS:
        return BACKGROUND_LABELS[name]
    if name in EMOTION_LABELS:
        return EMOTION_LABELS[name]
    return name.replace("_", " ").replace("-", " ")


def _legacy_canon_emotion(name: str) -> str | None:
    return EMOTION_ALIASES.get(_normalize_key(name))


def _canon_emotion(name: str) -> str | None:
    raw = str(name or "").strip()
    if not raw:
        return None
    return EMOTION_ALIASES.get(_normalize_key(raw), raw)


def _normalize_emotion_id(name: Any) -> str:
    raw = str(name or "").strip()
    if not raw:
        return ""
    return _canon_emotion(raw) or raw


def _emotion_candidate_ids(
    name: Any,
    *,
    emotion_aliases: dict[str, list[str]] | None = None,
) -> list[str]:
    raw = str(name or "").strip()
    candidates: list[str] = []

    def add(value: Any) -> None:
        text = str(value or "").strip()
        if text and text not in candidates:
            candidates.append(text)

    add(_normalize_emotion_id(raw))
    for item in EMOTION_FALLBACK_CANDIDATES.get(_normalize_key(raw), []):
        add(item)
    for item in (emotion_aliases or {}).get(_normalize_key(raw), []):
        add(item)
    return candidates


def _is_known_background_token(name: Any) -> bool:
    key = _normalize_key(name)
    return key in BACKGROUND_ALIASES or str(name or "").strip() in BACKGROUND_PRIORITY


def _canon_background(name: Any) -> str:
    raw = str(name or "").strip()
    if not raw:
        return ""
    key = _normalize_key(raw)
    return BACKGROUND_ALIASES.get(key, raw)


def _normalize_note_text(value: str, *, limit: int = PROMPT_NOTE_LIMIT) -> str:
    compact = " ".join(str(value or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: max(0, limit - 1)].rstrip() + "…"


def _join_text_parts(parts: list[str]) -> str:
    seen: set[str] = set()
    result: list[str] = []
    for part in parts:
        normalized = _normalize_note_text(part)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return " ".join(result)


def _coerce_string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return []


def _coerce_emotion_alias_map(value: Any) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        return {}
    alias_map: dict[str, list[str]] = {}
    for key, aliases in value.items():
        normalized_key = _normalize_key(key)
        if not normalized_key:
            continue
        items = _coerce_string_list(aliases)
        if items:
            alias_map[normalized_key] = list(dict.fromkeys(items))
    return alias_map


class ResourceManifest:
    def __init__(
        self,
        assets_dir: Path,
        public_prefix: str = "/assets",
        *,
        emotion_aliases: dict[str, list[str]] | None = None,
    ):
        self.assets_dir = Path(assets_dir)
        self.public_prefix = self._normalize_public_prefix(public_prefix)
        self.emotion_aliases = _coerce_emotion_alias_map(emotion_aliases or {})
        self._manifest: dict[str, Any] | None = None

    def refresh(self) -> dict[str, Any]:
        self._manifest = self._scan()
        return self._manifest

    def get_manifest(self) -> dict[str, Any]:
        if self._manifest is None:
            return self.refresh()
        return self._manifest

    def build_runtime_manifest(
        self,
        *,
        extra_bgm_tracks: list[dict[str, Any]] | None = None,
        extra_scene_groups: list[dict[str, Any]] | None = None,
        extra_character_outfits: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return self._merge_manifest_with_runtime_projection(
            self.get_manifest(),
            extra_bgm_tracks=extra_bgm_tracks or [],
            extra_scene_groups=extra_scene_groups or [],
            extra_character_outfits=extra_character_outfits or [],
        )

    def build_prompt_context(
        self,
        *,
        extra_bgm_tracks: list[dict[str, Any]] | None = None,
        extra_scene_groups: list[dict[str, Any]] | None = None,
        extra_character_outfits: list[dict[str, Any]] | None = None,
    ) -> str:
        manifest = self.build_runtime_manifest(
            extra_bgm_tracks=extra_bgm_tracks,
            extra_scene_groups=extra_scene_groups,
            extra_character_outfits=extra_character_outfits,
        )
        lines: list[str] = []

        global_notes = self._collect_global_notes()
        if global_notes:
            lines.append("资源说明：")
            lines.extend(f"- {note}" for note in global_notes)

        scene_lines: list[str] = []
        for major in manifest["scenes"]["majors"]:
            for minor in major["minors"]:
                scene_label = self._format_scene_label(major, minor)
                bg_ids = ", ".join(self._format_resource_label(background) for background in minor["backgrounds"]) or "(无)"
                scene_lines.append(f"- {scene_label} -> 背景: {bg_ids}")
        if scene_lines:
            lines.append("场景输出规则：scene.major/minor/background 使用清单列出的名称或 id，不要把未列出的文件名自行拆成新场景。")
            lines.append("可用场景与背景：")
            lines.extend(scene_lines)

        outfit_lines: list[str] = []
        for outfit in manifest["characters"]["outfits"]:
            outfit_label = self._format_resource_label(outfit)
            emotions = ", ".join(self._format_resource_label(emotion) for emotion in outfit["emotions"]) or "(无)"
            outfit_lines.append(f"- {outfit_label} -> 表情: {emotions}")
        if outfit_lines:
            lines.append("可用服装与表情：")
            lines.extend(outfit_lines)

        bgm_lines: list[str] = []
        for major in manifest["scenes"]["majors"]:
            for minor in major["minors"]:
                if minor["bgm_tracks"]:
                    scene_label = self._format_scene_label(major, minor)
                    track_ids = ", ".join(self._format_resource_label(track) for track in minor["bgm_tracks"])
                    bgm_lines.append(f"- {scene_label} -> BGM: {track_ids}")
        if bgm_lines:
            lines.append("可用 BGM：")
            lines.extend(bgm_lines)

        extra_tracks = [item for item in (extra_bgm_tracks or []) if isinstance(item, dict)]
        if extra_tracks:
            lines.append("你的私人收藏 BGM（可在任意场景使用）：")
            lines.extend(f"- {self._format_resource_label(track)}" for track in extra_tracks)

        return "\n".join(lines) if lines else "当前没有额外的视觉资源。"

    def build_character_prompt_context(
        self,
        *,
        extra_character_outfits: list[dict[str, Any]] | None = None,
    ) -> str:
        manifest = self.build_runtime_manifest(
            extra_character_outfits=extra_character_outfits,
        )
        lines: list[str] = [
            "桌宠模式只渲染角色服装立绘和表情，不渲染场景、背景或 BGM。",
            "输出时优先沿用当前 character.outfit，只在同一套服装下选择可用 emotion；确实需要换衣服时才切换 character.outfit。",
        ]

        outfit_lines: list[str] = []
        for outfit in manifest["characters"]["outfits"]:
            outfit_label = self._format_resource_label(outfit)
            emotions = ", ".join(self._format_resource_label(emotion) for emotion in outfit["emotions"]) or "(无)"
            outfit_lines.append(f"- {outfit_label} -> 表情: {emotions}")
        if outfit_lines:
            lines.append("可用服装与表情：")
            lines.extend(outfit_lines)

        return "\n".join(lines) if outfit_lines else "当前没有额外的角色视觉资源。"

    def build_emotion_prompt_context(self) -> str:
        manifest = self.build_runtime_manifest()
        lines = [
            "emotion 与桌宠共用当前角色包的表情图片变量。",
            "即使当前客户端不渲染立绘，也必须直接使用图片文件名去掉扩展名后的稳定 id；不要编造或改写 emotion。",
        ]
        outfit_lines: list[str] = []
        for outfit in manifest["characters"]["outfits"]:
            emotion_ids = ", ".join(
                str(emotion.get("id") or "").strip()
                for emotion in outfit.get("emotions") or []
                if str(emotion.get("id") or "").strip()
            )
            if emotion_ids:
                outfit_lines.append(f"- {outfit['id']}: {emotion_ids}")
        if outfit_lines:
            lines.append("当前角色包可用 emotion：")
            lines.extend(outfit_lines)
        return "\n".join(lines)

    def normalize_emotion_id(self, value: Any, *, preferred_outfit: str = "") -> str:
        manifest = self.build_runtime_manifest()
        defaults = manifest["defaults"]
        outfits = manifest["characters"]["outfits"]
        outfit = (
            self._find_outfit(manifest, preferred_outfit)
            or self._find_outfit(manifest, defaults["outfit"])
            or outfits[0]
        )
        requested = _normalize_emotion_id(value) or defaults["emotion"]
        emotion = self._find_emotion_with_aliases(outfit, requested)
        if emotion is None:
            for candidate_outfit in outfits:
                emotion = self._find_emotion_with_aliases(candidate_outfit, requested)
                if emotion is not None:
                    break
        if emotion is None:
            emotion = self._find_emotion_with_aliases(outfit, defaults["emotion"]) or outfit["emotions"][0]
        return str(emotion["id"])

    def normalize_emotion_output(self, result: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(result or {})
        character = normalized.get("character") if isinstance(normalized.get("character"), dict) else {}
        normalized["emotion"] = self.normalize_emotion_id(
            normalized.get("emotion"),
            preferred_outfit=str(character.get("outfit") or ""),
        )
        return normalized

    def normalize_visual_output(
        self,
        result: dict[str, Any],
        *,
        extra_bgm_tracks: list[dict[str, Any]] | None = None,
        extra_scene_groups: list[dict[str, Any]] | None = None,
        extra_character_outfits: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        manifest = self.build_runtime_manifest(
            extra_bgm_tracks=extra_bgm_tracks,
            extra_scene_groups=extra_scene_groups,
            extra_character_outfits=extra_character_outfits,
        )
        defaults = manifest["defaults"]

        if not isinstance(result.get("scene"), dict):
            result["scene"] = {}
        if not isinstance(result.get("character"), dict):
            result["character"] = {}

        requested_background_id = result["scene"].get("background")
        major_id = str(result["scene"].get("major") or defaults["major"])
        major = self._find_major(manifest, major_id) or self._find_major(manifest, defaults["major"])
        minor_id = str(result["scene"].get("minor") or defaults["minor"])
        minor = self._find_minor(major, minor_id)
        inferred_minor_background: tuple[dict[str, Any], dict[str, Any]] | None = None
        if minor is None:
            inferred_minor_background = self._find_background_across_minors(major, requested_background_id)
        minor = (
            minor
            or (inferred_minor_background[0] if inferred_minor_background else None)
            or self._find_minor(major, defaults["minor"])
            or major["minors"][0]
        )

        background_id = self._normalize_background_id(requested_background_id)
        background = (
            inferred_minor_background[1]
            if inferred_minor_background and inferred_minor_background[0]["id"] == minor["id"]
            else self._find_background(minor, background_id)
        )
        if background is None:
            inferred_minor_background = self._find_background_across_minors(major, requested_background_id)
            if inferred_minor_background is not None:
                minor, background = inferred_minor_background
        if background is None:
            background = self._find_background(minor, defaults["background"]) or minor["backgrounds"][0]

        outfit_id = str(result["character"].get("outfit") or defaults["outfit"])
        outfit = self._find_outfit(manifest, outfit_id) or self._find_outfit(manifest, defaults["outfit"]) or manifest["characters"]["outfits"][0]

        emotion_id = _normalize_emotion_id(str(result.get("emotion") or defaults["emotion"])) or defaults["emotion"]
        emotion = self._find_emotion_with_aliases(outfit, emotion_id)
        if emotion is None:
            emotion = self._find_emotion_with_aliases(outfit, defaults["emotion"]) or outfit["emotions"][0]

        bgm_id = str(result["scene"].get("bgm") or "")
        bgm = self._find_bgm(minor, bgm_id)
        if bgm is None:
            bgm = self._find_bgm(minor, background["id"])
        if bgm is None and defaults["bgm"]:
            bgm = self._find_bgm(minor, defaults["bgm"])
        if bgm is None and minor["bgm_tracks"]:
            bgm = minor["bgm_tracks"][0]

        result["emotion"] = emotion["id"]
        result["character"] = {
            "outfit": outfit["id"],
        }
        result["scene"] = {
            "major": major["id"],
            "minor": minor["id"],
            "background": background["id"],
            "bgm": bgm["id"] if bgm else "",
        }
        return result

    def describe_visual_state(
        self,
        result: dict[str, Any],
        *,
        extra_bgm_tracks: list[dict[str, Any]] | None = None,
        extra_scene_groups: list[dict[str, Any]] | None = None,
        extra_character_outfits: list[dict[str, Any]] | None = None,
    ) -> str:
        bundle = self.resolve_visual_bundle(
            result,
            extra_bgm_tracks=extra_bgm_tracks,
            extra_scene_groups=extra_scene_groups,
            extra_character_outfits=extra_character_outfits,
        )
        normalized = bundle["normalized"]
        major = bundle["major"]
        minor = bundle["minor"]
        background = bundle["background"]
        outfit = bundle["outfit"]
        emotion = bundle["emotion"]
        bgm = bundle["bgm"]

        parts = [
            f"地点: {self._format_scene_label(major, minor) if major and minor else normalized['scene']['major']}",
            f"背景: {self._format_resource_label(background) if background else normalized['scene']['background']}",
            f"服装: {self._format_resource_label(outfit) if outfit else normalized['character']['outfit']}",
            f"表情: {self._format_resource_label(emotion) if emotion else normalized['emotion']}",
            f"BGM: {self._format_resource_label(bgm) if bgm else (normalized['scene']['bgm'] or '未设置')}",
        ]
        return "；".join(parts)

    def describe_character_visual_state(
        self,
        result: dict[str, Any],
        *,
        extra_character_outfits: list[dict[str, Any]] | None = None,
    ) -> str:
        bundle = self.resolve_visual_bundle(
            result,
            extra_character_outfits=extra_character_outfits,
        )
        normalized = bundle["normalized"]
        outfit = bundle["outfit"]
        emotion = bundle["emotion"]
        available_emotions = (
            ", ".join(self._format_resource_label(item) for item in outfit["emotions"])
            if outfit
            else ""
        )

        parts = [
            f"服装: {self._format_resource_label(outfit) if outfit else normalized['character']['outfit']}",
            f"表情: {self._format_resource_label(emotion) if emotion else normalized['emotion']}",
        ]
        if available_emotions:
            parts.append(f"当前服装可用表情: {available_emotions}")
        return "；".join(parts)

    def resolve_visual_bundle(
        self,
        result: dict[str, Any],
        *,
        extra_bgm_tracks: list[dict[str, Any]] | None = None,
        extra_scene_groups: list[dict[str, Any]] | None = None,
        extra_character_outfits: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        normalized = self.normalize_visual_output(
            json.loads(json.dumps(result or {})),
            extra_bgm_tracks=extra_bgm_tracks,
            extra_scene_groups=extra_scene_groups,
            extra_character_outfits=extra_character_outfits,
        )
        manifest = self.build_runtime_manifest(
            extra_bgm_tracks=extra_bgm_tracks,
            extra_scene_groups=extra_scene_groups,
            extra_character_outfits=extra_character_outfits,
        )

        major = self._find_major(manifest, normalized["scene"]["major"])
        minor = self._find_minor(major, normalized["scene"]["minor"]) if major else None
        background = self._find_background(minor, normalized["scene"]["background"]) if minor else None
        outfit = self._find_outfit(manifest, normalized["character"]["outfit"])
        emotion = self._find_emotion(outfit, normalized["emotion"]) if outfit else None
        bgm = self._find_bgm(minor, normalized["scene"]["bgm"]) if minor else None
        return {
            "normalized": normalized,
            "manifest": manifest,
            "major": major,
            "minor": minor,
            "background": background,
            "outfit": outfit,
            "emotion": emotion,
            "bgm": bgm,
        }

    def _scan(self) -> dict[str, Any]:
        scenes_root = self.assets_dir / "scenes"
        characters_root = self.assets_dir / "characters"
        backgrounds_root = self.assets_dir / "backgrounds"
        bgm_root = self.assets_dir / "bgm"

        scenes = self._scan_scene_tree(scenes_root, bgm_root)
        fallback_scenes = self._scan_flat_backgrounds(backgrounds_root, bgm_root)
        if fallback_scenes:
            scenes.extend(fallback_scenes)
        scenes = self._merge_majors(scenes)
        if not scenes:
            scenes = self._scan_flat_backgrounds(backgrounds_root, bgm_root)

        outfits = self._scan_character_tree(characters_root)
        if not outfits:
            outfits = [self._default_outfit()]

        if not scenes:
            scenes = [self._default_scene()]

        scenes = self._sort_entries(scenes)
        outfits = self._sort_entries(outfits)

        default_major = scenes[0]["id"]
        default_minor = scenes[0]["minors"][0]["id"]
        default_background = scenes[0]["minors"][0]["backgrounds"][0]["id"]
        default_bgm = scenes[0]["minors"][0]["bgm_tracks"][0]["id"] if scenes[0]["minors"][0]["bgm_tracks"] else ""
        default_outfit = outfits[0]["id"]
        default_emotion = outfits[0]["emotions"][0]["id"]

        manifest = {
            "schema_version": 2,
            "scenes": {
                "majors": scenes,
            },
            "characters": {
                "outfits": outfits,
            },
            "defaults": {
                "major": default_major,
                "minor": default_minor,
                "background": default_background,
                "bgm": default_bgm,
                "outfit": default_outfit,
                "emotion": default_emotion,
            },
        }
        return self._strip_internal_fields(manifest)

    def _scan_scene_tree(self, scenes_root: Path, bgm_root: Path) -> list[dict[str, Any]]:
        if not scenes_root.exists():
            return []

        majors: list[dict[str, Any]] = []
        for major_dir in sorted(p for p in scenes_root.iterdir() if p.is_dir() and not _is_ignored_dir(p)):
            major_meta = _load_meta(major_dir / "meta.json")
            minors = self._collect_nested_minors(major_dir, major_meta, bgm_root)
            minors.extend(self._collect_legacy_minors(major_dir, major_meta, bgm_root))
            minors = self._merge_minors(minors)
            minors = self._sort_entries(minors, default_id=str(major_meta.get("default_minor") or ""))
            if not minors:
                continue

            major_id = str(major_meta.get("id") or major_dir.name)
            first_background = minors[0]["backgrounds"][0]["id"] if minors[0]["backgrounds"] else ""
            majors.append(
                {
                    "id": major_id,
                    "name": str(major_meta.get("name") or _humanize(major_id)),
                    "description": str(major_meta.get("description") or ""),
                    **self._build_entry_extras(major_meta, container_path=major_dir),
                    "minors": minors,
                    "_meta": major_meta,
                    "_fallback_priority": BACKGROUND_PRIORITY.get(first_background, 99),
                }
            )
        return majors

    def _collect_nested_minors(
        self,
        major_dir: Path,
        major_meta: dict[str, Any],
        bgm_root: Path,
    ) -> list[dict[str, Any]]:
        minors: list[dict[str, Any]] = []
        major_names = self._path_names(major_dir.name, major_meta.get("id"))

        for minor_dir in sorted(p for p in major_dir.iterdir() if p.is_dir() and not _is_ignored_dir(p)):
            minor_meta = _load_meta(minor_dir / "meta.json")
            default_background_id = str(minor_meta.get("default_background") or "")
            backgrounds = self._collect_backgrounds(minor_dir, default_id=default_background_id)
            if not backgrounds:
                continue

            minor_id = str(minor_meta.get("id") or minor_dir.name)
            minor_names = self._path_names(minor_dir.name, minor_id)
            bgm_tracks = self._collect_bgm_tracks_for_scene(
                bgm_root,
                major_names=major_names,
                minor_names=minor_names,
                default_id=str(minor_meta.get("default_bgm") or default_background_id or backgrounds[0]["id"]),
            )
            first_background = backgrounds[0]["id"]
            minors.append(
                {
                    "id": minor_id,
                    "name": str(minor_meta.get("name") or _humanize(minor_id)),
                    "description": str(minor_meta.get("description") or ""),
                    **self._build_entry_extras(minor_meta, container_path=minor_dir),
                    "backgrounds": backgrounds,
                    "bgm_tracks": bgm_tracks,
                    "_meta": minor_meta,
                    "_fallback_priority": BACKGROUND_PRIORITY.get(first_background, 99),
                }
            )
        return minors

    def _collect_legacy_minors(
        self,
        major_dir: Path,
        major_meta: dict[str, Any],
        bgm_root: Path,
    ) -> list[dict[str, Any]]:
        grouped: dict[str, dict[str, Any]] = {}
        major_names = self._path_names(major_dir.name, major_meta.get("id"))

        for path in sorted(major_dir.iterdir()):
            if not _is_image(path):
                continue

            raw_minor_name, raw_background_name = self._split_legacy_scene_stem(
                path.stem,
                major_name=major_dir.name,
            )
            minor_meta = _load_meta(major_dir / f"{raw_minor_name}.meta.json")
            minor_id = str(minor_meta.get("id") or raw_minor_name)
            group = grouped.setdefault(
                minor_id,
                {
                    "id": minor_id,
                    "name": str(minor_meta.get("name") or _humanize(minor_id)),
                    "description": str(minor_meta.get("description") or ""),
                    **self._build_entry_extras(minor_meta, container_path=major_dir),
                    "backgrounds": [],
                    "_raw_minor_name": raw_minor_name,
                    "_meta": minor_meta,
                },
            )
            group["backgrounds"].append(
                self._build_background_entry(
                    path,
                    background_id=raw_background_name,
                    name_hint=_humanize(raw_background_name),
                )
            )

        minors: list[dict[str, Any]] = []
        for group in grouped.values():
            default_background_id = str(group["_meta"].get("default_background") or "")
            backgrounds = self._sort_entries(
                self._merge_resource_entries(group["backgrounds"]),
                default_id=default_background_id,
            )
            if not backgrounds:
                continue

            minor_names = self._path_names(group["_raw_minor_name"], group["id"])
            bgm_tracks = self._collect_bgm_tracks_for_scene(
                bgm_root,
                major_names=major_names,
                minor_names=minor_names,
                default_id=str(group["_meta"].get("default_bgm") or default_background_id or backgrounds[0]["id"]),
            )
            first_background = backgrounds[0]["id"]
            minors.append(
                {
                    "id": group["id"],
                    "name": group["name"],
                    "description": group["description"],
                    "backgrounds": backgrounds,
                    "bgm_tracks": bgm_tracks,
                    "_meta": group["_meta"],
                    "_fallback_priority": BACKGROUND_PRIORITY.get(first_background, 99),
                }
            )
        return minors

    def _scan_flat_backgrounds(self, backgrounds_root: Path, bgm_root: Path) -> list[dict[str, Any]]:
        backgrounds_meta = _load_meta(backgrounds_root / "meta.json")
        default_background_id = str(backgrounds_meta.get("default_background") or "")
        backgrounds = self._collect_backgrounds(backgrounds_root, default_id=default_background_id)
        if not backgrounds:
            return []

        bgm_tracks = self._collect_bgm_tracks_for_scene(
            bgm_root,
            major_names=["default"],
            minor_names=["default"],
            default_id=str(backgrounds_meta.get("default_bgm") or default_background_id or backgrounds[0]["id"]),
        )
        return [
            {
                "id": "default",
                "name": "默认场景组",
                "description": str(backgrounds_meta.get("description") or ""),
                **self._build_entry_extras(backgrounds_meta, container_path=backgrounds_root),
                "minors": [
                    {
                        "id": "default",
                        "name": "默认场景",
                        "description": str(backgrounds_meta.get("minor_description") or ""),
                        "backgrounds": backgrounds,
                        "bgm_tracks": bgm_tracks,
                        "_fallback_priority": BACKGROUND_PRIORITY.get(backgrounds[0]["id"], 99),
                    }
                ],
                "_fallback_priority": BACKGROUND_PRIORITY.get(backgrounds[0]["id"], 99),
            }
        ]

    def _scan_character_tree(self, characters_root: Path) -> list[dict[str, Any]]:
        if not characters_root.exists():
            return []

        outfits: list[dict[str, Any]] = []
        outfits.extend(self._collect_flat_character_outfits(characters_root))

        outfit_dirs = sorted(p for p in characters_root.iterdir() if p.is_dir() and not _is_ignored_dir(p))
        for outfit_dir in outfit_dirs:
            meta = _load_meta(outfit_dir / "meta.json")
            emotions = self._collect_emotions_from_dir(outfit_dir, meta=meta)
            if not emotions:
                continue

            outfit_id = str(meta.get("id") or outfit_dir.name)
            outfits.append(
                {
                    "id": outfit_id,
                    "name": str(meta.get("name") or _humanize(outfit_id)),
                    "description": str(meta.get("description") or ""),
                    **self._build_entry_extras(meta, container_path=outfit_dir),
                    "allowed_emotions": self._resolve_allowed_emotions(meta, emotions),
                    "emotions": emotions,
                    "_meta": meta,
                    "_fallback_priority": DEFAULT_OUTFIT_PRIORITY.get(outfit_id, 99),
                }
            )

        outfits = self._merge_outfits(outfits)
        return self._sort_entries(outfits)

    def _collect_backgrounds(self, root: Path, default_id: str = "") -> list[dict[str, Any]]:
        if not root.exists():
            return []
        backgrounds: list[dict[str, Any]] = []
        for path in sorted(root.iterdir()):
            if not _is_image(path):
                continue
            backgrounds.append(self._build_background_entry(path))
        return self._sort_entries(self._merge_resource_entries(backgrounds), default_id=default_id)

    def _collect_bgm_tracks_for_scene(
        self,
        bgm_root: Path,
        *,
        major_names: list[str],
        minor_names: list[str],
        default_id: str = "",
    ) -> list[dict[str, Any]]:
        for root in self._iter_bgm_candidate_roots(bgm_root, major_names, minor_names):
            tracks = self._collect_bgm_tracks(root, default_id=default_id)
            if tracks:
                return tracks
        return []

    def _iter_bgm_candidate_roots(
        self,
        bgm_root: Path,
        major_names: list[str],
        minor_names: list[str],
    ):
        seen: set[str] = set()
        ordered_pairs: list[tuple[str, str]] = []
        ordered_pairs.extend((major_name, minor_name) for major_name in major_names for minor_name in minor_names)
        ordered_pairs.extend((major_name, "default") for major_name in major_names)
        ordered_pairs.extend(("default", minor_name) for minor_name in minor_names)
        ordered_pairs.append(("default", "default"))

        for major_name, minor_name in ordered_pairs:
            if not major_name or not minor_name:
                continue
            root = bgm_root / major_name / minor_name
            root_key = str(root).lower()
            if root_key in seen:
                continue
            seen.add(root_key)
            yield root

    def _collect_bgm_tracks(self, root: Path, default_id: str = "") -> list[dict[str, Any]]:
        if not root.exists():
            return []
        tracks: list[dict[str, Any]] = []
        for path in sorted(root.iterdir()):
            if not _is_audio(path):
                continue
            tracks.append(self._build_bgm_entry(path))
        return self._sort_entries(self._merge_resource_entries(tracks), default_id=default_id)

    def _collect_emotions_from_dir(self, root: Path, *, meta: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        emotions: list[dict[str, Any]] = []
        for path in sorted(root.iterdir()):
            if not _is_image(path):
                continue
            emotion = self._build_emotion_entry(path)
            if not emotion:
                continue
            emotions.append(emotion)

        emotions = self._merge_resource_entries(emotions)
        default_id = str((meta or {}).get("default_emotion") or "")
        emotions = self._sort_entries(emotions, default_id=default_id)

        allowed = self._resolve_allowed_emotions(meta or {}, emotions)
        if not allowed:
            return emotions

        available = {emotion["id"]: emotion for emotion in emotions}
        filtered = [available[emotion_id] for emotion_id in allowed if emotion_id in available]
        return filtered or emotions

    def _collect_flat_character_outfits(self, root: Path) -> list[dict[str, Any]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for path in sorted(root.iterdir()):
            if not _is_image(path):
                continue

            stem = path.stem
            canon = _legacy_canon_emotion(stem)
            if canon:
                emotion = self._build_emotion_entry(path, emotion_id=canon)
                if emotion:
                    grouped.setdefault("default", []).append(emotion)
                continue

            for separator in ("_", "-"):
                if separator not in stem:
                    continue
                prefix, suffix = stem.rsplit(separator, 1)
                canon = _legacy_canon_emotion(suffix)
                if not canon:
                    continue
                emotion = self._build_emotion_entry(path, emotion_id=canon)
                if emotion:
                    grouped.setdefault(prefix, []).append(emotion)
                break

        outfits: list[dict[str, Any]] = []
        for outfit_id, emotions in grouped.items():
            meta = _load_meta(root / f"{outfit_id}.meta.json")
            deduped = self._sort_entries(
                self._merge_resource_entries(emotions),
                default_id=str(meta.get("default_emotion") or ""),
            )
            allowed = self._resolve_allowed_emotions(meta, deduped)
            if allowed:
                available = {emotion["id"]: emotion for emotion in deduped}
                deduped = [available[emotion_id] for emotion_id in allowed if emotion_id in available] or deduped
            if not deduped:
                continue
            resolved_outfit_id = str(meta.get("id") or outfit_id)
            outfits.append(
                {
                    "id": resolved_outfit_id,
                    "name": str(meta.get("name") or _humanize(resolved_outfit_id)),
                    "description": str(meta.get("description") or ""),
                    **self._build_entry_extras(meta, container_path=root),
                    "allowed_emotions": self._resolve_allowed_emotions(meta, deduped),
                    "emotions": deduped,
                    "_meta": meta,
                    "_fallback_priority": DEFAULT_OUTFIT_PRIORITY.get(resolved_outfit_id, 99),
                }
            )
        return outfits

    def _build_background_entry(
        self,
        path: Path,
        *,
        background_id: str | None = None,
        name_hint: str | None = None,
    ) -> dict[str, Any]:
        meta = _load_sidecar_meta(path)
        raw_id = str(meta.get("id") or background_id or path.stem)
        canonical_id = _canon_background(raw_id)
        return {
            "id": canonical_id,
            "name": str(meta.get("name") or name_hint or _humanize(raw_id if raw_id != canonical_id else canonical_id)),
            "description": str(meta.get("description") or ""),
            **self._build_entry_extras(meta, asset_path=path),
            "path": self._asset_public_path(path),
            "_meta": meta,
            "_fallback_priority": BACKGROUND_PRIORITY.get(canonical_id, 99),
        }

    def _build_bgm_entry(self, path: Path) -> dict[str, Any]:
        meta = _load_sidecar_meta(path)
        raw_id = str(meta.get("id") or path.stem)
        canonical_background = _canon_background(raw_id)
        track_id = canonical_background if _is_known_background_token(raw_id) else raw_id
        return {
            "id": track_id,
            "name": str(meta.get("name") or _humanize(raw_id)),
            "description": str(meta.get("description") or ""),
            **self._build_entry_extras(meta, asset_path=path),
            "path": self._asset_public_path(path),
            "_meta": meta,
            "_fallback_priority": BACKGROUND_PRIORITY.get(track_id, 99),
        }

    def _build_emotion_entry(self, path: Path, *, emotion_id: str | None = None) -> dict[str, Any] | None:
        meta = _load_sidecar_meta(path)
        canonical_id = _normalize_emotion_id(str(meta.get("id") or emotion_id or path.stem))
        if not canonical_id:
            return None
        return {
            "id": canonical_id,
            "name": str(meta.get("name") or EMOTION_LABELS.get(canonical_id, _humanize(canonical_id))),
            "description": str(meta.get("description") or ""),
            **self._build_entry_extras(meta, asset_path=path),
            "path": self._asset_public_path(path),
            "_meta": meta,
            "_fallback_priority": EMOTION_PRIORITY.get(canonical_id, 99),
        }

    def _resolve_allowed_emotions(self, meta: dict[str, Any], emotions: list[dict[str, Any]]) -> list[str]:
        configured = meta.get("allowed_emotions") if isinstance(meta, dict) else None
        if isinstance(configured, list):
            resolved: list[str] = []
            for item in configured:
                match = self._find_entry_in_list(emotions, str(item))
                if match and match["id"] not in resolved:
                    resolved.append(match["id"])
            if resolved:
                default_match = self._find_entry_in_list(emotions, str(meta.get("default_emotion") or ""))
                if default_match and default_match["id"] in resolved:
                    resolved.remove(default_match["id"])
                    resolved.insert(0, default_match["id"])
                return resolved
        return [emotion["id"] for emotion in emotions]

    def _build_entry_extras(
        self,
        meta: dict[str, Any],
        *,
        container_path: Path | None = None,
        asset_path: Path | None = None,
    ) -> dict[str, Any]:
        aliases = _coerce_string_list(meta.get("aliases"))
        tags = _coerce_string_list(meta.get("tags"))
        ai_hint_parts = [str(meta.get(key) or "").strip() for key in META_NOTE_KEYS]

        if container_path is not None:
            ai_hint_parts.append(self._load_directory_note(container_path))
        if asset_path is not None:
            ai_hint_parts.append(self._load_sidecar_note(asset_path))

        ai_hint = _join_text_parts(ai_hint_parts)

        payload: dict[str, Any] = {}
        if aliases:
            payload["aliases"] = aliases
        if tags:
            payload["tags"] = tags
        if ai_hint:
            payload["ai_hint"] = ai_hint
        return payload

    def _collect_global_notes(self) -> list[str]:
        notes: list[str] = []
        for root in (
            self.assets_dir,
            self.assets_dir / "scenes",
            self.assets_dir / "characters",
            self.assets_dir / "bgm",
        ):
            note = self._load_directory_note(root)
            if note:
                notes.append(note)
        return notes

    def _load_directory_note(self, root: Path) -> str:
        if not root.exists() or not root.is_dir():
            return ""
        parts = [_load_text(root / filename) for filename in NOTE_FILENAMES]
        return _join_text_parts(parts)

    def _load_sidecar_note(self, path: Path) -> str:
        parts = [_load_text(path.with_suffix(suffix)) for suffix in SIDECAR_NOTE_SUFFIXES]
        return _join_text_parts(parts)

    def _format_resource_label(self, entry: dict[str, Any]) -> str:
        name = str(entry.get("name") or entry.get("id") or "").strip()
        entry_id = str(entry.get("id") or "").strip()
        alias_text = ", ".join(str(alias).strip() for alias in entry.get("aliases") or [] if str(alias).strip())
        detail_parts: list[str] = []
        if entry_id and name and entry_id != name:
            detail_parts.append(f"id:{entry_id}")
        if alias_text:
            detail_parts.append(f"别名:{alias_text}")
        note = _join_text_parts([str(entry.get("description") or ""), str(entry.get("ai_hint") or "")])
        if note:
            detail_parts.append(note)
        if detail_parts:
            return f"{name} ({'; '.join(detail_parts)})"
        return name or entry_id

    def _merge_manifest_with_runtime_projection(
        self,
        manifest: dict[str, Any],
        *,
        extra_bgm_tracks: list[dict[str, Any]],
        extra_scene_groups: list[dict[str, Any]],
        extra_character_outfits: list[dict[str, Any]],
    ) -> dict[str, Any]:
        runtime_manifest = json.loads(json.dumps(manifest))
        groups = [
            dict(group)
            for group in extra_scene_groups
            if isinstance(group, dict) and str(group.get("id") or "").strip()
        ]
        if groups:
            runtime_manifest["scenes"]["majors"] = self._sort_entries(
                self._merge_majors(list(runtime_manifest["scenes"]["majors"]) + groups)
            )

        outfits = [
            dict(outfit)
            for outfit in extra_character_outfits
            if isinstance(outfit, dict) and str(outfit.get("id") or "").strip()
        ]
        if outfits:
            runtime_manifest["characters"]["outfits"] = self._sort_entries(
                self._merge_outfits(list(runtime_manifest["characters"]["outfits"]) + outfits)
            )

        tracks = [
            dict(track)
            for track in extra_bgm_tracks
            if isinstance(track, dict) and str(track.get("id") or "").strip()
        ]
        if not tracks:
            return runtime_manifest

        for major in runtime_manifest["scenes"]["majors"]:
            for minor in major["minors"]:
                existing_tracks = list(minor.get("bgm_tracks") or [])
                existing_ids = {
                    str(item.get("id") or "").strip()
                    for item in existing_tracks
                    if isinstance(item, dict)
                }
                appended = [
                    track
                    for track in tracks
                    if str(track.get("id") or "").strip() not in existing_ids
                ]
                minor["bgm_tracks"] = existing_tracks + appended
        return runtime_manifest

    def _format_scene_label(self, major: dict[str, Any], minor: dict[str, Any]) -> str:
        return f"{self._format_resource_label(major)} / {self._format_resource_label(minor)}"

    def _entry_lookup_values(self, entry: dict[str, Any]) -> list[str]:
        values: list[str] = []
        for key in ("id", "name"):
            value = str(entry.get(key) or "").strip()
            if value and value not in values:
                values.append(value)
        for alias in entry.get("aliases") or []:
            value = str(alias or "").strip()
            if value and value not in values:
                values.append(value)
        return values

    def _entry_matches(self, entry: dict[str, Any], value: Any) -> bool:
        raw = str(value or "").strip()
        if not raw:
            return False
        normalized = _normalize_key(raw)
        for option in self._entry_lookup_values(entry):
            if raw == option or normalized == _normalize_key(option):
                return True
        return False

    def _find_entry_in_list(self, entries: list[dict[str, Any]], value: Any) -> dict[str, Any] | None:
        for entry in entries:
            if self._entry_matches(entry, value):
                return entry
        return None

    def _merge_resource_entries(self, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        for entry in entries:
            entry_id = entry["id"]
            if entry_id not in merged:
                merged[entry_id] = entry
                order.append(entry_id)
                continue
            merged[entry_id] = self._merge_entry_dicts(merged[entry_id], entry)
        return [merged[entry_id] for entry_id in order]

    def _merge_majors(self, majors: list[dict[str, Any]]) -> list[dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        for major in majors:
            major_id = major["id"]
            if major_id not in merged:
                merged[major_id] = major
                order.append(major_id)
                continue
            current = merged[major_id]
            current["minors"] = self._merge_minors(current["minors"] + major["minors"])
            merged[major_id] = self._merge_entry_dicts(current, major)
        return [merged[major_id] for major_id in order]

    def _merge_minors(self, minors: list[dict[str, Any]]) -> list[dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        for minor in minors:
            minor_id = minor["id"]
            if minor_id not in merged:
                merged[minor_id] = minor
                order.append(minor_id)
                continue
            current = merged[minor_id]
            current["backgrounds"] = self._sort_entries(
                self._merge_resource_entries(current["backgrounds"] + minor["backgrounds"]),
                default_id=str(current.get("_meta", {}).get("default_background") or minor.get("_meta", {}).get("default_background") or ""),
            )
            current["bgm_tracks"] = self._sort_entries(
                self._merge_resource_entries(current["bgm_tracks"] + minor["bgm_tracks"]),
                default_id=str(current.get("_meta", {}).get("default_bgm") or minor.get("_meta", {}).get("default_bgm") or ""),
            )
            first_background = current["backgrounds"][0]["id"] if current["backgrounds"] else ""
            current["_fallback_priority"] = BACKGROUND_PRIORITY.get(first_background, 99)
            merged[minor_id] = self._merge_entry_dicts(current, minor)
        return [merged[minor_id] for minor_id in order]

    def _merge_outfits(self, outfits: list[dict[str, Any]]) -> list[dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        for outfit in outfits:
            outfit_id = outfit["id"]
            if outfit_id not in merged:
                merged[outfit_id] = outfit
                order.append(outfit_id)
                continue
            current = merged[outfit_id]
            current["emotions"] = self._sort_entries(
                self._merge_resource_entries(current["emotions"] + outfit["emotions"]),
                default_id=str(current.get("_meta", {}).get("default_emotion") or outfit.get("_meta", {}).get("default_emotion") or ""),
            )
            current["allowed_emotions"] = self._resolve_allowed_emotions(
                current.get("_meta", {}) or outfit.get("_meta", {}),
                current["emotions"],
            )
            merged[outfit_id] = self._merge_entry_dicts(current, outfit)
        return [merged[outfit_id] for outfit_id in order]

    def _merge_entry_dicts(self, current: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
        merged = dict(current)
        merged_meta = {**current.get("_meta", {}), **incoming.get("_meta", {})}
        merged["_meta"] = merged_meta
        merged["_fallback_priority"] = min(current.get("_fallback_priority", 99), incoming.get("_fallback_priority", 99))

        for field in ("name", "description", "path", "ai_hint"):
            if not merged.get(field) and incoming.get(field):
                merged[field] = incoming[field]
        if current.get("aliases") or incoming.get("aliases"):
            merged["aliases"] = list(dict.fromkeys([*list(current.get("aliases") or []), *list(incoming.get("aliases") or [])]))
        if current.get("tags") or incoming.get("tags"):
            merged["tags"] = list(dict.fromkeys([*list(current.get("tags") or []), *list(incoming.get("tags") or [])]))
        for field in ("backgrounds", "bgm_tracks", "emotions", "minors", "allowed_emotions"):
            if field in incoming and field not in merged:
                merged[field] = incoming[field]
        return merged

    def _sort_entries(self, entries: list[dict[str, Any]], default_id: str = "") -> list[dict[str, Any]]:
        return sorted(entries, key=lambda item: self._entry_sort_key(item, default_id=default_id))

    def _entry_sort_key(self, item: dict[str, Any], *, default_id: str = "") -> tuple[int, int, str]:
        meta = item.get("_meta", {})
        is_default = _coerce_flag(meta.get("default")) or bool(default_id and item["id"] == default_id)
        priority = _coerce_priority(meta.get("priority"))
        if priority is None:
            priority = int(item.get("_fallback_priority", 99))
        return (0 if is_default else 1, priority, item["id"])

    def _path_names(self, raw_name: str, meta_id: Any) -> list[str]:
        names: list[str] = []
        for value in (raw_name, str(meta_id or "").strip()):
            if not value or "/" in value or "\\" in value:
                continue
            if value not in names:
                names.append(value)
        return names or [raw_name]

    def _split_legacy_scene_stem(self, stem: str, *, major_name: str = "") -> tuple[str, str]:
        for separator in ("_", "-", " "):
            if separator not in stem:
                continue
            prefix, suffix = stem.rsplit(separator, 1)
            if prefix and _is_known_background_token(suffix):
                return prefix, suffix
            if suffix and _is_known_background_token(prefix):
                minor = suffix if suffix != major_name else "default"
                return minor, prefix
        return ("default", stem)

    def _strip_internal_fields(self, value: Any) -> Any:
        if isinstance(value, list):
            return [self._strip_internal_fields(item) for item in value]
        if isinstance(value, dict):
            return {
                key: self._strip_internal_fields(item)
                for key, item in value.items()
                if not key.startswith("_")
            }
        return value

    def _default_outfit(self) -> dict[str, Any]:
        return {
            "id": "default",
            "name": "默认服装",
            "description": "",
            "allowed_emotions": ["normal"],
            "emotions": [
                {
                    "id": "normal",
                    "name": "平静",
                    "description": "",
                    "path": "",
                }
            ],
            "_fallback_priority": DEFAULT_OUTFIT_PRIORITY["default"],
        }

    def _default_scene(self) -> dict[str, Any]:
        return {
            "id": "default",
            "name": "默认场景",
            "description": "",
            "minors": [
                {
                    "id": "default",
                    "name": "默认",
                    "description": "",
                    "backgrounds": [
                        {
                            "id": "default",
                            "name": "默认背景",
                            "description": "",
                            "path": "",
                        }
                    ],
                    "bgm_tracks": [],
                    "_fallback_priority": 99,
                }
            ],
            "_fallback_priority": 99,
        }

    @staticmethod
    def _normalize_public_prefix(value: str) -> str:
        prefix = str(value or "").strip().replace("\\", "/")
        if not prefix:
            return ""
        return f"/{prefix.strip('/')}"

    def _asset_public_path(self, path: Path) -> str:
        relative = path.relative_to(self.assets_dir).as_posix()
        if not self.public_prefix:
            return relative
        return f"{self.public_prefix}/{relative}"

    def _find_major(self, manifest: dict[str, Any], major_id: str) -> dict[str, Any] | None:
        return self._find_entry_in_list(manifest["scenes"]["majors"], major_id)

    def _find_minor(self, major: dict[str, Any], minor_id: str) -> dict[str, Any] | None:
        return self._find_entry_in_list(major["minors"], minor_id)

    def _find_background(self, minor: dict[str, Any], background_id: str | None) -> dict[str, Any] | None:
        if not background_id:
            return None
        match = self._find_entry_in_list(minor["backgrounds"], background_id)
        if match is not None:
            return match
        normalized = self._normalize_background_id(background_id)
        match = self._find_entry_in_list(minor["backgrounds"], normalized)
        if match is not None:
            return match

        lookup = _normalize_key(background_id)
        minor_label = str(minor.get("name") or minor.get("id") or "").strip()
        for background in minor.get("backgrounds") or []:
            background_label = str(background.get("name") or background.get("id") or "").strip()
            values = [
                f"{background_label}{minor_label}",
                f"{minor_label}{background_label}",
            ]
            if any(lookup == _normalize_key(value) for value in values if value):
                return background
        return None

    def _find_background_across_minors(
        self,
        major: dict[str, Any] | None,
        background_id: Any,
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        if not major or not background_id:
            return None
        raw = str(background_id or "").strip()
        normalized = self._normalize_background_id(raw)
        for minor in major.get("minors") or []:
            for candidate in (raw, normalized):
                match = self._find_background(minor, candidate)
                if match is not None:
                    return minor, match

        lookup = _normalize_key(raw)
        if not lookup:
            return None
        for minor in major.get("minors") or []:
            for background in minor.get("backgrounds") or []:
                background_label = str(background.get("name") or background.get("id") or "").strip()
                minor_label = str(minor.get("name") or minor.get("id") or "").strip()
                values = [
                    str(background.get("id") or ""),
                    background_label,
                    f"{background_label}{minor_label}",
                    f"{minor_label}{background_label}",
                ]
                values.extend(str(alias or "") for alias in background.get("aliases") or [])
                if any(lookup == _normalize_key(value) for value in values if value):
                    return minor, background
        return None

    def _find_bgm(self, minor: dict[str, Any], bgm_id: str | None) -> dict[str, Any] | None:
        if not bgm_id:
            return None
        match = self._find_entry_in_list(minor["bgm_tracks"], bgm_id)
        if match is not None:
            return match
        normalized = _canon_background(bgm_id)
        return self._find_entry_in_list(minor["bgm_tracks"], normalized or bgm_id)

    def _find_outfit(self, manifest: dict[str, Any], outfit_id: str) -> dict[str, Any] | None:
        return self._find_entry_in_list(manifest["characters"]["outfits"], outfit_id)

    def _find_emotion(self, outfit: dict[str, Any], emotion_id: str) -> dict[str, Any] | None:
        return self._find_entry_in_list(outfit["emotions"], emotion_id)

    def _find_emotion_with_aliases(self, outfit: dict[str, Any], emotion_id: str) -> dict[str, Any] | None:
        if not outfit:
            return None
        for candidate in _emotion_candidate_ids(
            emotion_id,
            emotion_aliases=self.emotion_aliases,
        ):
            match = self._find_emotion(outfit, candidate)
            if match is not None:
                return match
        return None

    def _normalize_background_id(self, value: Any) -> str:
        background_id = str(value or "").strip()
        if not background_id:
            return ""

        alias_map = {
            "morning_classroom": "morning",
            "evening_classroom": "evening",
            "night_room": "night",
        }
        background_id = alias_map.get(background_id, background_id)
        canonical_id = _canon_background(background_id)
        if canonical_id != background_id or _is_known_background_token(background_id):
            return canonical_id

        for separator in ("_", "-", " "):
            if separator not in background_id:
                continue
            _, suffix = background_id.rsplit(separator, 1)
            if _is_known_background_token(suffix):
                return _canon_background(suffix)
        return background_id
