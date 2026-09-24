"""SDK-first companion plugin: one gentle check-in per quiet conversation period."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from akane_plugin import (
    DIRECT_CONVERSATION_EVENT,
    GROUP_CONVERSATION_EVENT,
    Plugin,
    PluginQQCommandRequest,
    PluginQQCommandResult,
    Result,
    ToolContext,
)


PLUGIN_ID = "akane.sample.gentle-checkin"
PLUGIN_VERSION = "0.2.0"
CAPABILITY_ID = f"{PLUGIN_ID}.configure.v1"
SERVICE_ID = "quiet-checkin"
STATE_FILE = "checkins.json"
DEFAULT_IDLE_MINUTES = 30
MIN_IDLE_MINUTES = 1
MAX_IDLE_MINUTES = 24 * 60
POLL_SECONDS = 15.0
FAILED_ATTEMPT_RETRY_SECONDS = 60


def _now() -> int:
    return int(time.time())


def _subscription_key(session_id: str) -> str:
    material = session_id.encode("utf-8", errors="strict")
    return hashlib.sha256(material).hexdigest()


def _positive_int(value: object, *, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


@dataclass(frozen=True)
class DueCheckin:
    key: str
    session_id: str
    conversation_ref: str
    idle_seconds: int
    activity_at: int


class CheckinStateError(RuntimeError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class CheckinStore:
    """Small atomic JSON ledger inside the plugin's scoped storage directory."""

    def __init__(self, storage_dir: Path, *, clock: Callable[[], int] = _now) -> None:
        self._path = Path(storage_dir) / STATE_FILE
        self._clock = clock
        self._lock = threading.RLock()

    def configure(
        self,
        *,
        session_id: str,
        conversation_ref: str,
        idle_seconds: int,
    ) -> dict[str, Any]:
        now = self._clock()
        key = _subscription_key(session_id)
        with self._lock:
            state = self._read_locked()
            subscriptions = state.setdefault("subscriptions", {})
            subscriptions[key] = {
                "session_id": session_id,
                "conversation_ref": conversation_ref,
                "idle_seconds": idle_seconds,
                "enabled": True,
                "activity_at": now,
                "notified_activity_at": 0,
                "last_event_id": "",
                "last_attempt_at": 0,
                "last_status": "configured",
            }
            self._write_locked(state)
            return dict(subscriptions[key])

    def disable(self, *, profile_user_id: str, session_id: str) -> bool:
        key = _subscription_key(session_id)
        with self._lock:
            state = self._read_locked()
            item = state.get("subscriptions", {}).get(key)
            if not isinstance(item, dict):
                return False
            item["enabled"] = False
            item["last_status"] = "disabled"
            self._write_locked(state)
            return True

    def status(self, *, profile_user_id: str, session_id: str) -> dict[str, Any] | None:
        key = _subscription_key(session_id)
        with self._lock:
            item = self._read_locked().get("subscriptions", {}).get(key)
            return dict(item) if isinstance(item, dict) else None

    def mark_activity(self, *, session_id: str, event_id: str, occurred_at: int) -> int:
        changed = 0
        with self._lock:
            state = self._read_locked()
            for item in state.get("subscriptions", {}).values():
                if not isinstance(item, dict) or not item.get("enabled"):
                    continue
                if str(item.get("session_id") or "") != session_id:
                    continue
                if str(item.get("last_event_id") or "") == event_id:
                    continue
                previous_activity = _positive_int(item.get("activity_at"))
                if occurred_at < previous_activity:
                    continue
                item["activity_at"] = occurred_at
                item["notified_activity_at"] = 0
                item["last_event_id"] = event_id
                item["last_status"] = "observed_activity"
                changed += 1
            if changed:
                self._write_locked(state)
        return changed

    def claim_due(self, *, now: int | None = None) -> tuple[DueCheckin, ...]:
        current = _positive_int(now, default=self._clock())
        due: list[DueCheckin] = []
        with self._lock:
            state = self._read_locked()
            changed = False
            for key, item in state.get("subscriptions", {}).items():
                if not isinstance(item, dict) or not item.get("enabled"):
                    continue
                activity_at = _positive_int(item.get("activity_at"))
                idle_seconds = _positive_int(item.get("idle_seconds"))
                if not activity_at or not idle_seconds or current - activity_at < idle_seconds:
                    continue
                if _positive_int(item.get("notified_activity_at")) == activity_at:
                    continue
                last_attempt_at = _positive_int(item.get("last_attempt_at"))
                if last_attempt_at and current - last_attempt_at < FAILED_ATTEMPT_RETRY_SECONDS:
                    continue
                session_id = str(item.get("session_id") or "").strip()
                conversation_ref = str(item.get("conversation_ref") or "").strip()
                if not session_id or not conversation_ref:
                    continue
                item["last_attempt_at"] = current
                item["last_status"] = "reasoning"
                changed = True
                due.append(
                    DueCheckin(
                        key=str(key),
                        session_id=session_id,
                        conversation_ref=conversation_ref,
                        idle_seconds=idle_seconds,
                        activity_at=activity_at,
                    )
                )
            if changed:
                self._write_locked(state)
        return tuple(due)

    def finish(self, due: DueCheckin, *, delivered: bool, status: str) -> None:
        with self._lock:
            state = self._read_locked()
            item = state.get("subscriptions", {}).get(due.key)
            if not isinstance(item, dict):
                return
            if delivered and _positive_int(item.get("activity_at")) == due.activity_at:
                item["notified_activity_at"] = due.activity_at
            item["last_status"] = str(status or "unknown")[:80]
            self._write_locked(state)

    def is_current(self, due: DueCheckin) -> bool:
        with self._lock:
            item = self._read_locked().get("subscriptions", {}).get(due.key)
            return bool(
                isinstance(item, dict)
                and item.get("enabled")
                and _positive_int(item.get("activity_at")) == due.activity_at
                and _positive_int(item.get("notified_activity_at")) != due.activity_at
            )

    def _read_locked(self) -> dict[str, Any]:
        if not self._path.is_file():
            return {"version": 1, "subscriptions": {}}
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise CheckinStateError("state_read_failed") from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CheckinStateError("state_invalid") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("subscriptions"), dict):
            raise CheckinStateError("state_invalid")
        return payload

    def _write_locked(self, state: dict[str, Any]) -> None:
        temp: Path | None = None
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temp = self._path.with_name(f".{self._path.name}.{uuid.uuid4().hex}.tmp")
            data = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
            temp.write_text(data, encoding="utf-8")
            os.replace(temp, self._path)
        except OSError as exc:
            raise CheckinStateError("state_write_failed") from exc
        finally:
            try:
                if temp is not None and temp.exists():
                    temp.unlink(missing_ok=True)
            except OSError:
                pass


def _status_text(item: dict[str, Any] | None) -> str:
    if not item:
        return "本会话尚未配置温和问候。"
    enabled = bool(item.get("enabled"))
    minutes = max(1, _positive_int(item.get("idle_seconds"), default=60) // 60)
    state = "已开启" if enabled else "已关闭"
    return f"温和问候{state}，静默间隔 {minutes} 分钟；每段静默最多问候一次。"


def _configuration_result(item: dict[str, Any] | None, *, summary: str) -> Result:
    content = {
        "configured": bool(item),
        "enabled": bool(item and item.get("enabled")),
        "idle_minutes": (
            max(1, _positive_int(item.get("idle_seconds"), default=60) // 60)
            if item
            else 0
        ),
        "last_status": str(item.get("last_status") or "") if item else "",
    }
    return Result(value=content, content=summary, status="ok")


def _configure_tool(store_of):
    """One model-facing tool bound to this plugin instance's own store."""

    def configure(action: str = "status", idle_minutes: int = DEFAULT_IDLE_MINUTES,
                  ctx: ToolContext = None) -> Result:
        store = store_of()
        if store is None:
            return Result(is_error=True, status="unavailable", reason="storage_not_ready")
        context = getattr(ctx, "invocation", None)
        profile_user_id = str(getattr(context, "profile_user_id", "") or "").strip()
        session_id = str(getattr(context, "session_id", "") or "").strip()
        conversation_ref = str(getattr(context, "conversation_ref", "") or "").strip()
        if not profile_user_id or not session_id:
            return Result(is_error=True, status="invalid_context", reason="conversation_identity_required")
        clean_action = str(action or "status").strip().lower()
        try:
            if clean_action == "status":
                item = store.status(profile_user_id=profile_user_id, session_id=session_id)
                return _configuration_result(item, summary=_status_text(item))
            if clean_action == "disable":
                changed = store.disable(profile_user_id=profile_user_id, session_id=session_id)
                item = store.status(profile_user_id=profile_user_id, session_id=session_id)
                return _configuration_result(
                    item,
                    summary="已关闭本会话的温和问候。" if changed else "本会话尚未配置温和问候。",
                )
        except CheckinStateError as exc:
            return Result(is_error=True, status="unavailable", reason=exc.reason)
        if clean_action != "enable":
            return Result(is_error=True, status="invalid_input", reason="unsupported_action")
        if isinstance(idle_minutes, bool) or not isinstance(idle_minutes, int) or not MIN_IDLE_MINUTES <= idle_minutes <= MAX_IDLE_MINUTES:
            return Result(is_error=True, status="invalid_input", reason="idle_minutes_out_of_range")
        if not conversation_ref:
            return Result(is_error=True, status="invalid_context", reason="conversation_reference_required")
        try:
            item = store.configure(
                session_id=session_id,
                conversation_ref=conversation_ref,
                idle_seconds=idle_minutes * 60,
            )
        except CheckinStateError as exc:
            return Result(is_error=True, status="unavailable", reason=exc.reason)
        return _configuration_result(item, summary=f"已开启：安静 {idle_minutes} 分钟后自然问候一次。")

    configure.__name__ = "configure.v1"
    configure.__doc__ = (
        "Configure one gentle check-in for the current private QQ chat: enable, disable, or inspect it."
    )
    return configure


def _activity_handler(store_of):
    """Record quiet-conversation activity; never requests a model turn."""

    async def record(event, ctx) -> None:
        store = store_of()
        if store is None:
            return None
        data = event.data if isinstance(event.data, dict) else {}
        session_id = str(data.get("session_id") or "").strip()
        if not session_id or not event.event_id:
            return None
        occurred_at = _positive_int(int(event.occurred_at_ms / 1000), default=_now())
        try:
            store.mark_activity(session_id=session_id, event_id=event.event_id, occurred_at=occurred_at)
        except CheckinStateError:
            return None
        return None

    return record


def _checkin_command(store_of):
    """Configure gentle check-in from a QQ command; the host owns sender authority."""

    async def checkin_command(request: PluginQQCommandRequest) -> PluginQQCommandResult:
        store = store_of()
        if store is None:
            return PluginQQCommandResult(True, "温和问候配置暂时不可用。", "storage_not_ready")
        if not str(request.session_id or "").strip():
            return PluginQQCommandResult(True, "当前会话身份不可用，配置没有写入。", "conversation_identity_required")
        parts = str(request.args or "").strip().lower().split()
        action = parts[0] if parts else "status"
        if action in {"on", "enable"}:
            if request.is_group and request.sender_role not in {"owner", "admin"}:
                return PluginQQCommandResult(True, "只有群主或管理员可以为本群开启温和问候。", "group_admin_required")
            minutes = _positive_int(parts[1], default=DEFAULT_IDLE_MINUTES) if len(parts) > 1 else DEFAULT_IDLE_MINUTES
            if not MIN_IDLE_MINUTES <= minutes <= MAX_IDLE_MINUTES:
                return PluginQQCommandResult(True, f"间隔需为 {MIN_IDLE_MINUTES}–{MAX_IDLE_MINUTES} 分钟。", "idle_minutes_out_of_range")
            if request.is_group and request.group_id <= 0:
                return PluginQQCommandResult(True, "当前群身份不可用，配置没有写入。", "conversation_identity_required")
            if not request.is_group and request.qq_number <= 0:
                return PluginQQCommandResult(True, "当前私聊身份不可用，配置没有写入。", "conversation_identity_required")
            if not request.conversation_ref:
                return PluginQQCommandResult(True, "当前会话引用不可用，配置没有写入。", "conversation_reference_required")
            try:
                store.configure(
                    session_id=request.session_id,
                    conversation_ref=request.conversation_ref,
                    idle_seconds=minutes * 60,
                )
            except CheckinStateError as exc:
                return PluginQQCommandResult(True, "温和问候配置暂时不可用。", exc.reason)
            return PluginQQCommandResult(True, f"已开启：本会话安静 {minutes} 分钟后，我会自然问候一次。")
        if action in {"off", "disable"}:
            if request.is_group and request.sender_role not in {"owner", "admin"}:
                return PluginQQCommandResult(True, "只有群主或管理员可以关闭本群温和问候。", "group_admin_required")
            try:
                changed = store.disable(
                    profile_user_id=request.profile_user_id or str(request.qq_number),
                    session_id=request.session_id,
                )
            except CheckinStateError as exc:
                return PluginQQCommandResult(True, "温和问候配置暂时不可用。", exc.reason)
            return PluginQQCommandResult(True, "已关闭本会话的温和问候。" if changed else "本会话尚未开启温和问候。")
        if action == "status":
            try:
                item = store.status(
                    profile_user_id=request.profile_user_id or str(request.qq_number),
                    session_id=request.session_id,
                )
            except CheckinStateError as exc:
                return PluginQQCommandResult(True, "温和问候配置暂时不可用。", exc.reason)
            return PluginQQCommandResult(True, _status_text(item))
        return PluginQQCommandResult(True, "用法：/checkin on [分钟]、/checkin status、/checkin off。", "invalid_command_args")

    return checkin_command


def _quiet_checkin_service(store_of):
    """Poll due check-ins and request one normal Agent turn through the host queue."""

    async def quiet_checkin_service(ctx) -> None:
        store = store_of()
        if store is None:
            return
        while not ctx.shutdown_requested:
            for due in store.claim_due():
                if ctx.shutdown_requested:
                    return
                if not store.is_current(due):
                    continue
                trace = hashlib.sha256(f"{due.key}:{due.activity_at}".encode("utf-8")).hexdigest()[:24]
                try:
                    receipt = await ctx.request_turn(
                        "A configured gentle check-in is due for this quiet conversation.",
                        {
                            "event_type": "companion.gentle_checkin_due",
                            "source": PLUGIN_ID,
                            "quiet_seconds": due.idle_seconds,
                            "delivery_purpose": "gentle_checkin",
                            "trace_id": f"gentle-checkin-{trace}",
                        },
                        coalesce_key=f"{PLUGIN_ID}:{due.key}",
                    )
                except Exception:
                    store.finish(due, delivered=False, status="agent_turn_unavailable")
                    continue
                delivered = receipt.status == "completed" and receipt.delivery_status in {
                    "delivered", "sent", "queued", "suppressed",
                }
                store.finish(
                    due,
                    delivered=delivered,
                    status=receipt.reason or receipt.delivery_status or receipt.status or "unknown",
                )
            if not await ctx.sleep(POLL_SECONDS):
                return

    return quiet_checkin_service


def create_plugin() -> Plugin:
    """Build one plugin instance with its own store and declared contributions."""

    store_holder: dict[str, CheckinStore] = {}

    def setup(registrar) -> None:
        store_holder["store"] = CheckinStore(registrar.get_storage_dir())

    plugin = Plugin(PLUGIN_ID, version=PLUGIN_VERSION, permissions=("storage.write",), setup=setup)
    store_of = lambda: store_holder.get("store")  # noqa: E731 - resolved after setup runs
    plugin.tool(_configure_tool(store_of), name="configure.v1", effects=("plugin_state",), visible_in=("qq",))
    plugin.on(DIRECT_CONVERSATION_EVENT, name="activity.direct", scope="conversation")(_activity_handler(store_of))
    plugin.on(GROUP_CONVERSATION_EVENT, name="activity.group", scope="conversation")(_activity_handler(store_of))
    plugin.qq_command("/checkin")(_checkin_command(store_of))
    plugin.background(SERVICE_ID)(_quiet_checkin_service(store_of))
    plugin.skill("gentle-checkin")
    return plugin


__all__ = [
    "CAPABILITY_ID",
    "DEFAULT_IDLE_MINUTES",
    "FAILED_ATTEMPT_RETRY_SECONDS",
    "MAX_IDLE_MINUTES",
    "MIN_IDLE_MINUTES",
    "PLUGIN_ID",
    "PLUGIN_VERSION",
    "POLL_SECONDS",
    "SERVICE_ID",
    "CheckinStateError",
    "CheckinStore",
    "DueCheckin",
    "create_plugin",
]
