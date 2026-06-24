from __future__ import annotations

import hashlib
import logging
import re
import shutil
import threading
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

from .store import MemoryStore, normalize_character_pack_id
from .text_utils import infer_time_of_day, normalize_text


logger = logging.getLogger("akane.memory_timeline")

TIME_PERIOD_LABELS = {
    "morning": "上午",
    "afternoon": "下午",
    "night": "夜晚",
    "midnight": "凌晨",
}
TIME_PERIOD_ORDER = ("midnight", "morning", "afternoon", "night")
MOOD_LABELS = {
    "calm": "平静",
    "warm": "温暖",
    "affectionate": "亲近",
    "happy": "开心",
    "playful": "俏皮",
    "curious": "好奇",
    "thoughtful": "认真",
    "touched": "触动",
    "proud": "欣慰",
    "worried": "担心",
    "lonely": "孤单",
    "sad": "难过",
    "embarrassed": "害羞",
    "tense": "紧张",
    "annoyed": "不悦",
    "determined": "坚定",
}
SAFE_PATH_SEGMENT = re.compile(r"^[A-Za-z0-9_.-]+$")


class MemoryTimelineService:
    """Exact raw-dialogue timeline plus a local, rebuildable Markdown mirror."""

    def __init__(
        self,
        *,
        store: MemoryStore,
        root_dir: Path,
        characters_dir: Path | None = None,
        background_tasks: Any = None,
    ) -> None:
        self.store = store
        self.root_dir = Path(root_dir)
        self.characters_dir = Path(characters_dir) if characters_dir is not None else None
        self.background_tasks = background_tasks
        self._write_lock = threading.RLock()

    def handle_message_write(self, message: dict[str, Any]) -> None:
        profile_user_id = str(message.get("profile_user_id") or "").strip()
        date_label = str(message.get("date_label") or "").strip()
        if not profile_user_id or not self._parse_date(date_label):
            return
        self._submit(
            name=f"memory-timeline-{date_label}",
            fn=self.render_day_file,
            kwargs={
                "profile_user_id": profile_user_id,
                "character_pack_id": str(message.get("character_pack_id") or ""),
                "date_label": date_label,
            },
        )

    def schedule_existing_backfill(self) -> None:
        days = self.store.list_memory_timeline_days()
        missing_days = [
            item
            for item in days
            if not self.day_file_path(
                profile_user_id=item["profile_user_id"],
                character_pack_id=item["character_pack_id"],
                date_label=item["date_label"],
            ).is_file()
        ]
        if not missing_days:
            return
        self._submit(
            name="memory-timeline-backfill",
            fn=self.backfill_existing,
            kwargs={"days": missing_days},
        )

    def backfill_existing(self, *, days: Iterable[dict[str, str]] | None = None) -> dict[str, Any]:
        candidates = list(days) if days is not None else self.store.list_memory_timeline_days()
        written = 0
        failed = 0
        for item in candidates:
            try:
                path = self.render_day_file(
                    profile_user_id=str(item.get("profile_user_id") or ""),
                    character_pack_id=str(item.get("character_pack_id") or ""),
                    date_label=str(item.get("date_label") or ""),
                )
                if path is not None:
                    written += 1
            except Exception as exc:
                failed += 1
                logger.warning("memory timeline backfill failed for one day: %s", exc)
        return {
            "status": "ok" if failed == 0 else "partial",
            "written_days": written,
            "failed_days": failed,
        }

    def read(
        self,
        *,
        profile_user_id: str,
        character_pack_id: str = "",
        date_from: str,
        date_to: str,
        time_periods: Iterable[str] | None = None,
        exclude_source_ids: Iterable[str] | None = None,
    ) -> dict[str, Any]:
        start = self._parse_date(date_from)
        end = self._parse_date(date_to)
        if start is None or end is None:
            return {
                "status": "invalid_range",
                "reason": "date_must_use_yyyy_mm_dd",
                "date_from": str(date_from or ""),
                "date_to": str(date_to or ""),
                "time_periods": [],
                "messages": [],
            }
        if start > end:
            return {
                "status": "invalid_range",
                "reason": "date_from_after_date_to",
                "date_from": start.isoformat(),
                "date_to": end.isoformat(),
                "time_periods": [],
                "messages": [],
            }

        periods = self.normalize_time_periods(time_periods)
        messages = self.store.get_memory_timeline_messages(
            profile_user_id=profile_user_id,
            character_pack_id=character_pack_id,
            date_from=start.isoformat(),
            date_to=end.isoformat(),
        )
        excluded = {
            str(item or "").strip()
            for item in exclude_source_ids or []
            if str(item or "").strip()
        }
        if excluded:
            messages = [
                item
                for item in messages
                if str(item.get("source_id") or "").strip() not in excluded
            ]
        normalized_messages = [self._project_message(item) for item in messages]
        if periods:
            allowed = set(periods)
            normalized_messages = [
                item for item in normalized_messages if item["actual_time_period"] in allowed
            ]

        active_dates = sorted({str(item["date_label"]) for item in normalized_messages})
        return {
            "status": "ok" if normalized_messages else "empty",
            "reason": "" if normalized_messages else "no_activity",
            "date_from": start.isoformat(),
            "date_to": end.isoformat(),
            "time_periods": periods,
            "active_dates": active_dates,
            "message_count": len(normalized_messages),
            "messages": normalized_messages,
        }

    def build_acquaintance_prompt(
        self,
        *,
        profile_user_id: str,
        character_pack_id: str = "",
        now_ts: int,
    ) -> str:
        stats = self.store.get_memory_timeline_stats(
            profile_user_id=profile_user_id,
            character_pack_id=character_pack_id,
        )
        first_timestamp = int(stats.get("first_timestamp") or 0)
        if first_timestamp <= 0:
            return ""
        first_date = datetime.fromtimestamp(first_timestamp).date()
        current_date = datetime.fromtimestamp(int(now_ts)).date()
        known_days = max(1, (current_date - first_date).days + 1)
        active_days = max(1, int(stats.get("active_day_count") or 0))
        return (
            "【你们共同走过的时间】\n"
            f"按本地留下的聊天记录算，你和用户第一次聊天是在 {first_date.isoformat()}；"
            f"到今天是认识的第 {known_days} 天，其中有 {active_days} 天留下过对话。\n"
            "这是一条时间感线索，不必每轮报数。只有谈到初识、陪伴、纪念日、许久未聊或时间流逝时，"
            "再自然地把这段共同时间放进语气里；不要把“首次留下记录”夸张成你能证明的现实起点。"
        )

    def render_tool_context(self, result: dict[str, Any]) -> str:
        status = str(result.get("status") or "")
        date_from = str(result.get("date_from") or "")
        date_to = str(result.get("date_to") or "")
        periods = list(result.get("time_periods") or [])
        range_label = date_from if date_from == date_to else f"{date_from} 至 {date_to}"
        period_label = "、".join(TIME_PERIOD_LABELS.get(item, item) for item in periods) or "全天"
        if status == "invalid_range":
            return (
                "原始对话时间线读取失败：日期范围无效。"
                "日期必须使用 YYYY-MM-DD，且 date_from 不能晚于 date_to。"
            )
        if status == "empty":
            return f"原始对话时间线：{range_label}（{period_label}）没有留下对话记录。"

        lines = [
            f"【原始对话时间线：{range_label}（{period_label}）】",
            "下面是按数据库时间精确读取的原始对话，不是摘要或长期语义记忆。",
            "“内容检索线索”只用于以后定位这段对话，不表示用户或角色主观上特别在意。",
        ]
        current_date = ""
        for item in list(result.get("messages") or []):
            item_date = str(item.get("date_label") or "")
            if item_date != current_date:
                lines.append(f"[日期 {item_date}]")
                current_date = item_date
            time_label = datetime.fromtimestamp(int(item["timestamp"])).strftime("%H:%M")
            period = TIME_PERIOD_LABELS.get(str(item.get("actual_time_period") or ""), "")
            content = self._raw_content(item.get("content"))
            prefix = f"[{time_label} | {period}] {self._speaker_label(str(item.get('role') or ''))}:"
            if "\n" in content:
                lines.append(prefix)
                lines.extend(f"  {line}" for line in content.splitlines())
            else:
                lines.append(f"{prefix} {content}")
            detail = self._render_metadata_detail(item.get("memory_metadata"))
            if detail:
                lines.append(f"  {detail}")
        lines.append("请只根据这些已加载的原始记录回答；缺失的细节不要凭印象补写。")
        return "\n".join(lines)

    def render_day_file(
        self,
        *,
        profile_user_id: str,
        character_pack_id: str = "",
        date_label: str,
    ) -> Path | None:
        result = self.read(
            profile_user_id=profile_user_id,
            character_pack_id=character_pack_id,
            date_from=date_label,
            date_to=date_label,
        )
        if result["status"] not in {"ok", "empty"}:
            return None
        path = self.day_file_path(
            profile_user_id=profile_user_id,
            character_pack_id=character_pack_id,
            date_label=date_label,
        )
        if result["status"] == "empty":
            if path.is_file():
                path.unlink()
            return None

        content = self._render_day_markdown(result)
        with self._write_lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._ensure_local_readme(character_pack_id)
            temp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
            try:
                temp_path.write_text(content, encoding="utf-8", newline="\n")
                temp_path.replace(path)
            finally:
                if temp_path.exists():
                    temp_path.unlink()
            self._remove_legacy_day_file(
                profile_user_id=profile_user_id,
                character_pack_id=character_pack_id,
                date_label=date_label,
                current_path=path,
            )
        return path

    def day_file_path(
        self,
        *,
        profile_user_id: str,
        character_pack_id: str = "",
        date_label: str,
    ) -> Path:
        parsed = self._parse_date(date_label)
        if parsed is None:
            raise ValueError("date_label must use YYYY-MM-DD")
        profile_segment = self._safe_segment(profile_user_id, fallback="default_user")
        pack_dir = self._character_pack_dir(character_pack_id)
        if pack_dir is not None:
            return (
                pack_dir
                / "_local"
                / "memory"
                / "profiles"
                / profile_segment
                / "days"
                / f"{parsed.year:04d}"
                / f"{parsed.month:02d}"
                / f"{parsed.isoformat()}.md"
            )
        return self._legacy_day_file_path(
            profile_user_id=profile_user_id,
            character_pack_id=character_pack_id,
            date_label=date_label,
        )

    def _legacy_day_file_path(
        self,
        *,
        profile_user_id: str,
        character_pack_id: str = "",
        date_label: str,
    ) -> Path:
        parsed = self._parse_date(date_label)
        if parsed is None:
            raise ValueError("date_label must use YYYY-MM-DD")
        profile_segment = self._safe_segment(profile_user_id, fallback="default_user")
        character_segment = self._safe_segment(
            normalize_character_pack_id(character_pack_id) or "_default",
            fallback="_default",
        )
        return (
            self.root_dir
            / "profiles"
            / profile_segment
            / "characters"
            / character_segment
            / "days"
            / f"{parsed.year:04d}"
            / f"{parsed.month:02d}"
            / f"{parsed.isoformat()}.md"
        )

    def clear_mirror(self) -> None:
        root = self.root_dir.resolve()
        if root.exists() and root.name == "memory":
            shutil.rmtree(root)
        characters_dir = self.characters_dir
        if characters_dir is None or not characters_dir.is_dir():
            return
        for pack_dir in characters_dir.iterdir():
            if not pack_dir.is_dir():
                continue
            memory_dir = pack_dir / "_local" / "memory"
            if memory_dir.is_dir():
                shutil.rmtree(memory_dir)

    @staticmethod
    def normalize_time_periods(values: Iterable[str] | None) -> list[str]:
        aliases = {
            "morning": "morning",
            "上午": "morning",
            "早上": "morning",
            "afternoon": "afternoon",
            "下午": "afternoon",
            "night": "night",
            "evening": "night",
            "晚上": "night",
            "夜晚": "night",
            "midnight": "midnight",
            "凌晨": "midnight",
            "半夜": "midnight",
        }
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values or []:
            period = aliases.get(str(value or "").strip().lower())
            if not period or period in seen:
                continue
            seen.add(period)
            normalized.append(period)
        return [period for period in TIME_PERIOD_ORDER if period in normalized]

    def _render_day_markdown(self, result: dict[str, Any]) -> str:
        date_label = str(result.get("date_from") or "")
        lines = [
            f"# {date_label} 原始对话",
            "",
            "> 由本地 SQLite 记忆自动生成的只读镜像。数据库仍是真实来源；文件可删除并重新生成。",
            "> “内容检索线索”只用于以后定位这段对话，不表示用户或角色主观上特别在意。",
            "",
        ]
        messages = list(result.get("messages") or [])
        for period in TIME_PERIOD_ORDER:
            period_messages = [
                item for item in messages if item.get("actual_time_period") == period
            ]
            if not period_messages:
                continue
            lines.extend([f"## {TIME_PERIOD_LABELS[period]}", ""])
            for item in period_messages:
                time_label = datetime.fromtimestamp(int(item["timestamp"])).strftime("%H:%M:%S")
                lines.extend(
                    [
                        f"### {time_label} {self._speaker_label(str(item.get('role') or ''))}",
                        "",
                        self._markdown_quote(item.get("content")),
                    ]
                )
                detail = self._render_metadata_detail(item.get("memory_metadata"))
                if detail:
                    lines.extend(["", f"- {detail}"])
                lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    def _project_message(self, message: dict[str, Any]) -> dict[str, Any]:
        timestamp = int(message.get("timestamp") or 0)
        return {
            "role": str(message.get("role") or ""),
            "content": str(message.get("content") or ""),
            "timestamp": timestamp,
            "date_label": str(message.get("date_label") or ""),
            "actual_time_period": infer_time_of_day(timestamp),
            "memory_metadata": dict(message.get("memory_metadata") or {}),
        }

    def _render_metadata_detail(self, value: Any) -> str:
        metadata = value if isinstance(value, dict) else {}
        parts: list[str] = []
        emotion = str(metadata.get("response_emotion") or "").strip()
        if emotion:
            parts.append(f"角色当时的表情/情绪：{emotion}")
        outfit = str(metadata.get("response_outfit") or "").strip()
        if outfit:
            parts.append(f"角色当时的服装：{outfit}")
        moods = [
            MOOD_LABELS.get(str(item or "").strip(), str(item or "").strip())
            for item in list(metadata.get("mood_tags") or [])
            if str(item or "").strip()
        ]
        if moods:
            parts.append(f"记忆余温：{'、'.join(moods)}")
        keywords = [
            normalize_text(item)
            for item in list(metadata.get("keywords") or [])
            if normalize_text(item)
        ]
        if keywords:
            parts.append(f"内容检索线索：{'、'.join(keywords)}")
        return "；".join(parts)

    @classmethod
    def _markdown_quote(cls, value: Any) -> str:
        content = cls._raw_content(value)
        return "\n".join(f"> {line}" if line else ">" for line in content.splitlines())

    @staticmethod
    def _raw_content(value: Any) -> str:
        return str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()

    def _submit(self, *, name: str, fn: Any, kwargs: dict[str, Any]) -> None:
        if self.background_tasks is None:
            try:
                fn(**kwargs)
            except Exception as exc:
                logger.warning("memory timeline task failed: %s", exc)
            return
        try:
            self.background_tasks.submit(
                lane="timeline",
                name=name,
                fn=fn,
                kwargs=kwargs,
            )
        except Exception as exc:
            logger.warning("memory timeline scheduling failed: %s", exc)

    def _character_pack_dir(self, character_pack_id: Any) -> Path | None:
        pack_id = normalize_character_pack_id(character_pack_id)
        characters_dir = self.characters_dir
        if not pack_id or characters_dir is None or not characters_dir.is_dir():
            return None
        base = characters_dir.resolve()
        candidate = (base / pack_id).resolve()
        if candidate.parent != base or not candidate.is_dir():
            return None
        return candidate

    def _ensure_local_readme(self, character_pack_id: Any) -> None:
        pack_dir = self._character_pack_dir(character_pack_id)
        if pack_dir is None:
            return
        local_dir = pack_dir / "_local"
        readme_path = local_dir / "README.md"
        if readme_path.exists():
            return
        local_dir.mkdir(parents=True, exist_ok=True)
        readme_path.write_text(
            "# 本机角色数据\n\n"
            "这里存放该角色在本机运行时产生的私密数据，例如按日期生成的聊天记忆镜像。\n\n"
            "- 不属于可分享的角色设定。\n"
            "- 导出角色包时不会包含此目录。\n"
            "- 覆盖更新角色包时会保留此目录。\n"
            "- SQLite 数据库仍是记忆的真实来源，Markdown 文件可以重新生成。\n",
            encoding="utf-8",
            newline="\n",
        )

    def _remove_legacy_day_file(
        self,
        *,
        profile_user_id: str,
        character_pack_id: str,
        date_label: str,
        current_path: Path,
    ) -> None:
        legacy_path = self._legacy_day_file_path(
            profile_user_id=profile_user_id,
            character_pack_id=character_pack_id,
            date_label=date_label,
        )
        if legacy_path == current_path or not legacy_path.is_file():
            return
        legacy_path.unlink()
        self._prune_empty_parents(legacy_path.parent, stop_at=self.root_dir)

    @staticmethod
    def _prune_empty_parents(start: Path, *, stop_at: Path) -> None:
        current = start
        stop = stop_at.resolve()
        while current.exists():
            resolved = current.resolve()
            if resolved == stop or stop not in resolved.parents:
                return
            try:
                current.rmdir()
            except OSError:
                return
            current = current.parent

    @staticmethod
    def _parse_date(value: Any) -> date | None:
        try:
            return date.fromisoformat(str(value or "").strip())
        except ValueError:
            return None

    @staticmethod
    def _safe_segment(value: Any, *, fallback: str) -> str:
        text = str(value or "").strip()
        if not text:
            return fallback
        if SAFE_PATH_SEGMENT.fullmatch(text) and text not in {".", ".."}:
            return text[:96]
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
        readable = re.sub(r"[^A-Za-z0-9_.-]+", "_", text).strip("._-")[:48]
        return f"{readable or fallback}_{digest}"

    @staticmethod
    def _speaker_label(role: str) -> str:
        normalized = str(role or "").strip()
        lowered = normalized.lower()
        if lowered == "user":
            return "用户"
        if lowered == "assistant":
            return "角色"
        if lowered.startswith("npc:"):
            name = normalized.split(":", 1)[1].strip()
            return name or "NPC"
        if lowered == "system":
            return "系统"
        if lowered == "tool":
            return "工具"
        return normalized or "未知说话人"
