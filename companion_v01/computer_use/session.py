"""Trusted caller scope is transport metadata, never a model argument."""
from contextvars import ContextVar
from hashlib import sha256

dispatch_scope = ContextVar("computer_use_dispatch_scope", default="")
dispatch_control = ContextVar("computer_use_dispatch_control", default=None)


def scope_key(profile_user_id, session_id):
    return sha256(f"{profile_user_id}\0{session_id}".encode()).hexdigest()
