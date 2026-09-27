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
    ) -> None:
        self.store = store
        self.bot_id = str(bot_id or "").strip()
        self.master_qq = str(master_qq or "").strip()
        self.enabled = enabled
        self.resolve_origin = resolve_origin
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
        origin = self._origin(context)
        current_profile = str(context.profile_user_id or "")
        current_session = str(context.session_id or "")
        character = str(context.character_pack_id or "")
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

    def prepare(self, *, context: Any, call: Mapping[str, Any], tool: str) -> tuple[MemoryReadTarget | None, dict[str, Any], dict[str, Any] | None]:
        if "cross_conversation" in call:
            return None, {}, self._error("invalid_conversation")
        requested = call.get("conversation", "current")
        if not isinstance(requested, str) or not requested or requested != requested.strip():
            return None, {}, self._error("invalid_conversation")
        cursor = call.get("cursor")
        native = {str(key): value for key, value in call.items() if key not in {"type", "conversation"}}
        if cursor:
            if not isinstance(cursor, str) or len(cursor) > 16384:
                return None, {}, self._error("cursor_invalid")
            saved = self._decode(cursor)
            if saved is None or saved.get("tool") != tool or saved.get("bot") != self.bot_id:
                return None, {}, self._error("cursor_invalid")
            if any(saved.get(key) != str(getattr(context, attr, "") or "") for key, attr in (
                ("profile", "profile_user_id"), ("session", "session_id"), ("character", "character_pack_id"),
            )) or saved.get("actor") != self._actor(self._origin(context), context):
                return None, {}, self._error("cursor_invalid")
            selector = str(saved.get("conversation") or "")
            if "conversation" in call and requested != selector:
                return None, {}, self._error("cursor_invalid")
            if any(key not in {"cursor", "type", "conversation"} for key in call):
                return None, {}, self._error("cursor_invalid")
            native = {"cursor": str(saved.get("native") or "")}
            if not native["cursor"]:
                return None, {}, self._error("cursor_invalid")
        else:
            selector = requested
            native.pop("cursor", None)
        target, error = self._authorize(context, selector)
        if error is not None:
            return None, {}, error
        return target, native, None

    def project(self, *, context: Any, target: MemoryReadTarget, tool: str, result: Mapping[str, Any]) -> dict[str, Any]:
        output = dict(result)
        output["conversation"] = target.conversation
        output["source_label"] = target.source_label
        for key in ("matches", "messages", "cards", "items"):
            if isinstance(output.get(key), list):
                output[key] = [
                    {**item, "conversation": target.conversation, "source_label": target.source_label}
                    if isinstance(item, dict) else item for item in output[key]
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
        if isinstance(output.get("snippets"), list):
            output["snippets"] = [f"【来源：{target.source_label}；conversation={target.conversation}】\n{item}"
                                  for item in output["snippets"]]
        next_cursor = str(output.get("next_cursor") or "")
        coverage = output.get("coverage")
        if not next_cursor and isinstance(coverage, Mapping):
            next_cursor = str(coverage.get("next_cursor") or "")
        receipt_source = output.get("receipt")
        if not next_cursor and isinstance(receipt_source, Mapping):
            next_cursor = str(receipt_source.get("next_cursor") or "")
        if next_cursor:
            wrapped = self._encode({
                "tool": tool, "bot": self.bot_id, "profile": str(context.profile_user_id or ""),
                "session": str(context.session_id or ""), "character": str(context.character_pack_id or ""),
                "actor": target.actor_id, "conversation": target.conversation, "native": next_cursor,
            })
            output["next_cursor"] = wrapped
            if isinstance(coverage, Mapping):
                output["coverage"] = {**coverage, "next_cursor": wrapped}
        if isinstance(receipt_source, Mapping):
            receipt = dict(receipt_source)
            old_selector = receipt.get("selector")
            receipt["selector"] = {**(dict(old_selector) if isinstance(old_selector, Mapping) else {}),
                                   "conversation": target.conversation}
            if next_cursor:
                receipt["next_cursor"] = output["next_cursor"]
            output["receipt"] = receipt
        if next_cursor and isinstance(output.get("result"), Mapping):
            nested = dict(output["result"])
            if nested.get("next_cursor") == next_cursor:
                nested["next_cursor"] = output["next_cursor"]
                output["result"] = nested
        if str(output.get("text") or "").strip():
            output["text"] = f"【来源：{target.source_label}；conversation={target.conversation}】\n" + str(output["text"])
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
            if any(saved.get(key) != str(getattr(context, attr, "") or "") for key, attr in (
                ("profile", "profile_user_id"), ("session", "session_id"), ("character", "character_pack_id"),
            )) or saved.get("actor") != current.actor_id:
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
                "profile": str(context.profile_user_id or ""), "session": str(context.session_id or ""),
                "character": str(context.character_pack_id or ""), "actor": current.actor_id,
                "after": after, "stage": stage})
        unknown_checker = getattr(self.store, "qq_group_directory_has_unverified_sessions", None)
        directory_complete = not (callable(unknown_checker) and unknown_checker(
            character_pack_id=current.character_pack_id))
        return {"ok": True, "status": "ok", "items": entries, "next_cursor": next_cursor,
                "complete": not bool(next_cursor), "directory_complete": directory_complete,
                **({"directory_note": "unverified_historical_qq_sessions_omitted"}
                   if not directory_complete else {})}

    def _encode(self, payload: Mapping[str, Any]) -> str:
        body = json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
        tag = hmac.new(self._key, body, hashlib.sha256).digest()
        return f"{_CURSOR_PREFIX}.{_b64(body)}.{_b64(tag)}"

    def _decode(self, token: str) -> dict[str, Any] | None:
        parts = str(token).split(".")
        if len(parts) != 3 or parts[0] != _CURSOR_PREFIX:
            return None
        try:
            body, tag = _unb64(parts[1]), _unb64(parts[2])
            if not hmac.compare_digest(tag, hmac.new(self._key, body, hashlib.sha256).digest()):
                return None
            value = json.loads(body.decode("ascii"))
            return value if isinstance(value, dict) else None
        except (ValueError, UnicodeError, json.JSONDecodeError):
            return None
