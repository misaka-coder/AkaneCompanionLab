"""Akane-owned authorization for reading one conversation's memory.

MemCore remains unaware of QQ identities.  A signed, host-issued QQ origin is
required before a tool may select another QQ namespace.  Native MemCore cursors
are never accepted directly from a model after this boundary is enabled.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import uuid
import zlib
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Mapping


_GROUP = re.compile(r"group:([1-9][0-9]{0,19})\Z")
_CURSOR_PREFIX = "mrc1"


@dataclass(frozen=True)
class MemoryReadTarget:
    profile_user_id: str
    session_id: str
    character_pack_id: str
    conversation: str
    source_label: str
    actor_id: str
    external: bool


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("invalid_base64")
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


class MemoryReadPolicy:
    def __init__(
        self,
        *,
        store: Any,
        bot_id: str,
        master_qq: str,
        enabled: Callable[[], bool],
        resolve_origin: Callable[[str], Mapping[str, str] | None],
        key_path: Path | None = None,
        key: bytes | None = None,
        private_policy: Any = None,
    ) -> None:
        self.store = store
        self.bot_id = str(bot_id or "").strip()
        self.master_qq = str(master_qq or "").strip()
        self.enabled = enabled
        self.resolve_origin = resolve_origin
        self.private_policy = private_policy
        self._key = bytes(key) if key is not None else self._load_key(Path(key_path))
        if len(self._key) < 32:
            raise ValueError("memory_cursor_key_too_short")

    @staticmethod
    def _load_key(path: Path) -> bytes:
        try:
            encoded = path.read_text(encoding="ascii").strip()
        except FileNotFoundError:
            encoded = ""
        except OSError as exc:
            raise RuntimeError("memory_cursor_key_unreadable") from exc
        if encoded:
            try:
                key = bytes.fromhex(encoded)
            except ValueError as exc:
                raise RuntimeError("memory_cursor_key_invalid") from exc
            if len(key) != 32:
                raise RuntimeError("memory_cursor_key_invalid")
            return key
        if path.exists():
            raise RuntimeError("memory_cursor_key_invalid")
        path.parent.mkdir(parents=True, exist_ok=True)
        key = secrets.token_bytes(32)
        temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("x", encoding="ascii") as stream:
                stream.write(key.hex())
            os.chmod(temporary, 0o600)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        return key

    @staticmethod
    def _error(status: str) -> dict[str, Any]:
        return {"ok": False, "status": status, "reason": status, "backend": "memcore"}

    def _origin(self, context: Any) -> Mapping[str, str] | None:
        request = getattr(context, "request_context", None)
        request = request if isinstance(request, Mapping) else {}
        private = self._private_scope(context)
        if private and private.shared and private.channel == "desktop":
            return {"channel": "desktop_pet", "kind": "direct", "recipient": f"user:{self.master_qq}"}
        reference = request.get("_memory_qq_ref")
        if not isinstance(reference, str) or not reference:
            return None
        origin = self.resolve_origin(reference)
        if not isinstance(origin, Mapping) or origin.get("channel") != "qq":
            return None
        if any(str(origin.get(key) or "") != str(getattr(context, attr, "") or "") for key, attr in (
            ("profile", "profile_user_id"), ("session", "session_id"), ("character", "character_pack_id"),
        )):
            return None
        recipient = str(origin.get("recipient") or "")
        if origin.get("kind") == "group":
            match = _GROUP.fullmatch(recipient)
            if not match or context.session_id != f"qq_group_shared_{match.group(1)}":
                return None
        elif origin.get("kind") == "direct":
            user_id = recipient.removeprefix("user:")
            if recipient != f"user:{user_id}" or not user_id.isdigit() or not user_id:
                return None
            expected = ("master", "master") if user_id == self.master_qq else (f"qq_{user_id}", f"qq_pri_{user_id}")
            if (context.profile_user_id, context.session_id) != expected:
                return None
        else:
            return None
        return origin

    def _private_scope(self, context: Any):
        if self.private_policy is None:
            return None
        return self.private_policy.resolve({
            **dict(getattr(context, "request_context", None) or {}),
            "user_id": str(context.session_id or ""), "real_user_id": str(context.profile_user_id or ""),
            "client_mode": str(getattr(context, "client_mode", "") or ""),
        }, character=str(context.character_pack_id or ""))

    def _cursor_identity(self, context: Any) -> tuple[str, str]:
        private = self._private_scope(context)
        return private.memory_identity if private and private.shared else (
            str(context.profile_user_id or ""), str(context.session_id or ""))

    def _cursor_matches(self, saved, context) -> bool:
        profile, session = self._cursor_identity(context)
        return (saved.get("profile") == profile and saved.get("session") == session
                and saved.get("character") == str(context.character_pack_id or "")
                and saved.get("actor") == self._actor(self._origin(context), context))

    def is_verified_qq_turn(self, *, profile_user_id: str, session_id: str,
                            character_pack_id: str, reference: Any) -> bool:
        if not isinstance(reference, str) or not reference:
            return False
        context = SimpleNamespace(
            profile_user_id=profile_user_id, session_id=session_id,
            character_pack_id=character_pack_id,
            request_context={"_memory_qq_ref": reference},
        )
        return self._origin(context) is not None

    def _actor(self, origin: Mapping[str, str] | None, context: Any) -> str:
        if origin is None:
            return f"local:{context.profile_user_id}:{context.session_id}"
        if origin.get("kind") == "group":
            return str(origin.get("actor") or "qq:room")
        return str(origin.get("recipient") or "")

    def _authorize(self, context: Any, selector: str) -> tuple[MemoryReadTarget | None, dict[str, Any] | None]:
        task_id = str(getattr(getattr(context, "execution_scope", None), "task_id", "") or "")
        if task_id.startswith("subagent_"):
            if selector != "current" or (context.profile_user_id, context.session_id) != (task_id, task_id):
                return None, self._error("forbidden")
            return MemoryReadTarget(task_id, task_id, "", "current", "子任务历史",
                                    f"task:{task_id}", False), None
        origin = self._origin(context)
        current_profile = str(context.profile_user_id or "")
        current_session = str(context.session_id or "")
        character = str(context.character_pack_id or "")
        private = self._private_scope(context)
        if private and private.shared:
            current_profile, current_session = private.memory_identity
            if private.channel == "desktop":
                origin = {"kind": "direct", "recipient": f"user:{self.master_qq}"}
        if selector == "current":
            target_profile, target_session = current_profile, current_session
            conversation = "current"
        elif selector == "master":
            target_profile = target_session = "master"
            conversation = "master"
        else:
            match = _GROUP.fullmatch(selector)
            if match is None:
                return None, self._error("invalid_conversation")
            target_profile = target_session = f"qq_group_shared_{match.group(1)}"
            conversation = selector
        external = (target_profile, target_session) != (current_profile, current_session)
        if external:
            if not self.enabled():
                return None, self._error("cross_conversation_disabled")
            if origin is None or (origin.get("kind") != "group" and origin.get("recipient") != f"user:{self.master_qq}"):
                return None, self._error("forbidden")
        if selector != "current" and origin is None:
            return None, self._error("forbidden")
        if conversation.startswith("group:") and external:
            checker = getattr(self.store, "has_trusted_qq_group_session", None)
            if not callable(checker) or not checker(target_session, character_pack_id=character):
                return None, self._error("conversation_unavailable")
        if conversation == "current" and origin is not None:
            if origin.get("kind") == "group":
                conversation = str(origin.get("recipient") or "current")
            elif origin.get("recipient") == f"user:{self.master_qq}":
                conversation = "master"
        label = "主人私聊" if conversation == "master" else (f"QQ群 {conversation[6:]}" if conversation.startswith("group:") else "当前会话")
        return MemoryReadTarget(target_profile, target_session, character, conversation, label,
                                self._actor(origin, context), external), None

    def _known_memory_targets(self, context: Any, memory_ids: list[str]) -> dict[str, set[str]]:
        """Use provider-visible memory tool headers, never user text or a last-group cache."""
        from .capability_discovery import visible_tool_result_messages

        request = getattr(context, "request_context", {})
        history = request.get("_host_visible_tool_history", []) if isinstance(request, Mapping) else []
        known = {memory_id: set() for memory_id in memory_ids}

        def visit(value: Any, conversation: str = "") -> None:
            if isinstance(value, list):
                for item in value:
                    visit(item, conversation)
            elif isinstance(value, Mapping):
                # A failed lookup only echoes the requested scope; it does not
                # establish where the requested ID actually belongs.
                if value.get("ok") is False or (value.get("status") is not None
                        and value["status"] not in ("ok", "found", "partial", "replaced")):
                    return
                conversation = str(value.get("conversation") or conversation)
                for key in ("memory_id", "source_id"):
                    memory_id = value.get(key)
                    if isinstance(memory_id, str) and memory_id in known and conversation:
                        known[memory_id].add(conversation)
                for key in ("cards", "matches", "items", "result", "sources", "card", "navigation"):
                    if key in value:
                        visit(value[key], conversation)

        headers = ("【MemCore 记忆目录】", "【MemCore 记忆证据】", "【MemCore 原始时间线】", "【MemCore 记忆检索】", "【MemCore 语义检索状态】", "【记忆目录】", "【记忆节点】", "【记忆检索】")
        readback_kinds = {f"tool.{name}.result" for name in ("browse_memory", "open_memory", "retrieve_memory", "read_memory_timeline")}
        for message in visible_tool_result_messages(history):
            content = str(message.get("content") or "")
            if not content.startswith(headers):
                continue
            try:
                start = content.index("{")
                payload, used = json.JSONDecoder().raw_decode(content[start:])
            except (ValueError, json.JSONDecodeError):
                continue
            visit(payload)
            # A typed, linked memory observation may be read back after settlement.
            # Ordinary raw messages containing quoted headers cannot authorize a scope.
            tail = content[start + used:]
            while isinstance(payload, Mapping):
                record = payload.get("result")
                if not (payload.get("view") == "content" and payload.get("node_type") == "raw"
                        and isinstance(record, Mapping) and record.get("kind") in readback_kinds
                        and record.get("turn_role") == "observation" and record.get("relation_status") == "linked"
                        and record.get("origin") in {"environment", "tool"}):
                    break
                starts = [(tail.find(header), header) for header in headers if header in tail]
                if not starts:
                    break
                position, header = min(starts)
                remainder = tail[position + len(header):].lstrip()
                try:
                    payload, used = json.JSONDecoder().raw_decode(remainder)
                except (ValueError, json.JSONDecodeError):
                    break
                visit(payload)
                tail = remainder[used:]
        return known

    def prepare(self, *, context: Any, call: Mapping[str, Any], tool: str) -> tuple[MemoryReadTarget | None, dict[str, Any], dict[str, Any] | None]:
        if "cross_conversation" in call:
            return None, {}, self._error("invalid_conversation")
        requested = call.get("conversation") if call.get("conversation") is not None else "current"
        if not isinstance(requested, str) or not requested or requested != requested.strip():
            return None, {}, self._error("invalid_conversation")
        match_ref = call.get("match_ref")
        if match_ref and (tool != "open_memory" or any(value is not None for key, value in call.items() if key not in {"type", "match_ref"})):
            return None, {}, self._error("keyword_source_options_are_embedded")
        reference_key = "match_ref" if match_ref else "cursor"
        cursor = match_ref or call.get("cursor")
        native = {str(key): value for key, value in call.items() if key not in {"type", "conversation"}}
        if cursor:
            if not isinstance(cursor, str) or len(cursor) > 16384:
                return None, {}, self._error("cursor_invalid")
            saved = self._decode(cursor)
            if saved is None or saved.get("tool") != tool or saved.get("bot") != self.bot_id or saved.get("reference_type", "cursor") != reference_key:
                return None, {}, self._error("cursor_invalid")
            if not self._cursor_matches(saved, context):
                return None, {}, self._error("cursor_invalid")
            selector = str(saved.get("conversation") or "")
            if call.get("conversation") is not None and requested != selector:
                return None, {}, self._error("cursor_invalid")
            if any(key not in {reference_key, "type", "conversation"} and value is not None for key, value in call.items()):
                return None, {}, self._error("cursor_invalid")
            native = {reference_key: str(saved.get("native") or "")}
            if not native[reference_key]:
                return None, {}, self._error("cursor_invalid")
        else:
            selector = requested
            native.pop("cursor", None)
            selected_ids = [str(call.get(key)) for key in ("memory_id", "within_memory_id", "anchor_source_id") if call.get(key)]
            batch_ids = call.get("memory_ids")
            if isinstance(batch_ids, (list, tuple)):
                selected_ids.extend(item for item in batch_ids if isinstance(item, str))
            if selected_ids:
                known = self._known_memory_targets(context, selected_ids)
                targets = set().union(*known.values())
                if any(len(values) > 1 for values in known.values()):
                    return None, {}, self._error("ambiguous_memory_conversation")
                if call.get("conversation") is not None:
                    authorized, rejection = self._authorize(context, selector)
                    if rejection is not None:
                        return None, {}, rejection
                    if targets and any(value != authorized.conversation for value in targets):
                        return None, {}, self._error("memory_conversation_conflict")
                elif len(targets) == 1:
                    selector = next(iter(targets))
                elif len(targets) > 1:
                    error = self._error("memory_ids_span_multiple_conversations")
                    error["suggested_next_actions"] = [
                        {"memory_ids": [mid for mid, scopes in known.items() if conversation in scopes], "conversation": conversation, "view": call.get("view", "card")}
                        for conversation in sorted(targets)
                    ]
                    return None, {}, error
        target, error = self._authorize(context, selector)
        if error is not None:
            return None, {}, error
        return target, native, None

    def project(
        self, *, context: Any, target: MemoryReadTarget, tool: str, result: Mapping[str, Any]
    ) -> dict[str, Any]:
        output = dict(result)
        output["conversation"] = target.conversation
        output["source_label"] = target.source_label
        for key in ("matches", "messages", "cards", "items"):
            if isinstance(output.get(key), list):
                output[key] = [
                    {**item, "conversation": target.conversation, "source_label": target.source_label}
                    if isinstance(item, dict)
                    else item
                    for item in output[key]
                ]
        for key in ("navigation", "suggested_next_actions"):
            if isinstance(output.get(key), list):
                updated = []
                for item in output[key]:
                    if not isinstance(item, dict):
                        updated.append(item)
                        continue
                    entry = {**item, "conversation": target.conversation, "source_label": target.source_label}
                    if isinstance(entry.get("arguments"), Mapping):
                        entry["arguments"] = {**entry["arguments"], "conversation": target.conversation}
                    updated.append(entry)
                output[key] = updated

        signed_cursors = {}

        def wrap_cursor(native: str, reference_type: str = "cursor") -> str:
            cache_key = (native, reference_type)
            if cache_key in signed_cursors:
                return signed_cursors[cache_key]
            signed_cursors[cache_key] = self._encode(
                {
                    "tool": "open_memory" if reference_type == "match_ref" else tool,
                    "reference_type": reference_type,
                    "bot": self.bot_id,
                    "profile": self._cursor_identity(context)[0],
                    "session": self._cursor_identity(context)[1],
                    "character": str(context.character_pack_id or ""),
                    "actor": target.actor_id,
                    "conversation": target.conversation,
                    "native": native,
                }, compressed=reference_type == "match_ref" or native.startswith(("kmr1:", "kmr2:"))
            )
            return signed_cursors[cache_key]

        if tool == "browse_memory":
            for card in output.get("cards") or []:
                if isinstance(card, dict) and card.get("match_ref"):
                    card["match_ref"] = wrap_cursor(str(card["match_ref"]), "match_ref")

        def wrap_navigation(value: Any) -> Any:
            if isinstance(value, list):
                return [wrap_navigation(item) for item in value]
            if not isinstance(value, Mapping):
                return value
            projected = {key: wrap_navigation(item) for key, item in value.items()}
            for key in ("next_cursor", "cursor"):
                if isinstance(projected.get(key), str) and projected[key]:
                    projected[key] = wrap_cursor(projected[key])
            for key in ("navigation", "suggested_next_actions"):
                if isinstance(projected.get(key), list):
                    for action in projected[key]:
                        if isinstance(action, dict):
                            action["conversation"] = target.conversation
                            if isinstance(action.get("arguments"), Mapping):
                                action["arguments"] = {**action["arguments"], "conversation": target.conversation}
            return projected

        # Every source tree has its own cursor. None may bypass the origin and
        # target checks in prepare(), including a cursor copied into a receipt.
        if tool == "open_memory" and output.get("view") == "sources":
            output = wrap_navigation(output)
        else:
            # Existing fuzzy/catalog reads only have shallow page cursors.
            # Do not walk their evidence/lineage bodies for the new feature.
            if output.get("next_cursor"):
                output["next_cursor"] = wrap_cursor(str(output["next_cursor"]))
            for key in ("coverage", "result", "receipt"):
                page = output.get(key)
                if isinstance(page, Mapping) and page.get("next_cursor"):
                    output[key] = {**page, "next_cursor": wrap_cursor(str(page["next_cursor"]))}
        coverage = output.get("coverage")
        nested = output.get("result")
        next_cursor = (
            output.get("next_cursor")
            or (coverage.get("next_cursor") if isinstance(coverage, Mapping) else "")
            or (nested.get("next_cursor") if isinstance(nested, Mapping) else "")
            or (output.get("receipt", {}).get("next_cursor") if isinstance(output.get("receipt"), Mapping) else "")
        )
        if next_cursor:
            output["next_cursor"] = next_cursor
        if isinstance(nested, Mapping) and "page_complete" in nested:
            output["page_complete"] = bool(nested["page_complete"])
        if isinstance(output.get("receipt"), Mapping):
            receipt = dict(output["receipt"])
            receipt["selector"] = {**dict(receipt.get("selector") or {}), "conversation": target.conversation}
            if next_cursor:
                receipt["next_cursor"] = next_cursor
            if tool == "open_memory" and output.get("view") == "sources":
                # Signed host cursors are larger than package cursors; keep the
                # compact anchor bounded while preserving the full observation.
                if len(json.dumps(receipt, ensure_ascii=False).encode("utf-8")) > 4096:
                    if receipt.get("next_cursor"):
                        receipt["cursor_in_observation"] = True
                    receipt["next_cursor"] = ""
                    for node in receipt.get("source_nodes") or []:
                        if node.get("next_cursor"):
                            node["next_cursor"] = ""
                            node["cursor_in_observation"] = True
                    receipt["navigation_omitted"] = True
                for key in ("returned_logical_unit_ids", "returned_source_ids", "returned_memory_ids"):
                    values = receipt.get(key) or []
                    while values and len(json.dumps(receipt, ensure_ascii=False).encode("utf-8")) > 4096:
                        values.pop()
                        receipt[key + "_omitted_count"] = receipt.get(key + "_omitted_count", 0) + 1
                if len(json.dumps(receipt, ensure_ascii=False).encode("utf-8")) > 4096:
                    receipt = {
                        key: receipt[key]
                        for key in (
                            "operation",
                            "status",
                            "request_hash",
                            "result_hash",
                            "navigation_omitted",
                        )
                        if key in receipt
                    }
                    receipt["selector"] = {"conversation": target.conversation, "view": "sources"}
            output["receipt"] = receipt
        if str(output.get("text") or "").strip() and not (tool == "open_memory" and output.get("view") == "sources"):
            output["text"] = f"【来源：{target.source_label}；conversation={target.conversation}】\n" + str(
                output["text"]
            )
        return output

    def list_conversations(self, *, context: Any, cursor: str = "", limit: int = 20) -> dict[str, Any]:
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 50:
            return self._error("invalid_limit")
        origin = self._origin(context)
        current, error = self._authorize(context, "current")
        if error is not None or current is None:
            return error or self._error("forbidden")
        allowed = bool(self.enabled() and origin is not None and (
            origin.get("kind") == "group" or origin.get("recipient") == f"user:{self.master_qq}"))
        after = ""
        stage = 0
        if cursor:
            saved = self._decode(cursor) if isinstance(cursor, str) else None
            if saved is None or saved.get("tool") != "list_memory_conversations" or saved.get("bot") != self.bot_id:
                return self._error("cursor_invalid")
            if not self._cursor_matches(saved, context):
                return self._error("cursor_invalid")
            after = str(saved.get("after") or "")
            stage = saved.get("stage")
            if not isinstance(stage, int) or isinstance(stage, bool) or stage not in (0, 1, 2):
                return self._error("cursor_invalid")
        if not allowed:
            if cursor:
                return self._error("cross_conversation_disabled" if not self.enabled() else "forbidden")
            return {"ok": True, "status": "ok", "items": [{"conversation": current.conversation,
                    "display_name": current.source_label, "current": True}], "next_cursor": "", "complete": True}
        prefix = [{"conversation": current.conversation, "display_name": current.source_label, "current": True}]
        if current.conversation != "master":
            prefix.append({"conversation": "master", "display_name": "主人私聊", "current": False})
        if stage > len(prefix):
            return self._error("cursor_invalid")
        entries = prefix[stage:stage + limit]
        stage += len(entries)
        finder = getattr(self.store, "list_trusted_qq_group_sessions", None)
        if not callable(finder):
            return self._error("conversation_directory_unavailable")
        remaining = limit - len(entries)
        more = stage < len(prefix)
        if not more:
            groups = finder(character_pack_id=current.character_pack_id, after_session_id=after,
                            limit=remaining + 2)
            group_rows = []
            for row in groups:
                session = str(row.get("session_id") or "")
                group_id = session.removeprefix("qq_group_shared_")
                if not group_id.isdigit() or not group_id or session == current.session_id:
                    continue
                group_rows.append({"conversation": f"group:{group_id}", "display_name": f"QQ群 {group_id}",
                                   "current": False, "_session": session})
            page_groups = group_rows[:remaining]
            entries.extend({key: value for key, value in row.items() if key != "_session"} for row in page_groups)
            if page_groups:
                after = page_groups[-1]["_session"]
            more = len(group_rows) > remaining
        next_cursor = ""
        if more:
            next_cursor = self._encode({"tool": "list_memory_conversations", "bot": self.bot_id,
                "profile": self._cursor_identity(context)[0], "session": self._cursor_identity(context)[1],
                "character": str(context.character_pack_id or ""), "actor": current.actor_id,
                "after": after, "stage": stage})
        unknown_checker = getattr(self.store, "qq_group_directory_has_unverified_sessions", None)
        directory_complete = not (callable(unknown_checker) and unknown_checker(
            character_pack_id=current.character_pack_id))
        return {"ok": True, "status": "ok", "items": entries, "next_cursor": next_cursor,
                "complete": not bool(next_cursor), "directory_complete": directory_complete,
                **({"directory_note": "unverified_historical_qq_sessions_omitted"}
                   if not directory_complete else {})}

    def _encode(self, payload: Mapping[str, Any], *, compressed: bool = False) -> str:
        body = json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
        if compressed:
            body = zlib.compress(body, 9)
        tag = hmac.new(self._key, body, hashlib.sha256).digest()
        return f"{'mrc2' if compressed else _CURSOR_PREFIX}.{_b64(body)}.{_b64(tag)}"

    def _decode(self, token: str) -> dict[str, Any] | None:
        parts = str(token).split(".")
        if len(parts) != 3 or parts[0] not in {_CURSOR_PREFIX, "mrc2"}:
            return None
        try:
            body, tag = _unb64(parts[1]), _unb64(parts[2])
            if not hmac.compare_digest(tag, hmac.new(self._key, body, hashlib.sha256).digest()):
                return None
            if parts[0] == "mrc2":
                inflater = zlib.decompressobj()
                body = inflater.decompress(body, 65537)
                if len(body) > 65536 or not inflater.eof:
                    return None
            value = json.loads(body.decode("ascii"))
            return value if isinstance(value, dict) else None
        except (ValueError, UnicodeError, json.JSONDecodeError, zlib.error):
            return None
