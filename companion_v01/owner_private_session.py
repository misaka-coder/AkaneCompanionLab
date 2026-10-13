"""Host-owned private companionship scope; delivery identities remain unchanged."""
from __future__ import annotations

from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from dataclasses import dataclass, replace
from functools import wraps
from typing import Any, Mapping
import threading
import time

from .dialogue_targets import is_service_domain


@dataclass(frozen=True)
class PrivateTurnScope:
    policy: Any
    profile: str
    session: str
    character: str
    channel: str
    shared: bool
    proactive_guard: Any = None

    @property
    def memory_identity(self) -> tuple[str, str]:
        return ("master", "master") if self.shared else (self.profile, self.session)


_CURRENT: ContextVar[PrivateTurnScope | None] = ContextVar("owner_private_turn", default=None)


class _ProactiveGuard:
    def __init__(self, policy, character):
        self.policy, self.character = policy, character
        self.revision = policy._input_revision(character)
        self.delivered = False

    def cancelled(self):
        return not self.delivered and self.policy._input_revision(self.character) != self.revision

    def before_event(self, event):
        from .model_request_control import ModelRequestCancelled
        if self.cancelled():
            raise ModelRequestCancelled("owner_private_new_input")
        if event.get("type") in {"speech_chunk", "speech_segment", "final"}:
            self.delivered = True


class OwnerPrivateSessionPolicy:
    def __init__(self, *, enabled: bool, master_qq: str, references: Any, manager: Any, store: Any = None) -> None:
        self.enabled = bool(enabled)
        self.master_qq = str(master_qq or "").strip()
        self.references = references
        self.manager = manager
        self.store = store
        self._input_lock = threading.RLock()
        self._inputs = {}
        self._input_receipts = {}

    def note_input(self, payload, *, source_id=""):
        """Host admission, before waiting for the shared turn. No history write."""
        scope = self.resolve(payload)
        if not scope.shared or payload.get("turn_kind") or payload.get("transient_user_message"):
            return
        key = (scope.character, scope.channel, str(source_id))
        with self._input_lock:
            if source_id and key in self._input_receipts:
                return
            if source_id:
                self._input_receipts[key] = True
                while len(self._input_receipts) > 2048:
                    self._input_receipts.pop(next(iter(self._input_receipts)))
            revision, _ = self._inputs.get(scope.character, (0, 0))
            self._inputs[scope.character] = (revision + 1, time.time())

    def _input_revision(self, character):
        with self._input_lock:
            return self._inputs.get(character, (0, 0))[0]

    def activity(self, character):
        result = self.store.owner_private_activity(character_pack_id=character)
        with self._input_lock:
            result["last_user_at"] = max(result["last_user_at"], self._inputs.get(character, (0, 0))[1])
        return result

    def resolve(self, payload: Mapping[str, Any], *, character: str = "") -> PrivateTurnScope:
        session = str(payload.get("user_id") or payload.get("session_id") or "default_session")
        profile = str(payload.get("real_user_id") or profile_from(payload) or session)
        character = str(character or payload.get("character_pack_id") or "").strip()
        reference = payload.get("_memory_desktop_ref") or payload.get("_memory_qq_ref")
        origin = self.references.resolve(reference) if isinstance(reference, str) else None
        matches = bool(origin and all(origin.get(key) == value for key, value in (
            ("profile", profile), ("session", session), ("character", character),
        )))
        channel, owner = "", False
        if matches and origin["channel"] == "desktop_pet":
            channel = "desktop"
            owner = (profile == "master" and origin.get("owner_private") == "true"
                     and str(payload.get("client_mode") or "") == "desktop_pet")
        elif matches and origin["channel"] == "qq":
            channel = "qq_private" if origin["kind"] == "direct" else "qq_group"
            owner = (channel == "qq_private" and profile == session == "master"
                     and origin["recipient"] == f"user:{self.master_qq}")
        shared = bool(self.enabled and self.master_qq and character and owner and not is_service_domain(character))
        return PrivateTurnScope(self, profile, session, character, channel, shared)

    def stamp_desktop(self, payload: dict[str, Any], *, owner_authenticated: bool, character: str) -> None:
        """Called only by authenticated host ingress, never by a model tool."""
        payload.pop("_memory_qq_ref", None)
        payload.pop("_memory_desktop_ref", None)
        payload.pop("memory_channel", None)
        profile = str(payload.get("real_user_id") or payload.get("user_id") or payload.get("session_id") or "default_session")
        session = str(payload.get("user_id") or payload.get("session_id") or "default_session")
        payload["_memory_desktop_ref"] = self.references.issue_desktop(
            profile_user_id=profile, session_id=session, character_pack_id=character,
            owner_private=bool(owner_authenticated and profile == "master"),
            client_capabilities=payload.get("client_capabilities"),
        )

    @contextmanager
    def scope(self, payload: Mapping[str, Any], *, character: str = ""):
        resolved = self.resolve(payload, character=character)
        from .turn_coordination import cancellation_scope
        guard = _ProactiveGuard(self, resolved.character) if (
            resolved.shared and payload.get("turn_kind") == "desktop_pet_proactive") else None
        if guard:
            resolved = replace(resolved, proactive_guard=guard)
        token = _CURRENT.set(resolved)
        try:
            with cancellation_scope(guard.cancelled) if guard else nullcontext():
                yield resolved
        finally:
            _CURRENT.reset(token)


def profile_from(payload: Mapping[str, Any]) -> str:
    return str(payload.get("profile_user_id") or "")


def current_private_scope(engine: Any) -> PrivateTurnScope | None:
    current = _CURRENT.get()
    return current if current and current.policy is getattr(engine, "owner_private_policy", None) else None


def current_private_store_scope(store: Any) -> PrivateTurnScope | None:
    current = _CURRENT.get()
    return current if current and current.shared and current.policy.store is store else None


@contextmanager
def private_turn_scope(engine: Any, payload: Mapping[str, Any]):
    policy = getattr(engine, "owner_private_policy", None)
    if policy is None:
        yield None
    else:
        character = engine._resolve_payload_character_pack_id(payload)
        with policy.scope(payload, character=character) as scope:
            yield scope


def memory_scope(method):
    """Normalize all namespace inputs before IDs, projections or cursors are built.

    Only the active engine's verified physical scope is an alias. Explicit
    other-conversation reads and calls outside that turn retain their identity.
    """
    @wraps(method)
    def scoped(self, *args, **kwargs):
        current = _CURRENT.get()
        if (current and current.shared and current.policy.manager is self
                and str(kwargs.get("character_pack_id") or "") == current.character
                and (str(kwargs.get("profile_user_id") or ""), str(kwargs.get("session_id") or ""))
                == (current.profile, current.session)):
            kwargs["profile_user_id"], kwargs["session_id"] = current.memory_identity
        return method(self, *args, **kwargs)
    return scoped


def desktop_care_scope(method):
    @wraps(method)
    def scoped(self, **kwargs):
        reference = kwargs.pop("conversation_ref", "")
        if not reference:
            return method(self, **kwargs)
        payload = {
            "user_id": kwargs.get("session_id") or kwargs.get("profile_user_id"),
            "real_user_id": kwargs.get("profile_user_id"),
            "character_pack_id": kwargs.get("character_pack_id"),
            "_memory_desktop_ref": reference,
            "client_mode": "desktop_pet",
        }
        with private_turn_scope(self, payload):
            return method(self, **kwargs)
    return scoped


def private_asset_scope(method):
    """Keep shared private file handles in the existing QQ material account.

    Called at service admission as well as store reads, so scheduled workers
    receive canonical ownership instead of depending on a thread-local context.
    URLs and the active client's delivery identity remain host concerns.
    """
    @wraps(method)
    def scoped(self, *args, **kwargs):
        current = _CURRENT.get()
        if (current and current.shared and current.policy.store is not None
                and (self is current.policy.store or getattr(self, "store", None) is current.policy.store)
                and (str(kwargs.get("profile_user_id") or ""), str(kwargs.get("session_id") or ""))
                == (current.profile, current.session)):
            kwargs["profile_user_id"], kwargs["session_id"] = current.memory_identity
        return method(self, *args, **kwargs)
    return scoped
