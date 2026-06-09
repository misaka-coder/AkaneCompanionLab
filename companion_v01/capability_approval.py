from __future__ import annotations

import re
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping


APPROVAL_REQUEST_ID_RE = re.compile(r"^approvalreq_[a-f0-9]{32}$")
APPROVAL_GRANT_ID_RE = re.compile(r"^approvalgrant_[a-f0-9]{32}$")
APPROVAL_REQUEST_TTL_MIN_SECONDS = 30
APPROVAL_REQUEST_TTL_MAX_SECONDS = 900
APPROVAL_GRANT_TTL_SECONDS = 120
APPROVAL_SECRET_MARKERS = (
    "api_key",
    "apikey",
    "authorization",
    "bearer",
    "cookie",
    "password",
    "secret",
    "token",
)
APPROVAL_SAFE_KEY_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
LOCAL_PATH_RE = re.compile(
    r"(?i)([A-Z]:[\\/][^\s,;]+|\\\\[^\s,;]+|/(?:users|home|root|var|tmp|mnt|Volumes)/[^\s,;]+)"
)


class CapabilityApprovalStore:
    def __init__(self, *, now_func: Callable[[], datetime] | None = None) -> None:
        self._now_func = now_func or (lambda: datetime.now(timezone.utc))
        self._lock = threading.RLock()
        self._requests: dict[str, dict[str, Any]] = {}

    def create_request(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        normalized = normalize_approval_request_payload(payload)
        if not normalized.get("ok"):
            return normalized
        now = self._now()
        ttl_seconds = int(normalized.get("expiresInSec") or 300)
        request_id = f"approvalreq_{uuid.uuid4().hex}"
        entry = {
            "_profileUserId": str(profile_user_id or ""),
            "_sessionId": str(session_id or ""),
            "requestId": request_id,
            "kind": "capability_approval_request",
            "status": "pending",
            "decision": "",
            "capabilityId": normalized["capabilityId"],
            "actionId": normalized["actionId"],
            "title": normalized["title"],
            "summary": normalized["summary"],
            "risk": normalized["risk"],
            "approvalMode": "ask_each_time",
            "approvalReason": normalized["approvalReason"],
            "requestedBy": normalized["requestedBy"],
            "payloadPreview": normalized["payloadPreview"],
            "createdAt": _iso(now),
            "updatedAt": _iso(now),
            "expiresAt": _iso(now + timedelta(seconds=ttl_seconds)),
            "decidedAt": "",
            "grantId": "",
            "grantExpiresAt": "",
        }
        with self._lock:
            self._requests[request_id] = entry
        return {
            "ok": True,
            "status": "pending",
            "request": _public_request(entry),
            "requestId": request_id,
            "refresh": True,
        }

    def list_requests(
        self,
        *,
        profile_user_id: str,
        include_resolved: bool = False,
        limit: int = 20,
    ) -> dict[str, Any]:
        now = self._now()
        safe_limit = max(1, min(50, int(limit or 20)))
        with self._lock:
            self._expire_pending_locked(now)
            entries = [
                entry
                for entry in self._requests.values()
                if str(entry.get("_profileUserId") or "") == str(profile_user_id or "")
                and (include_resolved or entry.get("status") == "pending")
            ]
            entries.sort(key=lambda item: str(item.get("createdAt") or ""), reverse=True)
            public_entries = [_public_request(entry) for entry in entries[:safe_limit]]
            pending_count = sum(
                1
                for entry in self._requests.values()
                if str(entry.get("_profileUserId") or "") == str(profile_user_id or "")
                and entry.get("status") == "pending"
            )
        return {
            "ok": True,
            "status": "available",
            "schemaVersion": 1,
            "pendingCount": pending_count,
            "approvalRequests": public_entries,
            "summary": {
                "pending": pending_count,
                "returned": len(public_entries),
                "includeResolved": bool(include_resolved),
            },
        }

    def get_request(self, *, profile_user_id: str, request_id: str) -> dict[str, Any]:
        safe_request_id = _safe_request_id(request_id)
        if not safe_request_id:
            return {"ok": False, "status": "invalid_request", "reason": "approval_request_id_invalid"}
        with self._lock:
            self._expire_pending_locked(self._now())
            entry = self._requests.get(safe_request_id)
            if not entry or str(entry.get("_profileUserId") or "") != str(profile_user_id or ""):
                return {"ok": False, "status": "not_found", "reason": "approval_request_not_found"}
            return {"ok": True, "status": entry.get("status") or "pending", "request": _public_request(entry)}

    def decide_request(
        self,
        *,
        profile_user_id: str,
        request_id: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        safe_request_id = _safe_request_id(request_id)
        if not safe_request_id:
            return {"ok": False, "status": "invalid_request", "reason": "approval_request_id_invalid"}
        decision = _safe_decision(payload.get("decision") or payload.get("status"))
        if not decision:
            return {"ok": False, "status": "invalid_request", "reason": "approval_decision_invalid"}
        with self._lock:
            now = self._now()
            self._expire_pending_locked(now)
            entry = self._requests.get(safe_request_id)
            if not entry or str(entry.get("_profileUserId") or "") != str(profile_user_id or ""):
                return {"ok": False, "status": "not_found", "reason": "approval_request_not_found"}
            if entry.get("status") != "pending":
                return {
                    "ok": False,
                    "status": str(entry.get("status") or "resolved"),
                    "reason": "approval_request_already_resolved",
                    "request": _public_request(entry),
                    "refresh": True,
                }
            entry["status"] = "approved" if decision == "approved" else "denied"
            entry["decision"] = decision
            entry["updatedAt"] = _iso(now)
            entry["decidedAt"] = _iso(now)
            if decision == "approved":
                grant_expires_at = min(_parse_iso(entry.get("expiresAt")) or now, now + timedelta(seconds=APPROVAL_GRANT_TTL_SECONDS))
                entry["grantId"] = f"approvalgrant_{uuid.uuid4().hex}"
                entry["grantExpiresAt"] = _iso(grant_expires_at)
            public_entry = _public_request(entry)
        result = {
            "ok": True,
            "status": entry["status"],
            "decision": decision,
            "request": public_entry,
            "requestId": safe_request_id,
            "refresh": True,
            "followupContext": "用户已批准该能力请求。" if decision == "approved" else "用户拒绝了该能力请求，请不要执行该动作。",
        }
        if decision == "approved":
            result["approvalGrant"] = {
                "grantId": entry["grantId"],
                "requestId": safe_request_id,
                "capabilityId": entry["capabilityId"],
                "actionId": entry["actionId"],
                "expiresAt": entry["grantExpiresAt"],
            }
        return result

    def _expire_pending_locked(self, now: datetime) -> None:
        for entry in self._requests.values():
            if entry.get("status") != "pending":
                continue
            expires_at = _parse_iso(entry.get("expiresAt"))
            if expires_at and expires_at <= now:
                entry["status"] = "expired"
                entry["decision"] = "expired"
                entry["updatedAt"] = _iso(now)

    def _now(self) -> datetime:
        now = self._now_func()
        if now.tzinfo is None:
            return now.replace(tzinfo=timezone.utc)
        return now.astimezone(timezone.utc)


def normalize_approval_request_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    payload = payload if isinstance(payload, Mapping) else {}
    risk = _safe_enum(payload.get("risk"), {"medium", "high"}, default="medium")
    requires_confirmation = bool(payload.get("requiresConfirmation") or payload.get("requires_confirmation"))
    approval_mode = _safe_enum(
        payload.get("approvalMode") or payload.get("approval_mode"),
        {"trusted_auto_allow", "ask_each_time", "disabled"},
        default="ask_each_time" if risk == "high" or requires_confirmation else "trusted_auto_allow",
    )
    capability_id = _safe_public_token(payload.get("capabilityId") or payload.get("capability_id"))
    action_id = _safe_public_token(payload.get("actionId") or payload.get("action_id"))
    if approval_mode == "disabled":
        return {"ok": False, "status": "disabled", "reason": "capability_disabled"}
    if approval_mode != "ask_each_time" and risk != "high" and not requires_confirmation:
        return {"ok": False, "status": "not_required", "reason": "approval_not_required"}
    if not capability_id and not action_id:
        return {"ok": False, "status": "invalid_request", "reason": "approval_capability_missing"}
    title = _safe_public_text(payload.get("title") or payload.get("name"), default="能力请求", limit=80)
    summary = _safe_public_text(payload.get("summary") or payload.get("description"), default="Akane 想执行一个需要确认的能力动作。", limit=180)
    requested_by = _safe_public_token(payload.get("requestedBy") or payload.get("requested_by")) or "akane"
    ttl = _safe_ttl(payload.get("expiresInSec") or payload.get("expires_in_sec"))
    return {
        "ok": True,
        "capabilityId": capability_id,
        "actionId": action_id,
        "title": title,
        "summary": summary,
        "risk": risk,
        "approvalReason": _safe_public_token(payload.get("approvalReason") or payload.get("approval_reason")) or "requires_confirmation",
        "requestedBy": requested_by,
        "payloadPreview": _safe_payload_preview(payload.get("payloadPreview") or payload.get("payload_preview")),
        "expiresInSec": ttl,
    }


def _public_request(entry: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "requestId": str(entry.get("requestId") or ""),
        "kind": "capability_approval_request",
        "status": str(entry.get("status") or ""),
        "decision": str(entry.get("decision") or ""),
        "capabilityId": str(entry.get("capabilityId") or ""),
        "actionId": str(entry.get("actionId") or ""),
        "title": str(entry.get("title") or ""),
        "summary": str(entry.get("summary") or ""),
        "risk": str(entry.get("risk") or ""),
        "approvalMode": str(entry.get("approvalMode") or "ask_each_time"),
        "approvalReason": str(entry.get("approvalReason") or "requires_confirmation"),
        "requestedBy": str(entry.get("requestedBy") or "akane"),
        "payloadPreview": _safe_payload_preview(entry.get("payloadPreview")),
        "createdAt": str(entry.get("createdAt") or ""),
        "updatedAt": str(entry.get("updatedAt") or ""),
        "expiresAt": str(entry.get("expiresAt") or ""),
        "decidedAt": str(entry.get("decidedAt") or ""),
        "grantExpiresAt": str(entry.get("grantExpiresAt") or ""),
    }


def _safe_request_id(value: Any) -> str:
    text = str(value or "").strip()
    return text if APPROVAL_REQUEST_ID_RE.fullmatch(text) else ""


def _safe_decision(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"approve", "approved", "allow", "allowed", "yes", "true"}:
        return "approved"
    if text in {"deny", "denied", "reject", "rejected", "no", "false"}:
        return "denied"
    return ""


def _safe_enum(value: Any, allowed: set[str], *, default: str) -> str:
    text = str(value or "").strip().lower().replace("-", "_")
    return text if text in allowed else default


def _safe_public_token(value: Any) -> str:
    text = str(value or "").strip()
    if not text or not APPROVAL_SAFE_KEY_RE.fullmatch(text):
        return ""
    lowered = text.lower()
    if any(marker in lowered for marker in APPROVAL_SECRET_MARKERS):
        return ""
    return text[:80]


def _safe_public_key(value: Any) -> str:
    return _safe_public_token(value)


def _safe_public_text(value: Any, *, default: str = "", limit: int = 160) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return default[:limit]
    lowered = text.lower()
    if any(marker in lowered for marker in APPROVAL_SECRET_MARKERS):
        return default[:limit]
    text = LOCAL_PATH_RE.sub("[local_path]", text)
    return text[:limit]


def _safe_payload_preview(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    preview: dict[str, Any] = {}
    for raw_key, raw_value in list(value.items())[:12]:
        key = _safe_public_key(raw_key)
        if not key:
            continue
        safe_value = _safe_preview_value(raw_value)
        if safe_value in (None, "", {}, []):
            continue
        preview[key] = safe_value
    return preview


def _safe_preview_value(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        return _safe_public_text(value, limit=160)
    if isinstance(value, list):
        items = [_safe_preview_value(item) for item in value[:8]]
        return [item for item in items if item not in (None, "", {}, [])]
    if isinstance(value, Mapping):
        return _safe_payload_preview(value)
    return _safe_public_text(value, limit=120)


def _safe_ttl(value: Any) -> int:
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        seconds = 300
    return max(APPROVAL_REQUEST_TTL_MIN_SECONDS, min(APPROVAL_REQUEST_TTL_MAX_SECONDS, seconds))


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _parse_iso(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
