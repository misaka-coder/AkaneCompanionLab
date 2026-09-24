"""Scope-bound capability directory and exact contract loading.

This module is a projection over the already-resolved handler map. It does not
register a second capability authority and never reads provider configuration,
paths, credentials, or MCP connection internals into the model-facing catalog.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Any, Mapping

from capcore import CapabilityToolSpec

from .capability_discovery_specs import CAPABILITY_LOAD_TOOL_SPEC, CAPABILITY_SEARCH_TOOL_SPEC, CAPABILITY_LIST_TOOL_SPEC, CAPABILITY_INVOKE_TOOL_SPEC
from .capability_contracts import contract_snapshot, scope_key
from .native_tool_schema import build_openai_native_tool_from_spec
from .tool_handlers.core import BaseToolHandler, ToolExecutionContext, ToolExecutionResult


CAPABILITY_SEARCH_TOOL_ID = CAPABILITY_SEARCH_TOOL_SPEC.capability_id
CAPABILITY_LOAD_TOOL_ID = CAPABILITY_LOAD_TOOL_SPEC.capability_id
CAPABILITY_DISCOVERY_TOOL_IDS = frozenset({CAPABILITY_SEARCH_TOOL_ID, CAPABILITY_LOAD_TOOL_ID, "capability_list", "capability_invoke"})
_DEFAULT_SEARCH_LIMIT = 12
_MAX_SEARCH_LIMIT = 32
_MAX_LOAD_IDS = 32
_CURSOR_TAG_BYTES = 16


def visible_tool_result_messages(messages):
    """Text from actual provider-visible tool results, including Anthropic blocks.

    No user prose, screenshot pixels, or global 'ever loaded' state participates.
    """
    result = []
    def text(content):
        if isinstance(content, str): return content
        if isinstance(content, list): return "\n".join(str(block.get("text", "")) for block in content if isinstance(block, dict) and block.get("type") == "text")
        return ""
    for message in messages if isinstance(messages, list) else []:
        if not isinstance(message, dict): continue
        if message.get("role") == "tool":
            result.append({"role":"tool", "content":text(message.get("content"))})
        elif message.get("role") == "user" and isinstance(message.get("content"), list):
            for block in message["content"]:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    result.append({"role":"tool", "content":text(block.get("content"))})
    return result


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _b64_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(str(value or "") + "=" * (-len(str(value or "")) % 4))


def _spec_payload(spec: CapabilityToolSpec) -> dict[str, Any]:
    return {
        "capability_id": str(spec.capability_id or ""),
        "display_name": str(spec.display_name or ""),
        "description": str(spec.description or ""),
        "input_schema": dict(spec.input_schema or {}),
        "output_schema": dict(spec.output_schema or {}),
        "risk": str(spec.risk or ""),
        "confirm": str(spec.confirm or ""),
        "effects": list(spec.effects or ()),
        "visible_in": list(spec.visible_in or ()),
        "spec_version": str(spec.spec_version or ""),
        "schema_version": int(spec.schema_version or 0),
        "schema_hash": str(spec.schema_hash or ""),
        "execution_class": str(spec.execution_class or ""),
        "idempotency": str(spec.idempotency or ""),
        "max_result_bytes": spec.max_result_bytes,
    }


@dataclass(frozen=True)
class _CatalogEntry:
    capability_id: str
    handler: Any
    spec: CapabilityToolSpec
    fingerprint: str
    snapshot: Mapping[str, Any]

    @property
    def short(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "display_name": str(self.spec.display_name or self.capability_id),
            # Legacy providers may supply only a long description. Preserve its
            # meaning instead of silently cutting a condition or qualifier.
            "description": str(getattr(self.spec, "short_hint", "") or self.spec.description or self.spec.display_name or self.capability_id),
        }

    def full(self) -> dict[str, Any]:
        payload = _spec_payload(self.spec)
        payload["capability_id"] = self.capability_id
        payload["contract_ref"] = self.snapshot["contract_ref"]
        guide = getattr(self.handler, "usage_guide", None)
        if callable(guide):
            payload["usage_guide"] = guide()
        # computer_use is invoked through this loaded canonical schema. Its
        # native declaration is generated separately by the actual provider
        # request path; duplicating it here adds no callable interface.
        if self.capability_id != "computer_use":
            try:
                payload["native_schema"] = build_openai_native_tool_from_spec(self.spec)
            except Exception:
                payload["native_schema"] = None
        return payload


class CapabilityDiscoveryCatalog:
    """A deterministic catalog for one frozen scope and handler revision."""

    def __init__(
        self,
        handlers: Mapping[str, Any],
        *,
        profile_user_id: str,
        session_id: str,
        client_mode: str = "",
        domain_profile_id: str = "",
        cursor_secret: bytes | None = None,
        character_pack_id: str = "",
        authorization_profile_user_id: str = "",
        refresh=None,
        refresh_targets=None,
    ) -> None:
        self.profile_user_id = str(profile_user_id or "").strip()
        self.session_id = str(session_id or "").strip()
        self.client_mode = str(client_mode or "").strip()
        self.domain_profile_id = str(domain_profile_id or "").strip()
        self.character_pack_id = str(character_pack_id or "").strip()
        self.authorization_profile_user_id = str(authorization_profile_user_id or "").strip()
        self._cursor_secret = bytes(cursor_secret or b"akane-capability-discovery-v1")
        self._refresh = refresh
        self._refresh_targets = refresh_targets
        self.scope = scope_key(profile_user_id=self.profile_user_id, session_id=self.session_id,
                               character_pack_id=character_pack_id, client_mode=self.client_mode,
                               domain_profile_id=self.domain_profile_id,
                               authorization_profile_user_id=authorization_profile_user_id)
        entries: list[_CatalogEntry] = []
        for raw_name, handler in sorted(dict(handlers or {}).items(), key=lambda item: str(item[0] or "")):
            capability_id = str(raw_name or "").strip()
            if not capability_id or capability_id in CAPABILITY_DISCOVERY_TOOL_IDS or handler is None:
                continue
            tool_type = str(getattr(handler, "tool_type", "") or capability_id).strip()
            if tool_type != capability_id:
                continue
            getter = getattr(handler, "tool_spec", None)
            if not callable(getter):
                continue
            try:
                spec = getter()
            except Exception:
                continue
            if not isinstance(spec, CapabilityToolSpec) or str(spec.capability_id or "").strip() != capability_id:
                continue
            snapshot = contract_snapshot(handler, scope=self.scope)
            entries.append(
                _CatalogEntry(
                    capability_id=capability_id,
                    handler=handler,
                    spec=spec,
                    fingerprint=snapshot["fingerprint"],
                    snapshot=snapshot,
                )
            )
        self._entries = tuple(entries)
        self._by_id = {entry.capability_id: entry for entry in self._entries}
        self._scope_fingerprint = self.scope
        self.revision = hashlib.sha256(
            _canonical_json(
                [(entry.capability_id, entry.fingerprint) for entry in self._entries]
            ).encode("utf-8")
        ).hexdigest()

    @property
    def capability_ids(self) -> tuple[str, ...]:
        return tuple(entry.capability_id for entry in self._entries)

    def current(self):
        return self._refresh() if self._refresh is not None else self

    def current_for(self, capability_ids):
        return self._refresh_targets(tuple(capability_ids)) if self._refresh_targets is not None else self.current()

    def restricted(self, allowed):
        """Apply a host-owned execution ceiling to directory reads as well."""
        return CapabilityDiscoveryCatalog(
            {name: self.handler(name) for name in self.capability_ids if name in allowed},
            profile_user_id=self.profile_user_id, session_id=self.session_id,
            character_pack_id=self.character_pack_id, client_mode=self.client_mode,
            domain_profile_id=self.domain_profile_id,
            authorization_profile_user_id=self.authorization_profile_user_id,
            cursor_secret=self._cursor_secret,
            refresh=lambda: self.current().restricted(allowed),
            refresh_targets=lambda ids: self.current_for(ids).restricted(allowed))

    def snapshot(self, capability_id):
        entry = self._by_id.get(capability_id)
        return dict(entry.snapshot) if entry is not None else None

    def current_snapshot(self, capability_id):
        return self.current_for((capability_id,)).snapshot(capability_id)

    def handler(self, capability_id):
        entry = self._by_id.get(capability_id)
        return entry.handler if entry is not None else None

    def short_entries(self):
        return [entry.short for entry in self._entries]

    def search(self, *, query: str = "", cursor: str = "", limit: Any = None) -> dict[str, Any]:
        normalized_query = " ".join(str(query or "").split()).casefold()[:160]
        page_limit = _normalize_limit(limit)
        offset = 0
        if str(cursor or "").strip():
            decoded = self._decode_cursor(str(cursor).strip())
            if decoded is None:
                return self._rejected("cursor_invalid")
            if (
                decoded.get("scope") != self._scope_fingerprint
                or decoded.get("query") != normalized_query
            ):
                return self._rejected("cursor_invalid")
            if decoded.get("revision") != self.revision:
                return self._rejected("cursor_stale")
            try:
                offset = int(decoded.get("offset", 0))
            except (TypeError, ValueError):
                return self._rejected("cursor_invalid")
            if offset < 0:
                return self._rejected("cursor_invalid")

        matches = [entry for entry in self._entries if self._matches(entry, normalized_query)]
        page = matches[offset : offset + page_limit]
        next_offset = offset + len(page)
        next_cursor = ""
        if next_offset < len(matches):
            next_cursor = self._encode_cursor(
                {
                    "scope": self._scope_fingerprint,
                    "revision": self.revision,
                    "query": normalized_query,
                    "offset": next_offset,
                }
            )
        items = [entry.short for entry in page]
        return {
            "status": "ok",
            "items": items,
            "next_cursor": next_cursor,
            "catalog_revision": self.revision,
            "catalog_size": len(self._entries),
            "match_count": len(matches),
            "reason": "" if matches else ("catalog_empty" if not self._entries else "no_matches"),
            "recovery_hint": "" if matches else (
                "当前作用域没有可见能力；目录为空不说明其他会话的能力状态。" if not self._entries else
                "这只是文本子串查询没有匹配，不代表能力不可用。可缩短关键词、用 capability_list 浏览，或直接加载已知精确 ID。"
            ),
        }

    def load(self, capability_ids: Any) -> dict[str, Any]:
        if not isinstance(capability_ids, list) or not capability_ids:
            return self._rejected("capability_ids_required", include_load_fields=True)
        if len(capability_ids) > _MAX_LOAD_IDS:
            return self._rejected("capability_ids_too_many", include_load_fields=True)
        normalized = [str(item or "").strip() for item in capability_ids]
        if any(not item for item in normalized):
            return self._rejected("capability_ids_required", include_load_fields=True)
        # Loading is a read: repeated ids and one revoked entry must not keep
        # independent, available contracts from reaching the model.
        normalized = list(dict.fromkeys(normalized))
        missing = [item for item in normalized if item not in self._by_id]
        capabilities = [self._by_id[item].full() for item in normalized if item in self._by_id]
        return {
            "status": ("partial" if capabilities else "rejected") if missing else "ok",
            "capabilities": capabilities,
            "missing_capability_ids": missing,
            "catalog_revision": self.revision,
            "reason": "capability_not_available" if missing else "",
            "recovery_hint": (
                "已返回的契约可以立即通过 capability_invoke 使用，无需重复加载。缺失 ID 在当前目录不可见；"
                "可用 capability_list 核对当前 ID，不要据此判断整批能力都不可用。" if capabilities and missing else
                "请求的 ID 在当前目录不可见；可用 capability_list 核对当前能力，再加载精确 ID。" if missing else ""
            ),
        }

    def _matches(self, entry: _CatalogEntry, query: str) -> bool:
        if not query:
            return True
        haystack = " ".join(
            (
                entry.capability_id,
                str(entry.spec.display_name or ""),
                str(entry.spec.description or ""),
            )
        ).casefold()
        return query in haystack

    def _encode_cursor(self, payload: Mapping[str, Any]) -> str:
        body = _b64_encode(_canonical_json(dict(payload)).encode("utf-8"))
        signature = hmac.new(
            self._cursor_secret,
            body.encode("ascii"),
            hashlib.sha256,
        ).digest()[:_CURSOR_TAG_BYTES]
        return f"{body}.{_b64_encode(signature)}"

    def _decode_cursor(self, token: str) -> dict[str, Any] | None:
        try:
            body, signature = str(token).split(".", 1)
            expected = hmac.new(
                self._cursor_secret,
                body.encode("ascii"),
                hashlib.sha256,
            ).digest()[:_CURSOR_TAG_BYTES]
            if not hmac.compare_digest(expected, _b64_decode(signature)):
                return None
            value = json.loads(_b64_decode(body).decode("utf-8"))
            return dict(value) if isinstance(value, dict) else None
        except (ValueError, TypeError, json.JSONDecodeError, UnicodeError):
            return None

    @staticmethod
    def _rejected(
        reason: str,
        *,
        include_load_fields: bool = False,
        missing: list[str] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "status": "rejected",
            "items": [],
            "next_cursor": "",
            "catalog_revision": "",
            "reason": str(reason or "capability_discovery_rejected"),
            "recovery_hint": (
                "分页游标已失效或不属于当前查询；保留所需查询并省略 cursor，从当前目录第一页重新读取。"
                if reason in {"cursor_invalid", "cursor_stale"} else
                "按工具 schema 修正参数后重试；每批最多加载 32 个精确 ID。"
            ),
        }
        if include_load_fields:
            payload.pop("items")
            payload.pop("next_cursor")
            payload.update({"capabilities": []})
            if missing:
                payload["missing_capability_ids"] = list(missing)
        return payload


def build_capability_discovery_handlers(
    handlers: Mapping[str, Any],
    *,
    profile_user_id: str,
    session_id: str,
    client_mode: str = "",
    domain_profile_id: str = "",
    cursor_secret: bytes | None = None,
    character_pack_id: str = "",
    authorization_profile_user_id: str = "",
    refresh=None,
    refresh_targets=None,
    search_enabled: bool = True,
) -> tuple[dict[str, Any], CapabilityDiscoveryCatalog]:
    catalog = CapabilityDiscoveryCatalog(
        handlers,
        profile_user_id=profile_user_id,
        session_id=session_id,
        client_mode=client_mode,
        domain_profile_id=domain_profile_id,
        cursor_secret=cursor_secret,
        character_pack_id=character_pack_id,
        authorization_profile_user_id=authorization_profile_user_id,
        refresh=refresh,
        refresh_targets=refresh_targets,
    )
    return {
        **({CAPABILITY_SEARCH_TOOL_ID: CapabilityDiscoveryToolHandler(catalog, operation="search")} if search_enabled else {}),
        CAPABILITY_LOAD_TOOL_ID: CapabilityDiscoveryToolHandler(catalog, operation="load"),
        "capability_list": CapabilityDiscoveryToolHandler(catalog, operation="list"),
        "capability_invoke": CapabilityInvokeToolHandler(),
    }, catalog


class CapabilityDiscoveryToolHandler(BaseToolHandler):
    policy_accepted_native_tool = True

    def __init__(self, catalog: CapabilityDiscoveryCatalog, *, operation: str) -> None:
        self._catalog = catalog
        self._operation = str(operation or "").strip().lower()
        self.tool_type = {"search": CAPABILITY_SEARCH_TOOL_ID, "load": CAPABILITY_LOAD_TOOL_ID, "list": "capability_list"}[self._operation]

    def tool_spec(self):
        return {"search": CAPABILITY_SEARCH_TOOL_SPEC, "load": CAPABILITY_LOAD_TOOL_SPEC, "list": CAPABILITY_LIST_TOOL_SPEC}[self._operation]

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict) or str(value.get("type") or "").strip() != self.tool_type:
            return None
        if self._operation in {"search", "list"}:
            raw_limit = value.get("limit", _DEFAULT_SEARCH_LIMIT)
            if isinstance(raw_limit, bool):
                return None
            try:
                limit = max(1, min(_MAX_SEARCH_LIMIT, int(raw_limit)))
            except (TypeError, ValueError):
                return None
            return {
                "type": self.tool_type,
                "query": " ".join(str(value.get("query") or "").split())[:160],
                "cursor": str(value.get("cursor") or "").strip()[:2048],
                "limit": limit,
            }
        raw_ids = value.get("capability_ids")
        if not isinstance(raw_ids, list) or not raw_ids:
            return None
        ids = [str(item or "").strip() for item in raw_ids]
        if len(ids) > _MAX_LOAD_IDS or any(not item for item in ids):
            return None
        return {"type": self.tool_type, "capability_ids": ids}

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        catalog = (self._catalog.current_for(call.get("capability_ids", ()))
                   if self._operation == "load" else self._catalog.current())
        if self._operation in {"search", "list"}:
            payload = catalog.search(
                query=call.get("query") if self._operation == "search" else "",
                cursor=call.get("cursor"),
                limit=call.get("limit"),
            )
        else:
            payload = catalog.load(call.get("capability_ids"))
            visible = visible_tool_result_messages(getattr(context, "request_context", {}).get("_host_visible_tool_history", []))
            # Only exact host-authored guide content in the current projection
            # counts; there is no session-global 'ever loaded' flag.
            for capability in payload.get("capabilities", []):
                guide = capability.get("usage_guide")
                if isinstance(guide, dict) and guide.get("text"):
                    marker = json.dumps(guide, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                    if any(marker in str(message.get("content", "")) for message in visible
                           if isinstance(message, dict) and message.get("role") == "tool"):
                        capability["usage_guide"] = {"version": guide["version"], "already_visible": True}
        ok = payload.get("status") in {"ok", "partial"}
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if not ok:
            reason = str(payload.get("reason") or "capability_discovery_rejected")
            return ToolExecutionResult(
                tool_type=self.tool_type,
                stream_events=[
                    {
                        "type": "capability_discovery",
                        "operation": self._operation,
                        "status": "failed",
                        "reason": reason,
                    }
                ],
                followup_context=serialized,
            )
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=[
                {
                    "type": "capability_discovery",
                    "operation": self._operation,
                    "status": "partial" if payload.get("status") == "partial" else "succeeded",
                    "count": len(payload.get("items") or payload.get("capabilities") or []),
                }
            ],
            followup_context=f"【能力发现结果】\n{serialized}",
        )


class CapabilityInvokeToolHandler(BaseToolHandler):
    """Schema only; the host rewrites calls to real targets before execution."""

    policy_accepted_native_tool = True
    tool_type = "capability_invoke"

    def tool_spec(self):
        return CAPABILITY_INVOKE_TOOL_SPEC

    def normalize_call(self, value):
        return dict(value) if isinstance(value, dict) and value.get("type") == self.tool_type else None

    def execute(self, *, call, context):
        from .capability_contracts import contract_failure
        return contract_failure(str(call.get("capability_id") or self.tool_type), "capability_target_invalid")


def _normalize_limit(value: Any) -> int:
    if value is None or value == "":
        return _DEFAULT_SEARCH_LIMIT
    if isinstance(value, bool):
        return _DEFAULT_SEARCH_LIMIT
    try:
        return max(1, min(_MAX_SEARCH_LIMIT, int(value)))
    except (TypeError, ValueError):
        return _DEFAULT_SEARCH_LIMIT


__all__ = [
    "CAPABILITY_DISCOVERY_TOOL_IDS",
    "CAPABILITY_LOAD_TOOL_ID",
    "CAPABILITY_SEARCH_TOOL_ID",
    "CapabilityDiscoveryCatalog",
    "CapabilityDiscoveryToolHandler",
    "build_capability_discovery_handlers",
]
