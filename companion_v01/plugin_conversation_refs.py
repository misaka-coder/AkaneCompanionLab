"""Host-owned opaque conversation references for delayed plugin work."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
from typing import Any, Mapping


class PluginConversationReferenceAuthority:
    """Issue and verify restart-stable, instance-scoped opaque references."""

    def __init__(self, key_path: Path, *, instance_id: str) -> None:
        self._key_path = Path(key_path)
        self._instance_id = str(instance_id or "").strip()
        if not self._instance_id:
            raise ValueError("instance_id_required")
        self._key = self._load_or_create_key()

    def issue(self, context: Any) -> str:
        session_id = str(getattr(context, "session_id", "") or "").strip()
        profile_user_id = str(getattr(context, "profile_user_id", "") or "").strip()
        character_pack_id = str(getattr(context, "character_pack_id", "") or "").strip()
        client_mode = str(getattr(context, "client_mode", "") or "").strip().lower()
        request_context = getattr(context, "request_context", None)
        request_context = request_context if isinstance(request_context, Mapping) else {}
        if client_mode == "desktop_pet":
            return self.issue_desktop(
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=character_pack_id,
            )
        if client_mode not in {"qq", "qq_text"}:
            return ""
        # QQ turn payload.user_id is the Engine session key, not a QQ account.
        # All delayed work uses the same channel-owned delivery context.
        delivery = request_context.get("qq_delivery_context")
        if not isinstance(delivery, Mapping):
            return ""
        group_id = _positive_id(delivery.get("group_id"))
        user_id = _positive_id(delivery.get("user_id"))
        return self.issue_qq(
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
            user_id=user_id,
            group_id=group_id,
            # A system event has no QQ sender, but can retain a signed causal
            # actor in the host turn metadata for permissions and later work.
            actor_stable_id=str(request_context.get("actor_stable_id") or ""),
            actor_profile_user_id=str(request_context.get("actor_profile_user_id") or ""),
        )

    def issue_qq(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str,
        user_id: int = 0,
        group_id: int = 0,
        actor_stable_id: str = "",
        actor_profile_user_id: str = "",
    ) -> str:
        profile_user_id = str(profile_user_id or "").strip()
        session_id = str(session_id or "").strip()
        character_pack_id = str(character_pack_id or "").strip()
        if not profile_user_id or not session_id or not character_pack_id:
            return ""
        group_id = _positive_id(group_id)
        user_id = _positive_id(user_id)
        if group_id:
            kind, recipient_id = "group", f"group:{group_id}"
        elif user_id:
            kind, recipient_id = "direct", f"user:{user_id}"
        else:
            return ""
        payload = {
            "v": 1,
            "instance": self._instance_id,
            "channel": "qq",
            "kind": kind,
            "recipient": recipient_id,
            "session": session_id,
            "profile": profile_user_id,
            "character": character_pack_id,
        }
        actor = _qq_actor_id(actor_stable_id)
        actor_profile = _bounded_identity(actor_profile_user_id)
        if kind == "group" and actor:
            payload["actor"] = actor
            if actor_profile:
                payload["actor_profile"] = actor_profile
        body = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
        signature = hmac.new(self._key, body, hashlib.sha256).digest()
        return "acr1." + _b64(body) + "." + _b64(signature)

    def issue_desktop(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str,
    ) -> str:
        profile_user_id = str(profile_user_id or "").strip()
        session_id = str(session_id or "").strip()
        character_pack_id = str(character_pack_id or "").strip()
        if not profile_user_id or not session_id or not character_pack_id:
            return ""
        payload = {
            "v": 1,
            "instance": self._instance_id,
            "channel": "desktop_pet",
            "kind": "direct",
            "recipient": f"desktop:{session_id}",
            "session": session_id,
            "profile": profile_user_id,
            "character": character_pack_id,
        }
        body = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
        signature = hmac.new(self._key, body, hashlib.sha256).digest()
        return "acr1." + _b64(body) + "." + _b64(signature)

    def resolve(self, reference: str) -> dict[str, str] | None:
        parts = str(reference or "").strip().split(".")
        if len(parts) != 3 or parts[0] != "acr1":
            return None
        try:
            body = _unb64(parts[1])
            signature = _unb64(parts[2])
        except (ValueError, TypeError):
            return None
        if _b64(body) != parts[1] or _b64(signature) != parts[2]:
            return None
        expected = hmac.new(self._key, body, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            return None
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict) or payload.get("v") != 1 or payload.get("instance") != self._instance_id:
            return None
        result = {key: str(payload.get(key) or "").strip() for key in (
            "channel", "kind", "recipient", "session", "profile", "character",
            "actor", "actor_profile",
        )}
        if result["channel"] == "qq":
            if result["kind"] not in {"direct", "group"}:
                return None
        elif result["channel"] == "desktop_pet":
            if result["kind"] != "direct" or result["recipient"] != f'desktop:{result["session"]}':
                return None
        else:
            return None
        if not all(result[key] for key in ("recipient", "session", "profile", "character")):
            return None
        if result["actor"] and _qq_actor_id(result["actor"]) != result["actor"]:
            return None
        if result["actor_profile"] and _bounded_identity(result["actor_profile"]) != result["actor_profile"]:
            return None
        if result["actor_profile"] and not result["actor"]:
            return None
        return result

    def _load_or_create_key(self) -> bytes:
        try:
            encoded = self._key_path.read_text(encoding="ascii").strip()
            key = bytes.fromhex(encoded)
            if len(key) == 32:
                try:
                    os.chmod(self._key_path, 0o600)
                except OSError:
                    pass
                return key
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            raise RuntimeError("conversation_reference_key_invalid")
        self._key_path.parent.mkdir(parents=True, exist_ok=True)
        key = os.urandom(32)
        temporary = self._key_path.with_suffix(self._key_path.suffix + ".tmp")
        try:
            temporary.write_text(key.hex() + "\n", encoding="ascii")
            try:
                os.chmod(temporary, 0o600)
            except OSError:
                pass
            os.replace(temporary, self._key_path)
        finally:
            temporary.unlink(missing_ok=True)
        return key


def _positive_id(value: object) -> int:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0
    return parsed if 0 < parsed < 10**20 else 0


def _qq_actor_id(value: object) -> str:
    text = str(value or "").strip()
    number = _positive_id(text.removeprefix("qq:")) if text.startswith("qq:") else 0
    return f"qq:{number}" if number else ""


def _bounded_identity(value: object) -> str:
    text = str(value or "").strip()
    return text if text and len(text) <= 160 and "\x00" not in text else ""


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


__all__ = ["PluginConversationReferenceAuthority"]
