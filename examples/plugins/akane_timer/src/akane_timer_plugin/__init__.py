"""Durable one-shot scheduled Agent events for Akane."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from capcore import CapabilityIOSlot, CapabilityResult, InvocationContext
from capcore_adapter_python import PythonCapabilityAdapter, PythonCapabilitySpec

from companion_v01.plugin_api import (
    AGENT_EVENT_SUBMIT_PERMISSION,
    AKANE_PLUGIN_API_VERSION,
    BACKGROUND_JOB_PERMISSION,
    CAPABILITY_PROMPT_INVOKE_PERMISSION,
    PLUGIN_QQ_COMMAND_PERMISSION,
    PLUGIN_STATE_EFFECT,
    PLUGIN_STORAGE_WRITE_PERMISSION,
    PluginAgentEventRequest,
    PluginExternalEvent,
    PluginManifest,
    PluginQQCommandRequest,
    PluginQQCommandResult,
    PluginRegistrar,
    PluginResultExperience,
    PluginResultPayload,
)


PLUGIN_ID = "akane.timer"
PLUGIN_VERSION = "0.2.0"
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
    """Atomic plugin-owned ledger; the opaque reference is never interpreted."""

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
        event_id = str(event_id or "").strip()
        session_id = str(session_id or "").strip()
        conversation_ref = str(conversation_ref or "").strip()
        event_text = str(event_text or "").strip()
        due_at = _positive_int(due_at)
        if not event_id or not session_id or not conversation_ref or not event_text or not due_at:
            raise TimerStateError("required_field_missing")
        key = _timer_key(event_id)
        with self._lock:
            state = self._read_locked()
            timers = state.setdefault("timers", {})
            if key in timers:
                raise TimerStateError("event_id_already_exists")
            timers[key] = {
                "event_id": event_id,
                "session_id": session_id,
                "conversation_ref": conversation_ref,
                "event_text": event_text,
                "fields": _stable_fields(fields),
                "due_at": due_at,
                "status": "scheduled",
                "created_at": self._clock(),
                "attempts": 0,
                "last_attempt_at": 0,
                "last_reason": "",
            }
            self._write_locked(state)
            return dict(timers[key])

    def claim_due(self, *, now: int | None = None) -> tuple[DueTimer, ...]:
        current = _positive_int(now, default=self._clock())
        due: list[DueTimer] = []
        with self._lock:
            state = self._read_locked()
            changed = False
            for key, item in state.get("timers", {}).items():
                if not isinstance(item, dict) or item.get("status") not in {"scheduled", "failed"}:
                    continue
                due_at = _positive_int(item.get("due_at"))
                if not due_at or current < due_at:
                    continue
                last_attempt_at = _positive_int(item.get("last_attempt_at"))
                if item.get("status") == "failed" and last_attempt_at and current - last_attempt_at < FAILED_RETRY_SECONDS:
                    continue
                attempts = _positive_int(item.get("attempts")) + 1
                item["status"] = "claimed"
                item["attempts"] = attempts
                item["last_attempt_at"] = current
                item["last_reason"] = ""
                changed = True
                trace = hashlib.sha256(f"{key}:{due_at}".encode("utf-8")).hexdigest()[:24]
                due.append(
                    DueTimer(
                        event_id=str(item.get("event_id") or ""),
                        key=str(key),
                        session_id=str(item.get("session_id") or "").strip(),
                        conversation_ref=str(item.get("conversation_ref") or "").strip(),
                        event_text=str(item.get("event_text") or "").strip(),
                        fields=tuple(tuple(pair) for pair in item.get("fields") or ()),
                        due_at=due_at,
                        trace=trace,
                    )
                )
            if changed:
                self._write_locked(state)
        return tuple(due)

    def finish(self, timer: DueTimer, *, delivered: bool, reason: str) -> None:
        with self._lock:
            state = self._read_locked()
            item = state.get("timers", {}).get(timer.key)
            if not isinstance(item, dict) or item.get("status") != "claimed":
                return
            item["status"] = "delivered" if delivered else "failed"
            item["last_reason"] = str(reason or "")[:160]
            item["last_attempt_at"] = self._clock()
            self._write_locked(state)

    def cancel(self, *, event_id: str, session_id: str) -> bool:
        key = _timer_key(str(event_id or "").strip())
        with self._lock:
            state = self._read_locked()
            item = state.get("timers", {}).get(key)
            if not isinstance(item, dict) or str(item.get("session_id") or "") != session_id:
                return False
            if item.get("status") in {"delivered", "cancelled"}:
                return False
            item["status"] = "cancelled"
            item["last_reason"] = "user_cancelled"
            self._write_locked(state)
            return True

    def status(self, *, event_id: str, session_id: str) -> dict[str, Any] | None:
        key = _timer_key(str(event_id or "").strip())
        with self._lock:
            item = self._read_locked().get("timers", {}).get(key)
            if not isinstance(item, dict) or str(item.get("session_id") or "") != session_id:
                return None
            return dict(item)

    def list_active(self, *, session_id: str) -> tuple[dict[str, Any], ...]:
        with self._lock:
            items = [
                dict(item)
                for item in self._read_locked().get("timers", {}).values()
                if isinstance(item, dict)
                and str(item.get("session_id") or "") == session_id
                and item.get("status") in {"scheduled", "claimed", "failed"}
            ]
        return tuple(sorted(items, key=lambda item: _positive_int(item.get("due_at"))))

    def recover_stuck(self, *, now: int | None = None) -> int:
        current = _positive_int(now, default=self._clock())
        recovered = 0
        with self._lock:
            state = self._read_locked()
            for item in state.get("timers", {}).values():
                if not isinstance(item, dict) or item.get("status") != "claimed":
                    continue
                last_attempt_at = _positive_int(item.get("last_attempt_at"))
                if last_attempt_at and current - last_attempt_at < FAILED_RETRY_SECONDS * 2:
                    continue
                item["status"] = "scheduled"
                recovered += 1
            if recovered:
                self._write_locked(state)
        return recovered

    def _read_locked(self) -> dict[str, Any]:
        if not self._path.is_file():
            return {"version": 2, "timers": {}}
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
        temporary: Path | None = None
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._path.with_name(f".{self._path.name}.{uuid.uuid4().hex}.tmp")
            temporary.write_text(
                json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, self._path)
        except OSError as exc:
            raise TimerStateError("state_write_failed") from exc
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


class TimerService:
    def __init__(self, store: TimerStore, agent_event_port: Any, *, poll_seconds: float = POLL_SECONDS) -> None:
        self._store = store
        self._agent_events = agent_event_port
        self._poll_seconds = max(0.05, float(poll_seconds))
        self._stop = asyncio.Event()

    async def start(self, controller: Any) -> None:
        self._store.recover_stuck()
        while not controller.shutdown_requested and not self._stop.is_set():
            for due in self._store.claim_due():
                if controller.shutdown_requested or self._stop.is_set():
                    return
                await self._process(due)
            if await controller.wait_for_shutdown(timeout=self._poll_seconds):
                return

    async def stop(self) -> None:
        self._stop.set()

    async def _process(self, due: DueTimer) -> None:
        result = await self._agent_events.submit(
            PluginAgentEventRequest(
                trace_id=f"akane-timer-{due.trace}",
                conversation_ref=due.conversation_ref,
                message=f"A user-configured scheduled event is due now: {due.event_text}",
                memory_idempotency_key=f"{PLUGIN_ID}:{due.trace}",
                event=PluginExternalEvent(
                    event_type=DUE_EVENT_TYPE,
                    source=PLUGIN_ID,
                    fields=(("event_text", due.event_text), ("due_at", str(due.due_at)), *due.fields),
                ),
                delivery="timeline",
            )
        )
        delivered = result.ok and result.delivery_status in {"delivered", "sent", "queued", "suppressed"}
        self._store.finish(
            due,
            delivered=delivered,
            reason=result.reason or result.delivery_status or result.status,
        )


def _schedule_capability(store: TimerStore) -> Callable[..., CapabilityResult]:
    def schedule_timer(
        action: str = "create",
        delay_seconds: int = DEFAULT_DELAY_SECONDS,
        due_at: int = 0,
        event_text: str = "",
        event_id: str = "",
        fields: dict[str, Any] | None = None,
        *,
        ctx: InvocationContext,
    ) -> CapabilityResult:
        clean_action = str(action or "create").strip().lower()
        session_id = str(ctx.session_id or "").strip()
        conversation_ref = str(getattr(ctx, "conversation_ref", "") or "").strip()
        if not session_id:
            return CapabilityResult(is_error=True, status="invalid_context", reason="conversation_identity_required")
        try:
            if clean_action in {"status", "list"}:
                return _schedule_result(store.list_active(session_id=session_id), summary="当前会话的定时事件。")
            if clean_action == "cancel":
                target = str(event_id or "").strip()
                if not target:
                    return CapabilityResult(is_error=True, status="invalid_input", reason="event_id_required")
                changed = store.cancel(event_id=target, session_id=session_id)
                item = store.status(event_id=target, session_id=session_id)
                return _schedule_result(
                    (item,) if item else (),
                    summary="已取消该定时事件。" if changed else "没有找到可取消的定时事件。",
                )
        except TimerStateError as exc:
            return CapabilityResult(is_error=True, status="unavailable", reason=exc.reason)
        if clean_action != "create":
            return CapabilityResult(is_error=True, status="invalid_input", reason="unsupported_action")
        if not conversation_ref:
            return CapabilityResult(is_error=True, status="invalid_context", reason="conversation_reference_required")
        if isinstance(delay_seconds, bool) or not isinstance(delay_seconds, int):
            return CapabilityResult(is_error=True, status="invalid_input", reason="delay_seconds_out_of_range")
        now = _now()
        absolute_due = _positive_int(due_at)
        if absolute_due:
            if absolute_due <= now or absolute_due - now > MAX_DELAY_SECONDS:
                return CapabilityResult(is_error=True, status="invalid_input", reason="due_at_out_of_range")
            computed_due = absolute_due
        elif MIN_DELAY_SECONDS <= delay_seconds <= MAX_DELAY_SECONDS:
            computed_due = now + delay_seconds
        else:
            return CapabilityResult(is_error=True, status="invalid_input", reason="delay_seconds_out_of_range")
        clean_text = str(event_text or "").strip()
        if not clean_text:
            return CapabilityResult(is_error=True, status="invalid_input", reason="event_text_required")
        if len(clean_text) > MAX_EVENT_TEXT_LENGTH:
            return CapabilityResult(is_error=True, status="invalid_input", reason="event_text_too_long")
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
            return CapabilityResult(is_error=True, status="unavailable", reason=exc.reason)
        return _schedule_result((item,), summary=f"已登记定时事件 {created_id}，将在 {computed_due} 触发。")

    return schedule_timer


def _schedule_result(items: tuple[dict[str, Any], ...], *, summary: str) -> CapabilityResult:
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
    return CapabilityResult(
        is_error=False,
        status="ok",
        content=PluginResultPayload(
            content={"events": events},
            experience=PluginResultExperience(
                summary=summary,
                facts=("创建成功表示事件已持久化；到期后由当前会话的普通 Agent 回合处理。",),
                suggested_next_actions=("查看当前定时事件", "取消定时事件"),
            ),
        ),
    )


class TimerCommandHandler:
    def __init__(self, store: TimerStore) -> None:
        self._store = store

    async def handle(self, request: PluginQQCommandRequest) -> PluginQQCommandResult:
        session_id = str(request.session_id or "").strip()
        if not session_id:
            return PluginQQCommandResult(True, "当前会话身份不可用。", "conversation_identity_required")
        parts = str(request.args or "").strip().split()
        action = parts[0].lower() if parts else "status"
        if action in {"create", "add"}:
            return await self._create(request, parts[1:])
        if action == "cancel":
            event_id = parts[1] if len(parts) > 1 else ""
            if not event_id:
                return PluginQQCommandResult(True, "用法：/timer cancel <事件ID>。", "event_id_required")
            try:
                changed = self._store.cancel(event_id=event_id, session_id=session_id)
            except TimerStateError as exc:
                return PluginQQCommandResult(True, "定时事件暂时不可用。", exc.reason)
            return PluginQQCommandResult(
                True,
                f"已取消定时事件 {event_id}。" if changed else "没有找到可取消的定时事件。",
                "" if changed else "not_found",
            )
        if action == "status":
            try:
                return PluginQQCommandResult(True, _status_text(self._store.list_active(session_id=session_id)))
            except TimerStateError as exc:
                return PluginQQCommandResult(True, "定时事件暂时不可用。", exc.reason)
        return PluginQQCommandResult(True, "用法：/timer create <秒> <事件内容>；/timer status；/timer cancel <事件ID>。", "invalid_command_args")

    async def _create(self, request: PluginQQCommandRequest, args: list[str]) -> PluginQQCommandResult:
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
            self._store.schedule(
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


def _status_text(items: tuple[dict[str, Any], ...]) -> str:
    if not items:
        return "当前会话没有进行中的定时事件。"
    lines = ["当前定时事件："]
    for item in items:
        lines.append(f"- {item.get('event_id')} 于 {_positive_int(item.get('due_at'))} 触发，状态 {item.get('status')}")
    return "\n".join(lines)


class TimerPlugin:
    manifest = PluginManifest(
        plugin_id=PLUGIN_ID,
        plugin_version=PLUGIN_VERSION,
        plugin_api_version=AKANE_PLUGIN_API_VERSION,
        permissions=(
            CAPABILITY_PROMPT_INVOKE_PERMISSION,
            PLUGIN_STORAGE_WRITE_PERMISSION,
            BACKGROUND_JOB_PERMISSION,
            AGENT_EVENT_SUBMIT_PERMISSION,
            PLUGIN_QQ_COMMAND_PERMISSION,
        ),
    )

    def register(self, registrar: PluginRegistrar) -> None:
        store = TimerStore(registrar.get_storage_dir())
        schedule = _schedule_capability(store)
        spec = PythonCapabilitySpec.from_callable(
            schedule,
            capability_id=CAPABILITY_ID,
            display_name="Schedule a one-shot event",
            short_hint="Create, inspect, or cancel a one-shot event for the current conversation.",
            visible_in=("desktop", "qq"),
            prompt_exposed=True,
            risk="low",
            confirm="never",
            effects=(PLUGIN_STATE_EFFECT,),
            call_mode="kwargs_with_context",
            inputs=(
                CapabilityIOSlot(name="action", kind="string", required=False, raw={"enum": ("create", "status", "cancel"), "default": "create"}),
                CapabilityIOSlot(name="delay_seconds", kind="integer", required=False, raw={"minimum": MIN_DELAY_SECONDS, "maximum": MAX_DELAY_SECONDS, "default": DEFAULT_DELAY_SECONDS}),
                CapabilityIOSlot(name="due_at", kind="integer", required=False, raw={"description": "Optional future Unix timestamp; overrides delay_seconds."}),
                CapabilityIOSlot(name="event_text", kind="string", required=False, raw={"description": "What should happen or be considered when the event becomes due."}),
                CapabilityIOSlot(name="event_id", kind="string", required=False, raw={"description": "Timer ID required only for cancel."}),
                CapabilityIOSlot(name="fields", kind="object", required=False, raw={"description": "Optional structured event facts."}),
            ),
            raw={"contract": "akane.timer.v2"},
        )
        registrar.add_capability_adapter(
            PythonCapabilityAdapter(provider_id="provider.akane.timer", capabilities=(spec,))
        )
        registrar.add_qq_command("/timer", TimerCommandHandler(store))
        registrar.add_background_service(
            SERVICE_ID,
            TimerService(store, registrar.get_agent_event_port()),
        )


def create_plugin() -> TimerPlugin:
    return TimerPlugin()


__all__ = [
    "CAPABILITY_ID",
    "DUE_EVENT_TYPE",
    "PLUGIN_ID",
    "PLUGIN_VERSION",
    "DueTimer",
    "TimerCommandHandler",
    "TimerPlugin",
    "TimerService",
    "TimerStateError",
    "TimerStore",
    "create_plugin",
]
