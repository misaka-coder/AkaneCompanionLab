"""Akane plugin: 定时唤醒 (scheduled wake-up).

后台服务本身没有会话身份，所以它不直接向宿主请求对话。这里的路是：

1. 登记发生在一次真实对话里，那时把本会话的事件订阅绑定下来，拿到宿主签发的 binding；
2. 到点由后台服务发布一条事件；
3. 事件投递回被绑定的会话，处理器在那个会话的签名身份下请求一次正常对话轮次。

不另起调度器、不直连渠道、不写宿主存储以外的东西，也不改宿主源码。
"""

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
    Plugin,
    PluginQQCommandRequest,
    PluginQQCommandResult,
    Result,
    ToolContext,
)

PLUGIN_ID = "akane.scheduled-wakeup"
PLUGIN_VERSION = "0.2.0"
SERVICE_ID = "timer"
CAPABILITY_NAME = "wakeup.v1"
STATE_FILE = "timers.json"

# 本插件自己的事件类型与对话级订阅。绑定只能发生在有会话身份的调用里。
WAKEUP_EVENT = "akane.scheduled-wakeup.due"
SUBSCRIPTION_NAME = "due"
SUBSCRIPTION_ID = f"{PLUGIN_ID}.{SUBSCRIPTION_NAME}"

DEFAULT_DELAY_MINUTES = 10
MIN_DELAY_MINUTES = 1
MAX_DELAY_MINUTES = 24 * 60
MAX_NOTE_CHARS = 200

POLL_SECONDS = 10.0
VERIFY_SECONDS = 15.0
VERIFY_STEP_SECONDS = 0.5
FAILED_RETRY_SECONDS = 60
# 超过这个时长才被发现的过期定时不再补唤醒，避免主机重启后突然诈尸。
STALE_GRACE_SECONDS = 3600

# 宿主明确拒绝这次请求才算没叫到；其余一律不重复叫第二遍。
REFUSED_TURN_STATUSES = frozenset({"rejected", "failed", "cancelled", "stale"})


def _now() -> int:
    return int(time.time())


def _conversation_key(invocation: Any, fallback: str = "") -> str:
    """Hash of the host-supplied identity; only ever used as a local ledger key."""

    parts = [
        str(getattr(invocation, "profile_user_id", "") or ""),
        str(getattr(invocation, "session_id", "") or ""),
        str(getattr(invocation, "character_pack_id", "") or ""),
    ]
    material = "\x1f".join(parts)
    if not material.strip("\x1f"):
        material = str(fallback or "")
    return hashlib.sha256(material.encode("utf-8", errors="strict")).hexdigest()


def _positive_int(value: object, *, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _duration_text(seconds: int) -> str:
    seconds = max(1, _positive_int(seconds, default=60))
    minutes = max(1, seconds // 60)
    if minutes >= 60 and minutes % 60 == 0:
        return f"{minutes // 60} 小时"
    return f"{minutes} 分钟"


@dataclass(frozen=True)
class DueTimer:
    key: str
    binding: str
    due_at: int
    note: str
    late_seconds: int


class TimerStateError(RuntimeError):
    """Raised when the plugin-scoped ledger cannot be read or written."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class TimerStore:
    """Atomic JSON ledger kept inside the plugin's own storage directory."""

    def __init__(self, storage_dir: Path, *, clock: Callable[[], int] = _now) -> None:
        self._path = Path(storage_dir) / STATE_FILE
        self._clock = clock
        self._lock = threading.RLock()

    # -- write paths ---------------------------------------------------------

    def schedule(self, *, key: str, binding: str, delay_seconds: int, note: str) -> dict[str, Any]:
        now = self._clock()
        with self._lock:
            state = self._read_locked()
            timers = state.setdefault("timers", {})
            timers[key] = {
                "binding": binding,
                "delay_seconds": delay_seconds,
                "note": note,
                "due_at": now + delay_seconds,
                "created_at": now,
                "enabled": True,
                "fired_at": 0,
                "expired_at": 0,
                "last_attempt_at": 0,
                "retry_after": 0,
                "attempts": 0,
                "last_status": "scheduled",
            }
            self._write_locked(state)
            return dict(timers[key])

    def set_binding(self, *, key: str, binding: str) -> bool:
        """Point this conversation's pending timer at a freshly bound subscription."""

        with self._lock:
            state = self._read_locked()
            item = state.get("timers", {}).get(key)
            if not isinstance(item, dict) or not item.get("enabled"):
                return False
            if _positive_int(item.get("fired_at")) or _positive_int(item.get("expired_at")):
                return False
            if str(item.get("binding") or "") == binding:
                return False
            item["binding"] = binding
            self._write_locked(state)
            return True

    def cancel(self, *, key: str) -> bool:
        with self._lock:
            state = self._read_locked()
            item = state.get("timers", {}).get(key)
            if not isinstance(item, dict) or not item.get("enabled"):
                return False
            item["enabled"] = False
            item["last_status"] = "cancelled"
            self._write_locked(state)
            return True

    def finish(self, due: DueTimer, *, delivered: bool, status: str) -> None:
        with self._lock:
            state = self._read_locked()
            item = state.get("timers", {}).get(due.key)
            if not isinstance(item, dict) or _positive_int(item.get("due_at")) != due.due_at:
                return
            item["last_attempt_at"] = self._clock()
            item["attempts"] = _positive_int(item.get("attempts")) + 1
            item["last_status"] = str(status or "unknown")[:120]
            if delivered:
                item["fired_at"] = due.due_at
                item["retry_after"] = 0
            else:
                item["retry_after"] = self._clock() + FAILED_RETRY_SECONDS
            self._write_locked(state)

    def mark_expired(self, due: DueTimer) -> None:
        with self._lock:
            state = self._read_locked()
            item = state.get("timers", {}).get(due.key)
            if not isinstance(item, dict) or _positive_int(item.get("due_at")) != due.due_at:
                return
            item["expired_at"] = due.due_at
            item["last_status"] = "expired"
            self._write_locked(state)

    def clear(self, *, key: str) -> bool:
        with self._lock:
            state = self._read_locked()
            timers = state.get("timers", {})
            if key not in timers:
                return False
            del timers[key]
            self._write_locked(state)
            return True

    # -- read paths ----------------------------------------------------------

    def now(self) -> int:
        """Current time from this ledger's own clock; the single time source."""

        return self._clock()

    def status(self, *, key: str) -> dict[str, Any] | None:
        with self._lock:
            item = self._read_locked().get("timers", {}).get(key)
            return dict(item) if isinstance(item, dict) else None

    def is_current(self, due: DueTimer) -> bool:
        item = self.status(key=due.key)
        if not isinstance(item, dict) or not item.get("enabled"):
            return False
        if _positive_int(item.get("due_at")) != due.due_at:
            return False
        return not _positive_int(item.get("fired_at")) and not _positive_int(item.get("expired_at"))

    def claim_due(self, *, now: int | None = None) -> tuple[DueTimer, ...]:
        """Take ownership of every timer whose moment has arrived."""

        current = _positive_int(now, default=self._clock())
        due: list[DueTimer] = []
        with self._lock:
            state = self._read_locked()
            for key, item in state.get("timers", {}).items():
                if not isinstance(item, dict) or not item.get("enabled"):
                    continue
                if _positive_int(item.get("fired_at")) or _positive_int(item.get("expired_at")):
                    continue
                due_at = _positive_int(item.get("due_at"))
                if due_at <= 0 or due_at > current:
                    continue
                if _positive_int(item.get("retry_after")) > current:
                    continue
                due.append(DueTimer(
                    key=str(key),
                    binding=str(item.get("binding") or ""),
                    due_at=due_at,
                    note=str(item.get("note") or ""),
                    late_seconds=max(0, current - due_at),
                ))
        return tuple(sorted(due, key=lambda entry: entry.due_at))

    # -- internals -----------------------------------------------------------

    def _read_locked(self) -> dict[str, Any]:
        if not self._path.exists():
            return {"timers": {}}
        try:
            raw = self._path.read_text(encoding="utf-8")
        except OSError as exc:
            raise TimerStateError("state_read_failed") from exc
        if not raw.strip():
            return {"timers": {}}
        try:
            payload = json.loads(raw)
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


# -- presentation helpers ----------------------------------------------------


def _status_text(item: dict[str, Any] | None, *, now: int | None = None) -> str:
    if not item:
        return "本会话还没有设定定时唤醒。"
    current = _positive_int(now, default=_now())
    due_at = _positive_int(item.get("due_at"))
    note = str(item.get("note") or "")
    fired = _positive_int(item.get("fired_at")) == due_at and due_at > 0
    expired = _positive_int(item.get("expired_at")) == due_at and due_at > 0
    if fired:
        return "上一次定时唤醒已经响过了，现在没有待命的定时。"
    if expired:
        return "上一个定时已经过期作废，现在没有待命的定时。"
    if not item.get("enabled"):
        return "定时唤醒已取消。"
    remaining = due_at - current
    suffix = f"；到点要说的事：{note}" if note else ""
    if remaining <= 0:
        last_status = str(item.get("last_status") or "")
        if last_status == "conversation_not_bound":
            return f"已到点，但这个会话还没登记到唤醒通道；在对话里重新说一次定时就会绑好{suffix}"
        if last_status and last_status not in {"scheduled", "dispatched"}:
            return f"已到点，但上一次叫醒没成功（{last_status}），还在重试{suffix}"
        return f"定时已到点，正在唤醒中{suffix}"
    return f"已设定：约 {_duration_text(remaining)} 后叫醒一次{suffix}"


def _snapshot(item: dict[str, Any] | None, *, now: int | None = None) -> dict[str, Any]:
    current = _positive_int(now, default=_now())
    if not item:
        return {
            "pending": False,
            "enabled": False,
            "due_at": 0,
            "remaining_seconds": 0,
            "note": "",
            "bound": False,
            "attempts": 0,
            "last_status": "",
        }
    due_at = _positive_int(item.get("due_at"))
    fired = _positive_int(item.get("fired_at")) == due_at and due_at > 0
    expired = _positive_int(item.get("expired_at")) == due_at and due_at > 0
    pending = bool(item.get("enabled")) and not fired and not expired
    return {
        "pending": pending,
        "enabled": bool(item.get("enabled")),
        "due_at": due_at,
        "remaining_seconds": max(0, due_at - current) if pending else 0,
        "note": str(item.get("note") or ""),
        "bound": bool(str(item.get("binding") or "").strip()),
        "attempts": _positive_int(item.get("attempts")),
        "last_status": str(item.get("last_status") or ""),
    }


def _ok(item: dict[str, Any] | None, *, now: int, summary: str) -> Result:
    return Result(value=_snapshot(item, now=now), content=summary, status="ok")


def _turn_accepted(receipt: Any) -> bool:
    """False only when the host explicitly refused this wake-up request."""

    status = str(getattr(receipt, "status", "") or "").strip().lower()
    return status not in REFUSED_TURN_STATUSES


def _deliveries(receipt: Any) -> tuple[Any, ...]:
    value = getattr(receipt, "deliveries", ()) or ()
    try:
        return tuple(value)
    except TypeError:
        return ()


def _verdict(receipt: Any, binding: str) -> tuple[bool, str]:
    """Read one event receipt as: was this conversation really woken?

    ``unconfirmed`` means the host has not finished delivering yet, so the
    caller should keep polling instead of concluding anything.
    """

    deliveries = _deliveries(receipt)
    if not deliveries:
        return False, "conversation_not_bound"
    match = next((item for item in deliveries if str(getattr(item, "scope_id", "") or "") == binding), None)
    if match is None:
        return False, "subscription_binding_stale"
    status = str(getattr(match, "status", "") or "")
    value = getattr(match, "value", None)
    if isinstance(value, dict) and str(value.get("outcome") or "") == "other_conversation":
        return False, "subscription_binding_stale"
    if status == "failed":
        return False, str(getattr(match, "reason", "") or "event_delivery_failed")
    if status == "completed":
        inner = value.get("receipt") if isinstance(value, dict) else None
        if isinstance(inner, dict):
            inner_status = str(inner.get("status") or "").strip().lower()
            if inner_status in REFUSED_TURN_STATUSES or not str(inner.get("request_id") or ""):
                return False, str(inner.get("reason") or "turn_request_rejected")
            return True, "turn_requested"
        return True, "delivered"
    if status in {"queued", "running", "waiting"}:
        return False, "unconfirmed"
    return False, status or "unknown"


async def _ensure_binding(ctx: Any) -> tuple[str, str]:
    """Bind this conversation's subscription; without it nothing can be woken."""

    events = getattr(ctx, "events", None)
    if events is None:
        return "", "event_port_unavailable"
    try:
        binding = await events.bind(SUBSCRIPTION_ID)
    except Exception:
        return "", "event_binding_failed"
    status = str(getattr(binding, "status", "") or "")
    scope_id = str(getattr(binding, "scope_id", "") or "")
    if status != "bound" or not scope_id:
        return "", str(getattr(binding, "reason", "") or "event_binding_rejected")
    return scope_id, ""


# -- contributions -----------------------------------------------------------


def _wakeup_tool(store_of):
    """One model-facing tool: schedule, inspect, cancel, or test a wake-up."""

    async def wakeup(
        action: str = "status",
        delay_minutes: int = DEFAULT_DELAY_MINUTES,
        note: str = "",
        ctx: ToolContext = None,
    ) -> Result:
        """Schedule a wake-up for this conversation, or inspect and cancel it."""

        store = store_of()
        if store is None:
            return Result(is_error=True, status="unavailable", reason="storage_not_ready")
        invocation = getattr(ctx, "invocation", None)
        session_id = str(getattr(invocation, "session_id", "") or "").strip()
        if not session_id:
            return Result(is_error=True, status="invalid_context", reason="conversation_identity_required")
        key = _conversation_key(invocation, session_id)
        clean_action = str(action or "status").strip().lower()

        binding, binding_reason = await _ensure_binding(ctx)
        try:
            if binding:
                store.set_binding(key=key, binding=binding)
            if clean_action == "status":
                item = store.status(key=key)
                return _ok(item, now=store.now(), summary=_status_text(item, now=store.now()))
            if clean_action in {"cancel", "disable", "off"}:
                changed = store.cancel(key=key)
                item = store.status(key=key)
                return _ok(
                    item,
                    now=store.now(),
                    summary="已取消定时唤醒。" if changed else "没有可取消的定时唤醒。",
                )
            if clean_action in {"test", "run_now", "now"}:
                if not binding:
                    return Result(is_error=True, status="invalid_context",
                                  reason=binding_reason or "conversation_binding_required")
                receipt = await ctx.events.emit(
                    WAKEUP_EVENT,
                    {
                        "binding": binding,
                        "note": str(note or "")[:MAX_NOTE_CHARS],
                        "due_at": store.now(),
                        "late_seconds": 0,
                        "test": True,
                    },
                    event_key=f"{PLUGIN_ID}:test:{key}:{store.now()}",
                )
                delivered, status = _verdict(receipt, binding)
                return Result(
                    value={
                        "wakeup_requested": bool(delivered),
                        "status": status,
                        "dispatch_status": str(getattr(receipt, "status", "") or ""),
                    },
                    content="测试唤醒已提交。" if delivered else f"测试唤醒没能提交（{status}）。",
                    status="ok",
                )
        except TimerStateError as exc:
            return Result(is_error=True, status="unavailable", reason=exc.reason)
        if clean_action not in {"schedule", "set", "on"}:
            return Result(is_error=True, status="invalid_input", reason="unsupported_action")
        if (
            isinstance(delay_minutes, bool)
            or not isinstance(delay_minutes, int)
            or not MIN_DELAY_MINUTES <= delay_minutes <= MAX_DELAY_MINUTES
        ):
            return Result(is_error=True, status="invalid_input", reason="delay_minutes_out_of_range")
        if not binding:
            return Result(is_error=True, status="invalid_context",
                          reason=binding_reason or "conversation_binding_required")
        clean_note = str(note or "").strip()[:MAX_NOTE_CHARS]
        try:
            item = store.schedule(
                key=key,
                binding=binding,
                delay_seconds=delay_minutes * 60,
                note=clean_note,
            )
        except TimerStateError as exc:
            return Result(is_error=True, status="unavailable", reason=exc.reason)
        return _ok(item, now=store.now(), summary=f"已设定：{delay_minutes} 分钟后叫醒一次。")

    wakeup.__name__ = CAPABILITY_NAME
    wakeup.__doc__ = (
        "Set, inspect or cancel a scheduled wake-up for the current conversation. "
        "Use action=schedule with delay_minutes for a one-shot wake-up; "
        "action=status reports it; action=cancel removes it; action=test wakes now."
    )
    return wakeup


def _wakeup_handler():
    """Deliver a due wake-up inside the conversation that owns the binding.

    The publication is routed to every bound conversation, so each handler
    claims only the wake-up addressed to its own binding. Claiming is done by
    returning a plain mapping: the turn this handler requests is already the
    real wake-up, so the delivery must not wait for the whole model turn.
    """

    async def on_wakeup(event, ctx):
        data = event.data if isinstance(event.data, dict) else {}
        target = str(data.get("binding") or "")
        mine = str(getattr(ctx, "scope_id", "") or "")
        if target and mine and target != mine:
            return {"outcome": "other_conversation"}
        receipt = await ctx.request_turn(
            f"event:{WAKEUP_EVENT}",
            {
                "event_type": WAKEUP_EVENT,
                "source": PLUGIN_ID,
                "note": str(data.get("note") or ""),
                "due_at": data.get("due_at"),
                "late_seconds": data.get("late_seconds"),
                "test": bool(data.get("test")),
            },
            coalesce_key=f"{SUBSCRIPTION_ID}:{mine or target}",
        )
        return {"outcome": "requested", "receipt": receipt.as_dict()}

    return on_wakeup


def _timer_command(store_of):
    """Inspect or cancel from QQ without going through the model.

    Setting a timer needs the conversation's subscription binding, and a QQ
    command has no tool-context ports to bind with. So setting is handed to the
    normal turn (handled=False) where the tool can bind it properly.
    """

    async def timer_command(request: PluginQQCommandRequest) -> PluginQQCommandResult:
        store = store_of()
        if store is None:
            return PluginQQCommandResult(True, "定时唤醒目前不可用。", "storage_not_ready")
        invocation = request
        session_id = str(getattr(invocation, "session_id", "") or "").strip()
        if not session_id:
            return PluginQQCommandResult(True, "当前会话身份不可用，没有写入。", "conversation_identity_required")
        key = _conversation_key(invocation, session_id)
        parts = str(request.args or "").strip().split()
        action = parts[0].lower() if parts else "status"
        try:
            if action in {"off", "cancel", "stop"}:
                changed = store.cancel(key=key)
                return PluginQQCommandResult(True, "已取消定时唤醒。" if changed else "没有可取消的定时唤醒。")
            if action == "status":
                return PluginQQCommandResult(True, _status_text(store.status(key=key), now=store.now()))
            if action in {"clear", "forget"}:
                store.clear(key=key)
                return PluginQQCommandResult(True, "已清除本会话的定时记录。")
        except TimerStateError as exc:
            return PluginQQCommandResult(True, "定时唤醒目前不可用。", exc.reason)
        if action.isdigit() or action in {"on", "set", "schedule"}:
            # Let the normal turn handle it: only there can the subscription bind.
            return PluginQQCommandResult(False, "", "defer_to_turn_for_binding")
        return PluginQQCommandResult(
            True,
            "用法：/timer 10 想被提醒的事 ｜ /timer status ｜ /timer off",
            "invalid_command_args",
        )

    return timer_command


async def _dispatch(ctx, due: DueTimer) -> tuple[bool, str]:
    """Publish one due event and wait a bounded time for the real verdict."""

    receipt = await ctx.events.emit(
        WAKEUP_EVENT,
        {
            "binding": due.binding,
            "note": due.note,
            "due_at": due.due_at,
            "late_seconds": due.late_seconds,
            "source": PLUGIN_ID,
        },
        event_key=f"{PLUGIN_ID}:{due.key}:{due.due_at}",
    )
    latest = receipt
    deadline = time.monotonic() + VERIFY_SECONDS
    while True:
        delivered, status = _verdict(latest, due.binding)
        if status != "unconfirmed":
            return delivered, status
        if time.monotonic() >= deadline:
            # 已经投递到本会话、只是还没收敛：不重复叫第二遍。
            return True, "turn_requested_unconfirmed"
        if not await ctx.sleep(VERIFY_STEP_SECONDS):
            return True, "turn_requested_unconfirmed"
        dispatch_id = str(getattr(latest, "dispatch_id", "") or "")
        if not dispatch_id:
            return False, status
        try:
            latest = await ctx.events.status(dispatch_id)
        except Exception:
            return True, "turn_requested_unconfirmed"


def _timer_service(store_of):
    """Supervised loop: publish a due event; the bound conversation wakes itself."""

    async def timer_service(ctx) -> None:
        store = store_of()
        if store is None:
            return
        while not ctx.shutdown_requested:
            for due in store.claim_due():
                if ctx.shutdown_requested:
                    return
                if not store.is_current(due):
                    continue
                if due.late_seconds > STALE_GRACE_SECONDS:
                    store.mark_expired(due)
                    continue
                if not due.binding:
                    store.finish(due, delivered=False, status="conversation_not_bound")
                    continue
                try:
                    delivered, status = await _dispatch(ctx, due)
                except Exception:
                    store.finish(due, delivered=False, status="event_emit_failed")
                    continue
                store.finish(due, delivered=delivered, status=status)
            if not await ctx.sleep(POLL_SECONDS):
                return

    return timer_service


def create_plugin() -> Plugin:
    """Build the plugin instance with its own ledger and declared contributions."""

    store_holder: dict[str, TimerStore] = {}

    def setup(registrar) -> None:
        store_holder["store"] = TimerStore(registrar.get_storage_dir())

    plugin = Plugin(
        PLUGIN_ID,
        version=PLUGIN_VERSION,
        permissions=("event.emit", "storage.write", "agent.turn.request"),
        setup=setup,
    )
    store_of = lambda: store_holder.get("store")  # noqa: E731 - resolved after setup runs
    plugin.tool(_wakeup_tool(store_of), name=CAPABILITY_NAME, effects=("plugin_state",), visible_in=("qq",))
    plugin.on(WAKEUP_EVENT, name=SUBSCRIPTION_NAME, scope="conversation")(_wakeup_handler())
    plugin.qq_command("/timer")(_timer_command(store_of))
    plugin.background(SERVICE_ID)(_timer_service(store_of))
    return plugin


__all__ = [
    "CAPABILITY_NAME",
    "DEFAULT_DELAY_MINUTES",
    "FAILED_RETRY_SECONDS",
    "MAX_DELAY_MINUTES",
    "MAX_NOTE_CHARS",
    "MIN_DELAY_MINUTES",
    "PLUGIN_ID",
    "PLUGIN_VERSION",
    "POLL_SECONDS",
    "REFUSED_TURN_STATUSES",
    "SERVICE_ID",
    "STALE_GRACE_SECONDS",
    "SUBSCRIPTION_ID",
    "SUBSCRIPTION_NAME",
    "VERIFY_SECONDS",
    "WAKEUP_EVENT",
    "DueTimer",
    "TimerStateError",
    "TimerStore",
    "_status_text",
    "_timer_command",
    "_verdict",
    "create_plugin",
]
