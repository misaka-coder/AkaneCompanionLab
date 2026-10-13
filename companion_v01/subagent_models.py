"""Resolve the shared child route once; never fall back to the parent on failure."""
from __future__ import annotations

import copy
from dataclasses import dataclass, replace
import hashlib
import json
import threading
from typing import Any

from .llm_runtime import ModelBundle, ModelExecutionTarget
from .runtime_settings import normalize_reasoning_effort, normalize_thinking_mode


def model_route_fingerprint(target: ModelExecutionTarget) -> str:
    client = target.bundle.client
    route = (target.role, target.model, target.protocol,
             str(getattr(client, "base_url", "") or getattr(client, "_akane_base_url", "")))
    return hashlib.sha256(json.dumps(route, ensure_ascii=False).encode()).hexdigest()


@dataclass(frozen=True)
class SubagentModelRoute:
    target: ModelExecutionTarget
    mode: str
    thinking_mode: str
    reasoning_effort: str
    max_output_tokens: int = 0
    timeout_seconds: float = 120.0

    def metadata(self) -> dict[str, str]:
        return {"model_role": self.target.role, "route_fingerprint": model_route_fingerprint(self.target),
                "route_mode": self.mode, "thinking_mode": self.thinking_mode,
                "max_output_tokens": str(self.max_output_tokens), "request_timeout": str(self.timeout_seconds)}


def resolve_subagent_route(engine: Any, *, parent_target: ModelExecutionTarget | None = None,
                           inherited_model: str = "", inherited_role: str = "chat") -> SubagentModelRoute:
    llm = engine.llm
    settings = llm._settings_view()
    config = settings.subagent
    mode = str(getattr(config, "mode", "inherit") or "inherit")
    if mode not in {"inherit", "shared_service", "independent"}:
        raise ValueError("subagent_model_mode_invalid")
    if mode == "inherit":
        target = parent_target or llm.resolve_turn_execution_target(
            has_real_images=inherited_role == "vision", chat_model_override=inherited_model)
        if not isinstance(target, ModelExecutionTarget):
            raise ValueError("subagent_model_route_unavailable")
        return SubagentModelRoute(target, mode, settings.llm_thinking_mode,
                                 llm._configured_reasoning_effort(target.bundle), settings.llm_chat_max_output_tokens, 0)
    model = str(getattr(config, "model", "") or "").strip()
    if not model:
        raise ValueError("subagent_model_required")
    timeout = float(getattr(config, "timeout_seconds", 120) or 120)
    if timeout <= 0:
        raise ValueError("subagent_request_timeout_invalid")
    if mode == "shared_service":
        key, base, protocol = settings.chat_api_key, settings.chat_base_url, settings.chat_api_protocol
    else:
        key = str(getattr(config, "api_key", "") or "")
        base = str(getattr(config, "base_url", "") or "")
        protocol = str(getattr(config, "protocol", "auto") or "auto")
    if not base or (not key and protocol != "ollama"):
        raise ValueError("subagent_model_service_incomplete")
    # A route client is shared by runs, not created at each tool step. Keys and
    # endpoint details stay in host memory and never enter the task receipt.
    cache_key = hashlib.sha256("\0".join((key, base, protocol, str(timeout))).encode()).hexdigest()
    lock = engine.__dict__.setdefault("_subagent_route_lock", threading.RLock())
    with lock:
        clients = engine.__dict__.setdefault("_subagent_route_clients", {})
        client = clients.get(cache_key)
        if client is None:
            client = llm._build_llm_client(api_key=key, base_url=base, protocol=protocol,
                                           timeout=timeout, max_retries=0)
            setattr(client, "_akane_bundle_role", "chat")
            clients[cache_key] = client
    target = ModelExecutionTarget("chat", ModelBundle(client, model), model, "subagent_" + mode)
    return SubagentModelRoute(target, mode,
        normalize_thinking_mode(getattr(config, "thinking_mode", "default")) or "default",
        normalize_reasoning_effort(getattr(config, "reasoning_effort", "")),
        max(0, int(getattr(config, "max_output_tokens", 0) or 0)), timeout)


def freeze_subagent_llm(parent: Any, route: SubagentModelRoute, *, reasoning_effort: str | None = None,
                        thinking_mode: str | None = None) -> Any:
    """Use captured clients/settings, with separate child metrics and errors."""
    llm = copy.copy(parent)
    effort = route.reasoning_effort if reasoning_effort is None else reasoning_effort
    llm.settings = replace(parent._settings_view(), llm_reasoning_effort=effort,
        llm_chat_reasoning_effort=effort, llm_thinking_mode=thinking_mode or route.thinking_mode,
        llm_chat_max_output_tokens=route.max_output_tokens)
    llm._bundle_lock = threading.RLock()
    llm.chat = route.target.bundle
    llm.memcore_summary = getattr(parent, "memcore_summary", None)
    llm._metrics_lock = threading.RLock()
    llm._metrics = {name: 0 for name in getattr(parent, "_metrics", {})}
    llm._last_error_lock = threading.RLock()
    llm._last_error = {}
    return llm
