"""Bot-bound HTTP transport for the small OneBot action surface Akane uses."""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

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
    "upload_file_stream": "POST",
}

ONEBOT_STREAM_UPLOAD_CHUNK_BYTES = 512 * 1024
ONEBOT_STREAM_UPLOAD_RETENTION_MS = 5 * 60 * 1000


@dataclass(frozen=True, slots=True)
class StagedOneBotFile:
    ok: bool
    code: str
    file_ref: str = field(default="", repr=False)
    public_reason: str = ""


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
        response = self._request_json(action, payload, timeout=timeout)
        if isinstance(response, OutboundActionResult):
            return response
        body, http_status = response
        return normalize_action_response(str(action or "").strip().lstrip("/"), body, http_status=http_status)

    def stage_file(
        self,
        file_path: str | Path,
        *,
        filename: str = "",
        chunk_bytes: int = ONEBOT_STREAM_UPLOAD_CHUNK_BYTES,
    ) -> StagedOneBotFile:
        """Copy one host-local file into NapCat without exposing either filesystem."""
        path = Path(file_path)
        try:
            resolved = path.resolve(strict=True)
            stat = resolved.stat()
        except OSError:
            return StagedOneBotFile(False, "file_not_found", public_reason="待发送文件不存在。")
        if not resolved.is_file() or stat.st_size <= 0:
            return StagedOneBotFile(False, "file_empty", public_reason="待发送文件为空。")

        safe_chunk_bytes = max(64 * 1024, min(2 * 1024 * 1024, int(chunk_bytes or 0)))
        total_chunks = max(1, math.ceil(stat.st_size / safe_chunk_bytes))
        digest = hashlib.sha256()
        with resolved.open("rb") as source:
            for chunk in iter(lambda: source.read(safe_chunk_bytes), b""):
                digest.update(chunk)

        stream_id = str(uuid4())
        safe_name = Path(str(filename or resolved.name).replace("\\", "/")).name or resolved.name
        with resolved.open("rb") as source:
            for chunk_index in range(total_chunks):
                chunk = source.read(safe_chunk_bytes)
                payload = {
                    "stream_id": stream_id,
                    "chunk_data": base64.b64encode(chunk).decode("ascii"),
                    "chunk_index": chunk_index,
                    "total_chunks": total_chunks,
                    "file_size": stat.st_size,
                    "expected_sha256": digest.hexdigest(),
                    "filename": safe_name,
                    "file_retention": ONEBOT_STREAM_UPLOAD_RETENTION_MS,
                }
                response = self._request_json("upload_file_stream", payload, timeout=60)
                if isinstance(response, OutboundActionResult):
                    return StagedOneBotFile(False, response.code, public_reason=response.public_reason)
                body, http_status = response
                normalized = normalize_action_response("upload_file_stream", body, http_status=http_status)
                if not normalized.ok:
                    return StagedOneBotFile(False, normalized.code, public_reason=normalized.public_reason)

        response = self._request_json(
            "upload_file_stream",
            {"stream_id": stream_id, "is_complete": True},
            timeout=60,
        )
        if isinstance(response, OutboundActionResult):
            return StagedOneBotFile(False, response.code, public_reason=response.public_reason)
        body, http_status = response
        normalized = normalize_action_response("upload_file_stream", body, http_status=http_status)
        if not normalized.ok:
            return StagedOneBotFile(False, normalized.code, public_reason=normalized.public_reason)
        data = body.get("data") if isinstance(body, dict) and isinstance(body.get("data"), dict) else {}
        file_ref = str(data.get("file_path") or "").strip()
        if str(data.get("status") or "").strip() != "file_complete" or not file_ref:
            return StagedOneBotFile(
                False,
                "stream_upload_incomplete",
                public_reason="NapCat 文件接收未完成。",
            )
        return StagedOneBotFile(True, "ok", file_ref=file_ref)

    def _request_json(
        self,
        action: str,
        payload: dict[str, Any] | None,
        *,
        timeout: float,
    ) -> tuple[Any, int] | OutboundActionResult:
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
        return body, http_status


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
