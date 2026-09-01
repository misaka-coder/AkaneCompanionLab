"""Mixed companion plugin using only Akane's public plugin ports."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from capcore import CapabilityIOSlot, CapabilityResult, InvocationContext
from capcore_adapter_python import PythonCapabilityAdapter, PythonCapabilitySpec

from companion_v01.plugin_api import (
    AKANE_PLUGIN_API_VERSION,
    BACKGROUND_JOB_PERMISSION,
    CAPABILITY_PROMPT_INVOKE_PERMISSION,
    DIRECT_CONVERSATION_EVENT,
    EVENT_SUBSCRIBE_PERMISSION,
    GROUP_CONVERSATION_EVENT,
    MODEL_REASONING_PERMISSION,
    NOTIFICATION_SEND_PERMISSION,
    PLUGIN_QQ_COMMAND_PERMISSION,
    PLUGIN_STATE_EFFECT,
    PLUGIN_STORAGE_WRITE_PERMISSION,
    SKILL_CONTRIBUTION_PERMISSION,
    NotificationIntent,
    PluginEventEnvelope,
    PluginEventResult,
    PluginExternalEvent,
    PluginManifest,
    PluginQQCommandRequest,
    PluginQQCommandResult,
    PluginReasoningRequest,
    PluginRegistrar,
    PluginResultExperience,
    PluginResultPayload,
)


PLUGIN_ID = "akane.sample.gentle-checkin"
PLUGIN_VERSION = "0.1.0"
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
    profile_user_id: str
    session_id: str
    character_pack_id: str
    recipient_id: str
    conversation_kind: str
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
        profile_user_id: str,
        session_id: str,
        character_pack_id: str,
        recipient_id: str,
        conversation_kind: str,
        idle_seconds: int,
    ) -> dict[str, Any]:
        now = self._clock()
        key = _subscription_key(session_id)
        with self._lock:
            state = self._read_locked()
            subscriptions = state.setdefault("subscriptions", {})
            subscriptions[key] = {
                "profile_user_id": profile_user_id,
                "session_id": session_id,
                "character_pack_id": character_pack_id,
                "recipient_id": recipient_id,
                "conversation_kind": conversation_kind,
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
                profile_user_id = str(item.get("profile_user_id") or "").strip()
                session_id = str(item.get("session_id") or "").strip()
                recipient_id = str(item.get("recipient_id") or "").strip()
                if not profile_user_id or not session_id or not recipient_id:
                    continue
                item["last_attempt_at"] = current
                item["last_status"] = "reasoning"
                changed = True
                due.append(
                    DueCheckin(
                        key=str(key),
                        profile_user_id=profile_user_id,
                        session_id=session_id,
                        character_pack_id=str(item.get("character_pack_id") or ""),
                        recipient_id=recipient_id,
                        conversation_kind=str(item.get("conversation_kind") or "direct"),
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


class CheckinEventHandler:
    def __init__(self, store: CheckinStore) -> None:
        self._store = store

    async def handle_event(self, event: PluginEventEnvelope) -> PluginEventResult:
        if event.event_type not in {DIRECT_CONVERSATION_EVENT, GROUP_CONVERSATION_EVENT}:
            return PluginEventResult()
        subject = str(event.subject or "").strip()
        event_id = str(event.event_id or "").strip()
        if subject and event_id:
            try:
                self._store.mark_activity(
                    session_id=subject,
                    event_id=event_id,
                    occurred_at=_positive_int(event.occurred_at, default=_now()),
                )
            except CheckinStateError as exc:
                return PluginEventResult(reason=exc.reason)
        return PluginEventResult()


class CheckinService:
    def __init__(
        self,
        store: CheckinStore,
        reasoning_port: Any,
        notification_port: Any,
        *,
        poll_seconds: float = POLL_SECONDS,
    ) -> None:
        self._store = store
        self._reasoning = reasoning_port
        self._notifications = notification_port
        self._poll_seconds = max(0.01, float(poll_seconds))
        self._stop = asyncio.Event()

    async def start(self, controller: Any) -> None:
        while not controller.shutdown_requested and not self._stop.is_set():
            for due in self._store.claim_due():
                if controller.shutdown_requested or self._stop.is_set():
                    return
                await self._process(due)
            if await controller.wait_for_shutdown(timeout=self._poll_seconds):
                return

    async def stop(self) -> None:
        self._stop.set()

    async def _process(self, due: DueCheckin) -> None:
        trace = hashlib.sha256(f"{due.key}:{due.activity_at}".encode("utf-8")).hexdigest()[:24]
        result = await self._reasoning.analyze(
            PluginReasoningRequest(
                trace_id=f"gentle-checkin-{trace}",
                profile_user_id=due.profile_user_id,
                session_id=due.session_id,
                character_pack_id=due.character_pack_id,
                timestamp=_now(),
                message="A configured gentle check-in is due for this quiet conversation.",
                extra_context=(
                    "结合这段会话已经存在的记忆，以当前角色自然地发一句简短关心；"
                    "不要提插件、计时器、配置、系统事件或内部字段。"
                ),
                memory_idempotency_key=f"{PLUGIN_ID}:{trace}",
                external_event=PluginExternalEvent(
                    event_type="companion.gentle_checkin_due",
                    source=PLUGIN_ID,
                    fields=(
                        ("conversation_kind", due.conversation_kind),
                        ("quiet_seconds", str(due.idle_seconds)),
                        ("delivery_purpose", "gentle_checkin"),
                    ),
                ),
            )
        )
        if not result.ok or not result.text.strip():
            self._store.finish(due, delivered=False, status=result.reason or result.status)
            return
        if not self._store.is_current(due):
            self._store.finish(due, delivered=False, status="superseded_by_activity")
            return
        delivered = await self._notifications.send(
            NotificationIntent(
                channel="qq_text",
                recipient_id=due.recipient_id,
                text=result.text.strip(),
                idempotency_key=f"gentle-checkin:{trace}",
            )
        )
        self._store.finish(
            due,
            delivered=bool(delivered.ok and delivered.status in {"delivered", "queued", "already_delivered"}),
            status=delivered.reason or delivered.status,
        )


class CheckinCommandHandler:
    def __init__(self, store: CheckinStore) -> None:
        self._store = store

    async def handle(self, request: PluginQQCommandRequest) -> PluginQQCommandResult:
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
            recipient_id = f"group:{request.group_id}" if request.is_group else f"user:{request.qq_number}"
            try:
                self._store.configure(
                    profile_user_id=request.profile_user_id or str(request.qq_number),
                    session_id=request.session_id,
                    character_pack_id=request.character_pack_id,
                    recipient_id=recipient_id,
                    conversation_kind="group" if request.is_group else "direct",
                    idle_seconds=minutes * 60,
                )
            except CheckinStateError as exc:
                return PluginQQCommandResult(True, "温和问候配置暂时不可用。", exc.reason)
            return PluginQQCommandResult(True, f"已开启：本会话安静 {minutes} 分钟后，我会自然问候一次。")
        if action in {"off", "disable"}:
            if request.is_group and request.sender_role not in {"owner", "admin"}:
                return PluginQQCommandResult(True, "只有群主或管理员可以关闭本群温和问候。", "group_admin_required")
            try:
                changed = self._store.disable(
                    profile_user_id=request.profile_user_id or str(request.qq_number),
                    session_id=request.session_id,
                )
            except CheckinStateError as exc:
                return PluginQQCommandResult(True, "温和问候配置暂时不可用。", exc.reason)
            return PluginQQCommandResult(True, "已关闭本会话的温和问候。" if changed else "本会话尚未开启温和问候。")
        if action == "status":
            try:
                item = self._store.status(
                    profile_user_id=request.profile_user_id or str(request.qq_number),
                    session_id=request.session_id,
                )
            except CheckinStateError as exc:
                return PluginQQCommandResult(True, "温和问候配置暂时不可用。", exc.reason)
            return PluginQQCommandResult(True, _status_text(item))
        return PluginQQCommandResult(True, "用法：/checkin on [分钟]、/checkin status、/checkin off。", "invalid_command_args")


def _status_text(item: dict[str, Any] | None) -> str:
    if not item:
        return "本会话尚未配置温和问候。"
    enabled = bool(item.get("enabled"))
    minutes = max(1, _positive_int(item.get("idle_seconds"), default=60) // 60)
    state = "已开启" if enabled else "已关闭"
    return f"温和问候{state}，静默间隔 {minutes} 分钟；每段静默最多问候一次。"


def _configure_capability(store: CheckinStore) -> Callable[..., CapabilityResult]:
    def configure(
        action: str = "status",
        idle_minutes: int = DEFAULT_IDLE_MINUTES,
        recipient_qq_number: str = "",
        *,
        ctx: InvocationContext,
    ) -> CapabilityResult:
        clean_action = str(action or "status").strip().lower()
        profile_user_id = str(ctx.profile_user_id or "").strip()
        session_id = str(ctx.session_id or "").strip()
        if not profile_user_id or not session_id:
            return CapabilityResult(is_error=True, status="invalid_context", reason="conversation_identity_required")
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
            return CapabilityResult(is_error=True, status="unavailable", reason=exc.reason)
        if clean_action != "enable":
            return CapabilityResult(is_error=True, status="invalid_input", reason="unsupported_action")
        if isinstance(idle_minutes, bool) or not isinstance(idle_minutes, int) or not MIN_IDLE_MINUTES <= idle_minutes <= MAX_IDLE_MINUTES:
            return CapabilityResult(is_error=True, status="invalid_input", reason="idle_minutes_out_of_range")
        qq_number = str(recipient_qq_number or "").strip()
        if not qq_number.isdigit() or qq_number == "0":
            return CapabilityResult(is_error=True, status="invalid_input", reason="private_qq_number_required")
        try:
            item = store.configure(
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id="",
                recipient_id=f"user:{qq_number}",
                conversation_kind="direct",
                idle_seconds=idle_minutes * 60,
            )
        except CheckinStateError as exc:
            return CapabilityResult(is_error=True, status="unavailable", reason=exc.reason)
        return _configuration_result(item, summary=f"已开启：安静 {idle_minutes} 分钟后自然问候一次。")

    return configure


def _configuration_result(item: dict[str, Any] | None, *, summary: str) -> CapabilityResult:
    content = {
        "configured": bool(item),
        "enabled": bool(item and item.get("enabled")),
        "idle_minutes": (
            max(1, _positive_int(item.get("idle_seconds"), default=60) // 60)
            if item
            else 0
        ),
        "conversation_kind": str(item.get("conversation_kind") or "") if item else "",
        "last_status": str(item.get("last_status") or "") if item else "",
    }
    return CapabilityResult(
        is_error=False,
        status="ok",
        content=PluginResultPayload(
            content=content,
            experience=PluginResultExperience(
                summary=summary,
                facts=("每段静默最多主动问候一次；新消息会重新开始计时。",),
                suggested_next_actions=("查看当前状态", "关闭温和问候"),
            ),
        ),
    )


class GentleCheckinPlugin:
    manifest = PluginManifest(
        plugin_id=PLUGIN_ID,
        plugin_version=PLUGIN_VERSION,
        plugin_api_version=AKANE_PLUGIN_API_VERSION,
        permissions=(
            CAPABILITY_PROMPT_INVOKE_PERMISSION,
            PLUGIN_STORAGE_WRITE_PERMISSION,
            BACKGROUND_JOB_PERMISSION,
            NOTIFICATION_SEND_PERMISSION,
            MODEL_REASONING_PERMISSION,
            PLUGIN_QQ_COMMAND_PERMISSION,
            EVENT_SUBSCRIBE_PERMISSION,
            SKILL_CONTRIBUTION_PERMISSION,
        ),
    )

    def register(self, registrar: PluginRegistrar) -> None:
        store = CheckinStore(registrar.get_storage_dir())
        reasoning = registrar.get_reasoning_port()
        notifications = registrar.get_notification_port()
        configure = _configure_capability(store)
        spec = PythonCapabilitySpec.from_callable(
            configure,
            capability_id=CAPABILITY_ID,
            display_name="Configure gentle check-in",
            short_hint="Enable, disable, or inspect one quiet-conversation check-in for the current private QQ chat.",
            visible_in=("qq",),
            prompt_exposed=True,
            risk="low",
            confirm="never",
            effects=(PLUGIN_STATE_EFFECT,),
            call_mode="kwargs_with_context",
            inputs=(
                CapabilityIOSlot(
                    name="action",
                    kind="string",
                    required=False,
                    raw={"enum": ("status", "enable", "disable"), "default": "status"},
                ),
                CapabilityIOSlot(
                    name="idle_minutes",
                    kind="integer",
                    required=False,
                    raw={"minimum": MIN_IDLE_MINUTES, "maximum": MAX_IDLE_MINUTES, "default": DEFAULT_IDLE_MINUTES},
                ),
                CapabilityIOSlot(
                    name="recipient_qq_number",
                    kind="string",
                    required=False,
                    raw={"description": "Numeric QQ ID of the private-chat recipient; required only for enable."},
                ),
            ),
            raw={"contract": "akane.sample.gentle-checkin.v1"},
        )
        registrar.add_capability_adapter(
            PythonCapabilityAdapter(
                provider_id="provider.akane.sample.gentle-checkin",
                capabilities=(spec,),
            )
        )
        handler = CheckinEventHandler(store)
        registrar.add_event_handler(DIRECT_CONVERSATION_EVENT, handler)
        registrar.add_event_handler(GROUP_CONVERSATION_EVENT, handler)
        registrar.add_qq_command("/checkin", CheckinCommandHandler(store))
        registrar.add_background_service(
            SERVICE_ID,
            CheckinService(store, reasoning, notifications),
        )
        registrar.add_skill(Path(__file__).resolve().parent / "skills" / "gentle-checkin")


def create_plugin() -> GentleCheckinPlugin:
    return GentleCheckinPlugin()


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
    "CheckinCommandHandler",
    "CheckinEventHandler",
    "CheckinService",
    "CheckinStore",
    "CheckinStateError",
    "DueCheckin",
    "GentleCheckinPlugin",
    "create_plugin",
]
