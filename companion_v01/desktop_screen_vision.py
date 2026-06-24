from __future__ import annotations

import threading
import time
import uuid
from collections import defaultdict
from typing import Any


class DesktopScreenVisionWorkspace:
    def __init__(
        self,
        *,
        vision_service: Any,
        max_ready_per_session: int = 5,
        ttl_sec: int = 15 * 60,
    ) -> None:
        self.vision_service = vision_service
        self.max_ready_per_session = max(1, int(max_ready_per_session or 5))
        self.ttl_sec = max(60, int(ttl_sec or 900))
        self._lock = threading.RLock()
        self._records: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        self._jobs_in_flight: set[str] = set()

    def reset(self) -> None:
        with self._lock:
            self._records.clear()
            self._jobs_in_flight.clear()

    def submit_clip(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        frames: list[dict[str, Any]],
        foreground: dict[str, Any] | None = None,
        captured_start_ts: int | None = None,
        captured_end_ts: int | None = None,
        mode: str = "",
    ) -> dict[str, Any]:
        normalized_frames = self._normalize_frames(frames)
        if not normalized_frames:
            raise ValueError("screen vision clip requires at least one frame")

        now_ts = int(time.time())
        key = self._key(profile_user_id, session_id)
        clip_id = f"screen_{now_ts}_{uuid.uuid4().hex[:10]}"
        record = {
            "clip_id": clip_id,
            "status": "pending",
            "profile_user_id": str(profile_user_id or ""),
            "session_id": str(session_id or ""),
            "captured_start_ts": int(captured_start_ts or normalized_frames[0].get("captured_at") or now_ts),
            "captured_end_ts": int(captured_end_ts or normalized_frames[-1].get("captured_at") or now_ts),
            "observed_at": 0,
            "updated_at": now_ts,
            "ttl_sec": self.ttl_sec,
            "frame_count": len(normalized_frames),
            "foreground": self._normalize_foreground(foreground or {}),
            "mode": str(mode or "").strip()[:40],
            "observation": {},
            "summary": "",
            "error_message": "",
        }
        with self._lock:
            self._prune_locked(key, now_ts=now_ts)
            self._records[key].insert(0, record)
            self._jobs_in_flight.add(clip_id)

        thread = threading.Thread(
            target=self._run_clip_job,
            args=(key, clip_id, normalized_frames, dict(record)),
            name=f"akane-screen-vision-{clip_id}",
            daemon=True,
        )
        thread.start()
        return self._public_record(record, now_ts=now_ts)

    def list_latest(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        limit: int = 3,
        include_pending: bool = False,
    ) -> list[dict[str, Any]]:
        now_ts = int(time.time())
        key = self._key(profile_user_id, session_id)
        with self._lock:
            self._prune_locked(key, now_ts=now_ts)
            records = list(self._records.get(key, []))
        usable = [
            record
            for record in records
            if include_pending or str(record.get("status") or "") == "ready"
        ]
        return [self._public_record(record, now_ts=now_ts) for record in usable[: max(1, int(limit or 3))]]

    def get_clip(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        clip_id: str,
    ) -> dict[str, Any] | None:
        now_ts = int(time.time())
        key = self._key(profile_user_id, session_id)
        with self._lock:
            self._prune_locked(key, now_ts=now_ts)
            for record in self._records.get(key, []):
                if str(record.get("clip_id") or "") == str(clip_id or ""):
                    return self._public_record(record, now_ts=now_ts)
        return None

    def clear(self, *, profile_user_id: str, session_id: str | None = None) -> dict[str, Any]:
        profile = str(profile_user_id or "").strip()
        session = str(session_id or "").strip()
        removed = 0
        with self._lock:
            if session:
                key = self._key(profile, session)
                removed = len(self._records.pop(key, []))
            else:
                keys = [key for key in self._records if key[0] == profile]
                for key in keys:
                    removed += len(self._records.pop(key, []))
        return {"ok": True, "removed": removed}

    def build_prompt_context(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        limit: int = 3,
    ) -> str:
        observations = self.list_latest(
            profile_user_id=profile_user_id,
            session_id=session_id,
            limit=limit,
            include_pending=False,
        )
        if not observations:
            return ""

        lines = ["【刚刚看到的画面】"]
        for item in observations:
            card = item.get("observation") if isinstance(item.get("observation"), dict) else {}
            salience = self._coerce_float(card.get("salience"), default=0.0, minimum=0.0, maximum=1.0)
            has_specific_evidence = bool(card.get("visible_text") or card.get("concrete_details") or card.get("changes"))
            if salience < 0.25 and not has_specific_evidence:
                continue
            age_label = self._format_age(item.get("age_sec"))
            duration = max(0, int(item.get("captured_end_ts") or 0) - int(item.get("captured_start_ts") or 0))
            duration_text = f"，持续约 {duration} 秒" if duration > 0 else ""
            summary = str(item.get("summary") or "").strip()
            current_state = str(card.get("current_state") or "").strip()
            if summary:
                lines.append(f"{age_label}{duration_text}：{summary}")
            elif current_state:
                lines.append(f"{age_label}{duration_text}：{current_state}")
            visible_text = [
                str(text).strip()
                for text in list(card.get("visible_text") or [])
                if str(text).strip()
            ]
            if visible_text:
                lines.append(f"能看清的文字：{'；'.join(visible_text[:5])}")
            concrete_details = [
                str(detail).strip()
                for detail in list(card.get("concrete_details") or [])
                if str(detail).strip()
            ]
            if concrete_details:
                lines.append(f"具体细节：{'；'.join(concrete_details[:5])}")
            changes = [
                str(change).strip()
                for change in list(card.get("changes") or [])
                if str(change).strip()
            ]
            if changes:
                lines.append(f"刚才的变化：{'；'.join(changes[:3])}")
        if len(lines) == 1:
            return ""
        lines.append("请把这些当作你刚刚在旁边看到的情况，自然参考就好；不要把它当成主人亲口说过的话。")
        return "\n".join(lines)

    def build_reaction_with_llm(self, *, llm: Any, observation: dict[str, Any]) -> dict[str, Any]:
        clip = observation if isinstance(observation, dict) else {}
        card = clip.get("observation") if isinstance(clip.get("observation"), dict) else {}
        if bool(card.get("sensitive")):
            return {"ok": True, "speech": "", "emotion": "正常", "skip": True, "reason": "sensitive"}

        fallback = self._fallback_reaction(card)
        payload = fallback
        speech = str(payload.get("speech") or "").strip()[:80]
        if not speech:
            return {"ok": True, "speech": "", "emotion": "正常", "skip": True, "reason": "skip"}
        return {
            "ok": True,
            "speech": speech,
            "emotion": str(payload.get("emotion") or "正常").strip()[:40] or "正常",
            "skip": False,
            "clip_id": str(clip.get("clip_id") or ""),
        }

    def _run_clip_job(
        self,
        key: tuple[str, str],
        clip_id: str,
        frames: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> None:
        try:
            observation = self.vision_service.analyze_screen_clip(
                frames=frames,
                context=context,
            )
            normalized = self._normalize_observation(observation)
            self._update_record(
                key=key,
                clip_id=clip_id,
                patch={
                    "status": "ready",
                    "observed_at": int(time.time()),
                    "updated_at": int(time.time()),
                    "observation": normalized,
                    "summary": str(normalized.get("summary") or ""),
                    "error_message": "",
                },
            )
        except Exception as exc:
            self._update_record(
                key=key,
                clip_id=clip_id,
                patch={
                    "status": "error",
                    "updated_at": int(time.time()),
                    "error_message": str(exc)[:240],
                },
            )
        finally:
            with self._lock:
                self._jobs_in_flight.discard(clip_id)

    def _update_record(self, *, key: tuple[str, str], clip_id: str, patch: dict[str, Any]) -> None:
        with self._lock:
            for record in self._records.get(key, []):
                if str(record.get("clip_id") or "") == clip_id:
                    record.update(patch)
                    break
            self._prune_locked(key, now_ts=int(time.time()))

    def _prune_locked(self, key: tuple[str, str], *, now_ts: int) -> None:
        records = list(self._records.get(key, []))
        fresh: list[dict[str, Any]] = []
        ready_count = 0
        for record in records:
            status = str(record.get("status") or "")
            observed_at = int(record.get("observed_at") or 0)
            updated_at = int(record.get("updated_at") or 0)
            reference_ts = observed_at or updated_at
            if reference_ts and now_ts - reference_ts > int(record.get("ttl_sec") or self.ttl_sec):
                continue
            if status == "ready":
                ready_count += 1
                if ready_count > self.max_ready_per_session:
                    continue
            fresh.append(record)
        self._records[key] = fresh

    def _public_record(self, record: dict[str, Any], *, now_ts: int) -> dict[str, Any]:
        observed_at = int(record.get("observed_at") or 0)
        updated_at = int(record.get("updated_at") or 0)
        age_base = observed_at or updated_at or now_ts
        return {
            "clip_id": str(record.get("clip_id") or ""),
            "status": str(record.get("status") or ""),
            "captured_start_ts": int(record.get("captured_start_ts") or 0),
            "captured_end_ts": int(record.get("captured_end_ts") or 0),
            "observed_at": observed_at,
            "updated_at": updated_at,
            "age_sec": max(0, now_ts - age_base),
            "ttl_sec": int(record.get("ttl_sec") or self.ttl_sec),
            "frame_count": int(record.get("frame_count") or 0),
            "foreground": dict(record.get("foreground") or {}),
            "mode": str(record.get("mode") or ""),
            "summary": str(record.get("summary") or ""),
            "observation": dict(record.get("observation") or {}),
            "error_message": str(record.get("error_message") or ""),
        }

    def _normalize_frames(self, frames: Any) -> list[dict[str, Any]]:
        if not isinstance(frames, list):
            return []
        max_frames = 6
        max_data_len = 2_000_000
        normalized: list[dict[str, Any]] = []
        for item in frames[:max_frames]:
            if not isinstance(item, dict):
                continue
            data_url = str(item.get("data_url") or item.get("dataUrl") or "").strip()
            if not data_url.startswith("data:image/") or len(data_url) > max_data_len:
                continue
            normalized.append(
                {
                    "data_url": data_url,
                    "captured_at": int(item.get("captured_at") or item.get("capturedAt") or time.time()),
                    "width": int(float(item.get("width") or 0)),
                    "height": int(float(item.get("height") or 0)),
                }
            )
        return normalized

    def _normalize_foreground(self, value: dict[str, Any]) -> dict[str, Any]:
        return {
            "title": str(value.get("title") or "")[:180],
            "process_name": str(value.get("process_name") or value.get("processName") or "")[:80],
            "source": str(value.get("source") or "")[:40],
        }

    def _normalize_observation(self, value: Any) -> dict[str, Any]:
        payload = value if isinstance(value, dict) else {}
        return {
            "summary": str(payload.get("summary") or "")[:260],
            "current_state": str(payload.get("current_state") or payload.get("state") or "")[:220],
            "visible_text": self._string_list(payload.get("visible_text"), limit=6),
            "concrete_details": self._string_list(payload.get("concrete_details"), limit=6),
            "changes": self._string_list(payload.get("changes"), limit=5),
            "topics": self._string_list(payload.get("topics"), limit=6),
            "mood_tags": self._string_list(payload.get("mood_tags"), limit=6),
            "uncertainty": self._string_list(payload.get("uncertainty"), limit=4),
            "salience": self._coerce_float(payload.get("salience"), default=0.0, minimum=0.0, maximum=1.0),
            "sensitive": bool(payload.get("sensitive")),
            "confidence": self._coerce_float(payload.get("confidence"), default=0.5, minimum=0.0, maximum=1.0),
        }

    def _string_list(self, value: Any, *, limit: int) -> list[str]:
        if isinstance(value, list):
            items = [str(item).strip() for item in value if str(item).strip()]
        elif isinstance(value, str):
            items = [part.strip() for part in value.replace("，", ",").split(",") if part.strip()]
        else:
            items = []
        deduped: list[str] = []
        for item in items:
            if item not in deduped:
                deduped.append(item[:80])
        return deduped[:limit]

    def _coerce_float(self, value: Any, *, default: float, minimum: float, maximum: float) -> float:
        try:
            number = float(value)
        except Exception:
            number = default
        return max(minimum, min(maximum, number))

    def _format_age(self, value: Any) -> str:
        try:
            seconds = max(0, int(value or 0))
        except Exception:
            seconds = 0
        if seconds < 8:
            return "几秒前"
        if seconds < 60:
            return f"{seconds} 秒前"
        minutes = max(1, round(seconds / 60))
        return f"大约 {minutes} 分钟前"

    def _reaction_emotion(self, card: dict[str, Any]) -> str:
        text = " ".join(
            [
                str(card.get("summary") or ""),
                str(card.get("current_state") or ""),
                " ".join(str(item) for item in list(card.get("visible_text") or [])),
                " ".join(str(item) for item in list(card.get("concrete_details") or [])),
                " ".join(str(item) for item in list(card.get("topics") or [])),
                " ".join(str(item) for item in list(card.get("mood_tags") or [])),
            ]
        )
        if any(key in text for key in ("报错", "错误", "失败", "卡住", "不确定")):
            return "困惑"
        if any(key in text for key in ("游戏", "战斗", "视频", "直播", "开心", "有趣")):
            return "开心"
        return "正常"

    def _fallback_reaction(self, card: dict[str, Any]) -> dict[str, Any]:
        summary = str(card.get("summary") or card.get("current_state") or "").strip()
        has_context = bool(
            summary
            or card.get("changes")
            or card.get("topics")
            or card.get("visible_text")
            or card.get("concrete_details")
        )
        if not has_context:
            return {"speech": "", "emotion": "正常", "skip": True}
        if any(key in summary for key in ("报错", "错误", "失败", "卡住")):
            return {"speech": "这里好像卡了一下，要不要我陪你看看？", "emotion": "困惑", "skip": False}
        if any(key in summary for key in ("游戏", "战斗", "视频", "直播")):
            return {"speech": "刚刚这一下还挺有意思的。", "emotion": "开心", "skip": False}
        return {"speech": "我刚刚看到你还在忙这边。", "emotion": "正常", "skip": False}

    def _key(self, profile_user_id: str, session_id: str) -> tuple[str, str]:
        return str(profile_user_id or "").strip(), str(session_id or "").strip()
