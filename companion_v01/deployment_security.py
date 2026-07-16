"""Immutable deployment-owned channel and management security boundaries.

Instance manifests select *which* channel profile is enabled.  Secrets and
network endpoints remain deployment inputs and are bound once, before the
engine opens mutable stores.  This module deliberately exposes no public
snapshot containing secret material.
"""

from __future__ import annotations

import hmac
import json
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from .instance_profile import InstanceContext


class DeploymentSecurityError(RuntimeError):
    """Structured startup failure without secret values or local paths."""

    def __init__(self, *, reason: str, field_name: str = "") -> None:
        self.status = "invalid_config"
        self.reason = str(reason or "deployment_security_invalid")
        self.field = str(field_name or "")
        super().__init__(json.dumps(self.as_dict(), ensure_ascii=False, sort_keys=True))

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ok": False,
            "status": self.status,
            "reason": self.reason,
        }
        if self.field:
            payload["field"] = self.field
        return payload


@dataclass(frozen=True, slots=True)
class AuthorizationDecision:
    ok: bool
    status_code: int = 200
    reason: str = ""


@dataclass(frozen=True, slots=True)
class QQChannelRuntimeConfig:
    """One restart-only QQ binding for the current Akane process."""

    enabled: bool
    profile_ref: str
    bot_id: str
    onebot_http_url: str
    webhook_secret: str = field(repr=False)
    onebot_access_token: str = field(repr=False)
    require_webhook_auth: bool
    require_self_id: bool

    def authorize_webhook(self, request: Any) -> AuthorizationDecision:
        if not self.require_webhook_auth:
            return AuthorizationDecision(True)
        supplied = _request_token(
            request,
            alternate_header="x-akane-webhook-secret",
        )
        if supplied and hmac.compare_digest(supplied, self.webhook_secret):
            return AuthorizationDecision(True)
        return AuthorizationDecision(False, 401, "qq_webhook_auth_required")

    def authorize_event_identity(self, event: Any) -> AuthorizationDecision:
        if not self.require_self_id:
            return AuthorizationDecision(True)
        if not isinstance(event, dict):
            return AuthorizationDecision(False, 400, "qq_event_must_be_object")
        event_self_id = str(event.get("self_id") or "").strip()
        if event_self_id and hmac.compare_digest(event_self_id, self.bot_id):
            return AuthorizationDecision(True)
        return AuthorizationDecision(False, 403, "qq_self_id_mismatch")

    def onebot_headers(self) -> dict[str, str]:
        if not self.onebot_access_token:
            return {}
        return {"Authorization": f"Bearer {self.onebot_access_token}"}


@dataclass(frozen=True, slots=True)
class AdminWriteAuth:
    """Management-write authorization with local-default compatibility."""

    token: str = field(repr=False)
    require_token: bool
    allow_loopback_without_token: bool

    def authorize(self, request: Any) -> AuthorizationDecision:
        if self.require_token:
            supplied = _request_token(
                request,
                alternate_header="x-akane-admin-token",
            )
            if supplied and hmac.compare_digest(supplied, self.token):
                return AuthorizationDecision(True)
            return AuthorizationDecision(False, 401, "admin_auth_required")
        if self.allow_loopback_without_token and is_loopback_request(request):
            return AuthorizationDecision(True)
        return AuthorizationDecision(False, 403, "local_request_required")

    @classmethod
    def local_compatibility(cls) -> "AdminWriteAuth":
        return cls(token="", require_token=False, allow_loopback_without_token=True)


@dataclass(frozen=True, slots=True)
class DesktopSatelliteAuth:
    """Restart-only device credential, deliberately separate from admin auth."""

    token: str = field(repr=False)

    @property
    def enabled(self) -> bool:
        return bool(self.token)


@dataclass(frozen=True, slots=True)
class InstanceDeploymentSecurity:
    qq: QQChannelRuntimeConfig
    admin: AdminWriteAuth
    satellite: DesktopSatelliteAuth


def resolve_instance_deployment_security(
    instance_context: InstanceContext,
    config_module: Any,
) -> InstanceDeploymentSecurity:
    """Bind manifest selections to deployment secrets before Engine startup."""

    compatibility = instance_context.is_compatibility_default
    admin_token = _clean(getattr(config_module, "AKANE_ADMIN_TOKEN", ""))
    if not compatibility and not admin_token:
        _fail("admin_token_required", field_name="AKANE_ADMIN_TOKEN")
    admin = AdminWriteAuth(
        token=admin_token,
        require_token=bool(admin_token),
        allow_loopback_without_token=compatibility and not admin_token,
    )
    satellite = DesktopSatelliteAuth(
        token=_clean(getattr(config_module, "AKANE_DESKTOP_SATELLITE_TOKEN", ""))
    )

    manifest_qq = instance_context.channels.qq
    enabled = (
        bool(getattr(config_module, "QQ_BRIDGE_ENABLED", False))
        if compatibility
        else manifest_qq.enabled
    )
    configured_profile_ref = _clean(
        getattr(config_module, "QQ_CHANNEL_PROFILE_REF", "")
    )
    profile_ref = configured_profile_ref if compatibility else manifest_qq.profile_ref
    bot_id = _clean(getattr(config_module, "QQ_BOT_QQ", ""))
    onebot_http_url = _clean(
        getattr(config_module, "QQ_ONEBOT_HTTP_URL", "http://127.0.0.1:3001")
    ).rstrip("/") or "http://127.0.0.1:3001"
    webhook_secret = _clean(getattr(config_module, "QQ_WEBHOOK_SECRET", ""))
    onebot_access_token = _clean(
        getattr(config_module, "QQ_ONEBOT_ACCESS_TOKEN", "")
    )

    if enabled and not compatibility:
        if not configured_profile_ref:
            _fail(
                "qq_channel_profile_ref_required",
                field_name="QQ_CHANNEL_PROFILE_REF",
            )
        if configured_profile_ref != manifest_qq.profile_ref:
            _fail(
                "qq_channel_profile_ref_mismatch",
                field_name="QQ_CHANNEL_PROFILE_REF",
            )
        if not bot_id or not bot_id.isdigit():
            _fail("qq_bot_id_required", field_name="QQ_BOT_QQ")
        if not webhook_secret:
            _fail("qq_webhook_secret_required", field_name="QQ_WEBHOOK_SECRET")
        if not onebot_access_token:
            _fail(
                "qq_onebot_access_token_required",
                field_name="QQ_ONEBOT_ACCESS_TOKEN",
            )
        if not _valid_http_url(onebot_http_url):
            _fail("qq_onebot_http_url_invalid", field_name="QQ_ONEBOT_HTTP_URL")

    return InstanceDeploymentSecurity(
        qq=QQChannelRuntimeConfig(
            enabled=enabled,
            profile_ref=profile_ref,
            bot_id=bot_id,
            onebot_http_url=onebot_http_url,
            webhook_secret=webhook_secret,
            onebot_access_token=onebot_access_token,
            require_webhook_auth=enabled and (not compatibility or bool(webhook_secret)),
            require_self_id=enabled and bool(bot_id),
        ),
        admin=admin,
        satellite=satellite,
    )


def is_loopback_request(request: Any) -> bool:
    host = str(
        getattr(getattr(request, "client", None), "host", "") or ""
    ).strip().lower()
    return host in {"127.0.0.1", "::1", "localhost", "testclient"}


def _request_token(request: Any, *, alternate_header: str) -> str:
    headers = getattr(request, "headers", {})
    authorization = _clean(headers.get("authorization", ""))
    if authorization:
        scheme, separator, value = authorization.partition(" ")
        if separator and scheme.lower() == "bearer":
            return value.strip()
    return _clean(headers.get(alternate_header, ""))


def _valid_http_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.hostname)


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _fail(reason: str, *, field_name: str) -> None:
    raise DeploymentSecurityError(reason=reason, field_name=field_name)
