"""Bot-bound HTTP transport for the small OneBot action surface Akane uses."""

from __future__ import annotations

import ipaddress
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import requests
from channelcore_onebot import OutboundActionResult, action_failure, normalize_action_response

from .deployment_security import QQChannelRuntimeConfig


ONEBOT_ACTION_METHODS = {
    "get_login_info": "GET",
    "get_status": "GET",
    "get_msg": "POST",
    "get_group_member_info": "POST",
    "get_image": "POST",
    "get_file": "POST",
    "send_private_msg": "POST",
    "send_group_msg": "POST",
    "upload_private_file": "POST",
    "upload_group_file": "POST",
}


class OneBotActionTransport:
    """A dedicated, non-proxying session bound to one configured Bot origin."""

    def __init__(self, channel_config: QQChannelRuntimeConfig, *, session: requests.Session | None = None) -> None:
        self._config = channel_config
        self._base_url = _canonical_base_url(channel_config.onebot_http_url)
        self._session = session or requests.Session()
        self._session.trust_env = False

    def call(
        self, action: str, payload: dict[str, Any] | None = None, *, timeout: float = 20.0
    ) -> OutboundActionResult:
        clean_action = str(action or "").strip().lstrip("/")
        method = ONEBOT_ACTION_METHODS.get(clean_action)
        if method is None:
            return action_failure(clean_action or "unknown", "action_not_allowed", "不支持的 OneBot action。")
        if not self._base_url:
            return action_failure(clean_action, "invalid_base_url", "OneBot 服务地址配置无效。")
        try:
            response = self._session.request(
                method,
                f"{self._base_url}/{clean_action}",
                json=dict(payload or {}) if method == "POST" else None,
                headers=self._config.onebot_headers(),
                timeout=timeout,
                allow_redirects=False,
            )
        except requests.Timeout:
            return action_failure(clean_action, "timeout", "OneBot 请求超时。")
        except requests.RequestException:
            return action_failure(clean_action, "connection_error", "无法连接 OneBot 服务，请确认 NapCat 端口可用。")
        except Exception:
            return action_failure(clean_action, "transport_error", "OneBot 请求失败。")

        http_status = int(getattr(response, "status_code", 200) or 0)
        if 300 <= http_status < 400:
            return action_failure(clean_action, "redirect_rejected", "OneBot action 不允许重定向。", http_status)
        if http_status in {401, 403}:
            return action_failure(clean_action, "auth_failed", "OneBot 鉴权失败，请检查访问令牌。", http_status)
        if not 200 <= http_status < 300:
            return action_failure(clean_action, "http_error", "OneBot 返回了异常 HTTP 状态。", http_status)
        try:
            body = response.json()
        except Exception:
            return action_failure(clean_action, "invalid_json", "OneBot 返回格式无效。", http_status)
        return normalize_action_response(clean_action, body, http_status=http_status)


def _canonical_base_url(value: str) -> str:
    try:
        parsed = urlsplit(str(value or "").strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            return ""
        hostname = parsed.hostname
        if hostname.lower() == "localhost":
            hostname = "127.0.0.1"
        else:
            try:
                hostname = f"[{ipaddress.ip_address(hostname).compressed}]" if ":" in hostname else hostname
            except ValueError:
                hostname = hostname.lower()
        port = f":{parsed.port}" if parsed.port is not None else ""
        path = parsed.path.rstrip("/")
        return urlunsplit((parsed.scheme.lower(), f"{hostname}{port}", path, "", ""))
    except (TypeError, ValueError):
        return ""
