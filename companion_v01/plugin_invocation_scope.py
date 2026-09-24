"""Host-only propagation of leased plugin generations across invocation callbacks."""
from contextlib import contextmanager
from contextvars import ContextVar


generation_scopes: ContextVar[tuple] = ContextVar("plugin_generation_scopes", default=())


def current_generation_scope(runtime):
    return next((scope for scope in reversed(generation_scopes.get()) if scope.runtime is runtime), None)


@contextmanager
def use_generation_scopes(scopes):
    token = generation_scopes.set(tuple(scopes))
    try:
        yield
    finally:
        generation_scopes.reset(token)


@contextmanager
def plugin_turn_scope(engine):
    source = getattr(engine, "plugin_capability_source", None)
    factory = getattr(source, "turn_scope", None)
    if callable(factory):
        with factory():
            yield
    else:
        yield
