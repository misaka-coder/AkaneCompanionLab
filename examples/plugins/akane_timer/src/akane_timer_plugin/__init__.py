"""SDK-first plugin: durable one-shot scheduled events for the current conversation."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from akane_plugin import (
    Plugin,
    PluginQQCommandRequest,
    PluginQQCommandResult,
    Result,
    ToolContext,
)


PLUGIN_ID = "akane.timer"
PLUGIN_VERSION = "0.3.0"
CAPABILITY_ID = f"{PLUGIN_ID}.schedule.v1"
SERVICE_ID = "timer-dispatch"
DUE_EVENT_TYPE = f"{PLUGIN_ID}.due"
STATE_FILE = "timers.json"
POLL_SECONDS = 0.5
FAILED_RETRY_SECONDS = 30
MAX_EVENT_TEXT_LENGTH = 2000
MAX_FIELDS = 16
DEFAULT_DELAY_SECONDS = 60
MIN_DELAY_SECONDS = 1
MAX_DELAY_SECONDS = 365 * 24 * 60 * 60


def _now() -> int:
    return int(time.time())


def _positive_int(value: object, *, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _timer_key(event_id: str) -> str:
    return hashlib.sha256(event_id.encode("utf-8", errors="strict")).hexdigest()


def _stable_fields(fields: dict[str, Any] | None) -> tuple[tuple[str, str], ...]:
    if not isinstance(fields, dict):
        return ()
    out: list[tuple[str, str]] = []
    for key, value in fields.items():
        normalized_key = str(key or "").strip().lower() if isinstance(key, str) else ""
        if re.fullmatch(r"[a-z][a-z0-9_.-]{0,63}", normalized_key) is None:
            continue
        if normalized_key in {"event_text", "due_at"} or any(
            existing == normalized_key for existing, _value in out
        ):
            continue
        try:
            serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError):
            serialized = str(value)
        out.append((normalized_key, serialized[:4000]))
        if len(out) >= MAX_FIELDS - 2:
            break
    return tuple(out)


@dataclass(frozen=True)
class DueTimer:
    event_id: str
    key: str
    session_id: str
    conversation_ref: str
    event_text: str
    fields: tuple[tuple[str, str], ...]
    due_at: int
    trace: str


class TimerStateError(RuntimeError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class TimerStore:
    """Durable one-shot timers inside the plugin's scoped storage directory."""

    def __init__(self, storage_dir: Path, *, clock: Callable[[], int] = _now) -> None:
        self._path = Path(storage_dir) / STATE_FILE
        self._clock = clock
        self._lock = threading.RLock()

    def schedule(
        self,
        *,
        event_id: str,
        session_id: str,
        conversation_ref: str,
        event_text: str,
        fields: dict[str, Any] | None,
        due_at: int,
    ) -> dict[str, Any]:
        key = _timer_key(event_id)
        with self._lock:
            state = self._read_locked()
            state.setdefault("timers", {})[key] = {
                "event_id": event_id,
                "session_id": session_id,
                "conversation_ref": conversation_ref,
                "event_text": event_text,
                "fields": [list(item) for item in _stable_fields(fields)],
                "due_at": int(due_at),
                "status": "scheduled",
                "last_reason": "",
                "attempt_at": 0,
                "trace": uuid.uuid4().hex[:24],
            }
            self._write_locked(state)
            return dict(state["timers"][key])

    def claim_due(self, *, now: int | None = None) -> tuple[DueTimer, ...]:
        current = _positive_int(now, default=self._clock())
        claimed: list[DueTimer] = []
        with self._lock:
            state = self._read_locked()
            changed = False
            for item in state.get("timers", {}).values():
                if not isinstance(item, dict) or item.get("status") != "scheduled":
                    continue
                if _positive_int(item.get("due_at")) > current:
                    continue
                item["status"] = "dispatching"
                item["attempt_at"] = current
                changed = True
                claimed.append(_due_timer(item))
            if changed:
                self._write_locked(state)
        return tuple(claimed)

    def finish(self, timer: DueTimer, *, delivered: bool, reason: str) -> None:
        with self._lock:
            state = self._read_locked()
            item = state.get("timers", {}).get(timer.key)
            if not isinstance(item, dict):
                return
            item["status"] = "delivered" if delivered else "failed"
            item["last_reason"] = str(reason or "")[:120]
            self._write_locked(state)

    def cancel(self, *, event_id: str, session_id: str) -> bool:
        key = _timer_key(event_id)
        with self._lock:
            state = self._read_locked()
            item = state.get("timers", {}).get(key)
            if not isinstance(item, dict) or str(item.get("session_id") or "") != session_id:
                return False
            if item.get("status") != "scheduled":
                return False
            item["status"] = "cancelled"
            self._write_locked(state)
            return True

    def status(self, *, event_id: str, session_id: str) -> dict[str, Any] | None:
        key = _timer_key(event_id)
        with self._lock:
            item = self._read_locked().get("timers", {}).get(key)
            if not isinstance(item, dict) or str(item.get("session_id") or "") != session_id:
                return None
            return dict(item)

    def list_active(self, *, session_id: str) -> tuple[dict[str, Any], ...]:
        with self._lock:
            items = [
                dict(item) for item in self._read_locked().get("timers", {}).values()
                if isinstance(item, dict)
                and str(item.get("session_id") or "") == session_id
                and item.get("status") in {"scheduled", "dispatching"}
            ]
        return tuple(sorted(items, key=lambda item: _positive_int(item.get("due_at"))))

    def recover_stuck(self, *, now: int | None = None) -> int:
        """Return timers whose dispatch attempt never finished to the queue."""

        current = _positive_int(now, default=self._clock())
        recovered = 0
        with self._lock:
            state = self._read_locked()
            for item in state.get("timers", {}).values():
                if not isinstance(item, dict) or item.get("status") != "dispatching":
                    continue
                attempt_at = _positive_int(item.get("attempt_at"))
                if attempt_at and current - attempt_at < FAILED_RETRY_SECONDS:
                    continue
                item["status"] = "scheduled"
                item["last_reason"] = "recovered_after_restart"
                recovered += 1
            if recovered:
                self._write_locked(state)
        return recovered

    def _read_locked(self) -> dict[str, Any]:
        if not self._path.is_file():
            return {"version": 1, "timers": {}}
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise TimerStateError("state_read_failed") from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TimerStateError("state_invalid") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("timers"), dict):
            raise TimerStateError("state_invalid")
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
            raise TimerStateError("state_write_failed") from exc
        finally:
            try:
                if temp is not None and temp.exists():
                    temp.unlink(missing_ok=True)
            except OSError:
                pass


def _due_timer(item: dict[str, Any]) -> DueTimer:
    fields = item.get("fields")
    pairs = tuple(
        (str(pair[0]), str(pair[1]))
        for pair in fields
        if isinstance(pair, (list, tuple)) and len(pair) == 2
    ) if isinstance(fields, list) else ()
    return DueTimer(
        event_id=str(item.get("event_id") or ""),
        key=_timer_key(str(item.get("event_id") or "")),
        session_id=str(item.get("session_id") or ""),
        conversation_ref=str(item.get("conversation_ref") or ""),
        event_text=str(item.get("event_text") or ""),
        fields=pairs,
        due_at=_positive_int(item.get("due_at")),
        trace=str(item.get("trace") or ""),
    )


def _status_text(items: tuple[dict[str, Any], ...]) -> str:
    if not items:
        return "当前会话没有进行中的定时事件。"
    lines = ["当前定时事件："]
    for item in items:
        lines.append(f"- {item.get('event_id')} 于 {_positive_int(item.get('due_at'))} 触发，状态 {item.get('status')}")
    return "\n".join(lines)


def _schedule_result(items: tuple[dict[str, Any], ...], *, summary: str) -> Result:
    events = [
        {
            "event_id": str(item.get("event_id") or ""),
            "status": str(item.get("status") or ""),
            "due_at": _positive_int(item.get("due_at")),
            "event_text": str(item.get("event_text") or ""),
            "last_reason": str(item.get("last_reason") or ""),
        }
        for item in items
    ]
    return Result(value={"events": events}, content=summary, status="ok")


def _schedule_tool(store_of):
    """One model-facing tool bound to this plugin instance's own store."""

    def schedule(
        action: str = "create",
        delay_seconds: int = DEFAULT_DELAY_SECONDS,
        due_at: int = 0,
        event_text: str = "",
        event_id: str = "",
        fields: dict[str, Any] | None = None,
        ctx: ToolContext = None,
    ) -> Result:
        store = store_of()
        if store is None:
            return Result(is_error=True, status="unavailable", reason="storage_not_ready")
        context = getattr(ctx, "invocation", None)
        session_id = str(getattr(context, "session_id", "") or "").strip()
        conversation_ref = str(getattr(context, "conversation_ref", "") or "").strip()
        if not session_id:
            return Result(is_error=True, status="invalid_context", reason="conversation_identity_required")
        clean_action = str(action or "create").strip().lower()
        try:
            if clean_action in {"status", "list"}:
                return _schedule_result(store.list_active(session_id=session_id), summary="当前会话的定时事件。")
            if clean_action == "cancel":
                target = str(event_id or "").strip()
                if not target:
                    return Result(is_error=True, status="invalid_input", reason="event_id_required")
                changed = store.cancel(event_id=target, session_id=session_id)
                item = store.status(event_id=target, session_id=session_id)
                return _schedule_result(
                    (item,) if item else (),
                    summary="已取消该定时事件。" if changed else "没有找到可取消的定时事件。",
                )
        except TimerStateError as exc:
            return Result(is_error=True, status="unavailable", reason=exc.reason)
        if clean_action != "create":
            return Result(is_error=True, status="invalid_input", reason="unsupported_action")
        if not conversation_ref:
            return Result(is_error=True, status="invalid_context", reason="conversation_reference_required")
        if isinstance(delay_seconds, bool) or not isinstance(delay_seconds, int):
            return Result(is_error=True, status="invalid_input", reason="delay_seconds_out_of_range")
        now = _now()
        absolute_due = _positive_int(due_at)
        if absolute_due:
            if absolute_due <= now or absolute_due - now > MAX_DELAY_SECONDS:
                return Result(is_error=True, status="invalid_input", reason="due_at_out_of_range")
            computed_due = absolute_due
        elif MIN_DELAY_SECONDS <= delay_seconds <= MAX_DELAY_SECONDS:
            computed_due = now + delay_seconds
        else:
            return Result(is_error=True, status="invalid_input", reason="delay_seconds_out_of_range")
        clean_text = str(event_text or "").strip()
        if not clean_text:
            return Result(is_error=True, status="invalid_input", reason="event_text_required")
        if len(clean_text) > MAX_EVENT_TEXT_LENGTH:
            return Result(is_error=True, status="invalid_input", reason="event_text_too_long")
        created_id = f"timer-{uuid.uuid4().hex[:20]}"
        try:
            item = store.schedule(
                event_id=created_id,
                session_id=session_id,
                conversation_ref=conversation_ref,
                event_text=clean_text,
                fields=fields,
                due_at=computed_due,
            )
        except TimerStateError as exc:
            return Result(is_error=True, status="unavailable", reason=exc.reason)
        return _schedule_result((item,), summary=f"已登记定时事件 {created_id}，将在 {computed_due} 触发。")

    schedule.__name__ = "schedule.v1"
    schedule.__doc__ = "Create, inspect, or cancel a one-shot event for the current conversation."
    return schedule


def _timer_command(store_of):
    """QQ command surface for the same store; the host owns sender authority."""

    async def create(request: PluginQQCommandRequest, args: list[str]) -> PluginQQCommandResult:
        store = store_of()
        if store is None:
            return PluginQQCommandResult(True, "定时事件暂时不可用。", "storage_not_ready")
        if request.is_group and request.sender_role not in {"owner", "admin"}:
            return PluginQQCommandResult(True, "只有群主或管理员可以为本群创建定时事件。", "group_admin_required")
        if len(args) < 2:
            return PluginQQCommandResult(True, "用法：/timer create <秒> <事件内容>。", "invalid_command_args")
        try:
            delay_seconds = int(args[0])
        except ValueError:
            delay_seconds = 0
        event_text = " ".join(args[1:]).strip()
        if not MIN_DELAY_SECONDS <= delay_seconds <= MAX_DELAY_SECONDS:
            return PluginQQCommandResult(True, f"秒数需为 {MIN_DELAY_SECONDS}–{MAX_DELAY_SECONDS}。", "delay_seconds_out_of_range")
        if not event_text or len(event_text) > MAX_EVENT_TEXT_LENGTH:
            return PluginQQCommandResult(True, "请提供不超过 2000 字的事件内容。", "event_text_invalid")
        if not request.conversation_ref:
            return PluginQQCommandResult(True, "当前会话引用不可用。", "conversation_reference_required")
        event_id = f"timer-{uuid.uuid4().hex[:20]}"
        try:
            store.schedule(
                event_id=event_id,
                session_id=request.session_id,
                conversation_ref=request.conversation_ref,
                event_text=event_text,
                fields=None,
                due_at=_now() + delay_seconds,
            )
        except TimerStateError as exc:
            return PluginQQCommandResult(True, "定时事件配置暂时不可用。", exc.reason)
        return PluginQQCommandResult(True, f"已登记定时事件 {event_id}，{delay_seconds} 秒后触发。")

    async def timer_command(request: PluginQQCommandRequest) -> PluginQQCommandResult:
        store = store_of()
        if store is None:
            return PluginQQCommandResult(True, "定时事件暂时不可用。", "storage_not_ready")
        session_id = str(request.session_id or "").strip()
        if not session_id:
            return PluginQQCommandResult(True, "当前会话身份不可用。", "conversation_identity_required")
        parts = str(request.args or "").strip().split()
        action = parts[0].lower() if parts else "status"
        if action in {"create", "add"}:
            return await create(request, parts[1:])
        if action == "cancel":
            event_id = parts[1] if len(parts) > 1 else ""
            if not event_id:
                return PluginQQCommandResult(True, "用法：/timer cancel <事件ID>。", "event_id_required")
            try:
                changed = store.cancel(event_id=event_id, session_id=session_id)
            except TimerStateError as exc:
                return PluginQQCommandResult(True, "定时事件暂时不可用。", exc.reason)
            return PluginQQCommandResult(
                True,
                f"已取消定时事件 {event_id}。" if changed else "没有找到可取消的定时事件。",
                "" if changed else "not_found",
            )
        if action == "status":
            try:
                return PluginQQCommandResult(True, _status_text(store.list_active(session_id=session_id)))
            except TimerStateError as exc:
                return PluginQQCommandResult(True, "定时事件暂时不可用。", exc.reason)
        return PluginQQCommandResult(True, "用法：/timer create <秒> <事件内容>；/timer status；/timer cancel <事件ID>。", "invalid_command_args")

    return timer_command


def _timer_service(store_of, *, poll_seconds: float = POLL_SECONDS):
    """Poll due timers and request one normal Agent turn through the host queue."""

    async def timer_service(ctx) -> None:
        store = store_of()
        if store is None:
            return
        store.recover_stuck()
        while not ctx.shutdown_requested:
            for due in store.claim_due():
                if ctx.shutdown_requested:
                    return
                try:
                    receipt = await ctx.request_turn(
                        f"A user-configured scheduled event is due now: {due.event_text}",
                        {
                            "event_type": DUE_EVENT_TYPE,
                            "source": PLUGIN_ID,
                            "event_text": due.event_text,
                            "due_at": due.due_at,
                            "trace_id": f"akane-timer-{due.trace}",
                            "fields": {key: value for key, value in due.fields},
                        },
                        coalesce_key=f"{PLUGIN_ID}:{due.key}",
                        conversation_ref=due.conversation_ref,
                    )
                except Exception:
                    store.finish(due, delivered=False, reason="agent_turn_unavailable")
                    continue
                delivered = receipt.status == "completed" and receipt.delivery_status in {
                    "delivered", "sent", "queued", "suppressed",
                }
                store.finish(
                    due,
                    delivered=delivered,
                    reason=receipt.reason or receipt.delivery_status or receipt.status or "unknown",
                )
            if not await ctx.sleep(max(0.05, float(poll_seconds))):
                return

    return timer_service


def create_plugin() -> Plugin:
    """Build one plugin instance with its own store and declared contributions."""

    store_holder: dict[str, TimerStore] = {}

    def setup(registrar) -> None:
        store_holder["store"] = TimerStore(registrar.get_storage_dir())

    plugin = Plugin(PLUGIN_ID, version=PLUGIN_VERSION, permissions=("storage.write",), setup=setup)
    store_of = lambda: store_holder.get("store")  # noqa: E731 - resolved after setup runs
    plugin.tool(_schedule_tool(store_of), name="schedule.v1", effects=("plugin_state",),
                visible_in=("desktop", "qq"))
    plugin.qq_command("/timer")(_timer_command(store_of))
    plugin.background(SERVICE_ID)(_timer_service(store_of))
    return plugin


__all__ = [
    "CAPABILITY_ID",
    "DUE_EVENT_TYPE",
    "PLUGIN_ID",
    "PLUGIN_VERSION",
    "DueTimer",
    "TimerStateError",
    "TimerStore",
    "create_plugin",
]
