from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable

from openai import OpenAI

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from companion_v01.llm_runtime import LLMRuntime
from companion_v01.runtime_settings import BotSettingsView


api_key = os.environ.get("AKANE_DEEPSEEK_PROBE_KEY", "").strip()
if not api_key:
    raise SystemExit("probe_key_missing")

model = os.environ.get("AKANE_DEEPSEEK_PROBE_MODEL", "deepseek-v4-flash").strip()
delay_seconds = max(1.0, float(os.environ.get("AKANE_DEEPSEEK_PROBE_DELAY_SECONDS", "3") or 3))
target_ratio = max(0.0, min(1.0, float(os.environ.get("AKANE_DEEPSEEK_PROBE_TARGET_RATIO", "0.85") or 0.85)))
required_streak = max(2, int(os.environ.get("AKANE_DEEPSEEK_PROBE_REQUIRED_STREAK", "3") or 3))
max_rounds = max(required_streak + 2, int(os.environ.get("AKANE_DEEPSEEK_PROBE_MAX_ROUNDS", "9") or 9))

client = OpenAI(
    api_key=api_key,
    base_url="https://api.deepseek.com",
    timeout=120,
    max_retries=0,
)

large_system_prompt = "Stable Akane system instructions, persona, output contract, and safety boundary. " * 420
stable_memory_prefix = "Stable rendered Akane memory, capability context, and earlier conversation prefix. " * 420
akane_system_prompt = (
    "You are Akane. Follow the stable persona, safety boundary, tool policy, and JSON output contract. " * 95
)
akane_runtime_context = (
    "Stable Akane rendered memory layers, resource context, capability context, and conversation prefix. " * 300
)
tools = [
    {
        "type": "function",
        "function": {
            "name": f"stable_tool_{index}",
            "description": (f"Stable tool schema {index}. " * 24).strip(),
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    }
    for index in range(14)
]
fixed_history = [
    {
        "role": "assistant" if index % 2 == 0 else "user",
        "content": f"Stable rendered history turn {index}. " * 4,
    }
    for index in range(10)
]


def stream_call(
    messages: list[dict[str, Any]],
    *,
    native_tools: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, int | float], str]:
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},
        "temperature": 0,
        "max_tokens": 24,
    }
    if native_tools is not None:
        kwargs.update(
            tools=native_tools,
            tool_choice="none",
            parallel_tool_calls=True,
        )
    stream = client.chat.completions.create(**kwargs)
    usage = None
    text_parts: list[str] = []
    try:
        for chunk in stream:
            if getattr(chunk, "usage", None) is not None:
                usage = chunk.usage
            choices = list(getattr(chunk, "choices", None) or [])
            if choices:
                content = getattr(getattr(choices[0], "delta", None), "content", None)
                if content:
                    text_parts.append(str(content))
    finally:
        close = getattr(stream, "close", None)
        if callable(close):
            close()
    if usage is None:
        raise RuntimeError("stream_usage_missing")
    prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
    hit_tokens = int(getattr(usage, "prompt_cache_hit_tokens", 0) or 0)
    miss_tokens = int(getattr(usage, "prompt_cache_miss_tokens", 0) or 0)
    if not hit_tokens:
        details = getattr(usage, "prompt_tokens_details", None)
        hit_tokens = int(getattr(details, "cached_tokens", 0) or 0)
    return (
        {
            "input_tokens": prompt_tokens,
            "cached_tokens": hit_tokens,
            "cache_miss_tokens": miss_tokens,
            "hit_ratio": round(hit_tokens / prompt_tokens, 6) if prompt_tokens else 0.0,
        },
        "".join(text_parts),
    )


def emit(label: str, round_index: int, stats: dict[str, int | float]) -> None:
    print(
        json.dumps(
            {"phase": label, "round": round_index, **stats},
            ensure_ascii=False,
            sort_keys=True,
        ),
        flush=True,
    )


def run_standard_multiturn() -> list[dict[str, int | float]]:
    """DeepSeek's documented A+B -> A+B+C append-only conversation."""

    history: list[dict[str, Any]] = []
    results: list[dict[str, int | float]] = []
    streak = 0
    for round_index in range(1, max_rounds + 1):
        user_message = {
            "role": "user",
            "content": f"Cache probe round {round_index}. Reply with only the round number.",
        }
        stats, assistant_text = stream_call(
            [{"role": "system", "content": large_system_prompt}, *history, user_message]
        )
        emit("standard_append_only", round_index, stats)
        results.append(stats)
        history.extend(
            [
                user_message,
                {"role": "assistant", "content": assistant_text or str(round_index)},
            ]
        )
        streak = streak + 1 if float(stats["hit_ratio"]) >= target_ratio else 0
        if streak >= required_streak:
            return results
        time.sleep(delay_seconds)
    return results


def run_until_stable(
    label: str,
    build_messages: Callable[[int], list[dict[str, Any]]],
    *,
    native_tools: list[dict[str, Any]] | None = None,
) -> list[dict[str, int | float]]:
    """Run A+B, A+C, A+D... until DeepSeek persists the common A unit."""

    results: list[dict[str, int | float]] = []
    streak = 0
    for round_index in range(1, max_rounds + 1):
        stats, _ = stream_call(build_messages(round_index), native_tools=native_tools)
        emit(label, round_index, stats)
        results.append(stats)
        streak = streak + 1 if float(stats["hit_ratio"]) >= target_ratio else 0
        if streak >= required_streak:
            return results
        time.sleep(delay_seconds)
    return results


def run_real_akane_payload_builder() -> list[dict[str, int | float]]:
    """Use LLMRuntime._build_completion_kwargs, not a hand-written payload."""

    settings = BotSettingsView(
        text_api_key=api_key,
        text_base_url="https://api.deepseek.com",
        text_model_name=model,
        text_api_protocol="openai",
        aux_api_key=api_key,
        aux_base_url="https://api.deepseek.com",
        aux_model_name=model,
        aux_api_protocol="openai",
        chat_api_key=api_key,
        chat_base_url="https://api.deepseek.com",
        chat_model_name=model,
        chat_api_protocol="openai",
        prompt_cache_hints_enabled=True,
        prompt_cache_hints_force=False,
        prompt_cache_namespace="akane-deepseek-probe",
        llm_context_window=0,
        llm_auto_compact_token_limit=0,
    )
    runtime = LLMRuntime(
        log_dir=Path(os.devnull),
        instance_id="deepseek-cache-probe",
        settings=settings,
    )
    base_history: list[dict[str, Any]] = [
        {"role": "user", "content": akane_runtime_context},
        *fixed_history,
    ]
    appended_history: list[dict[str, Any]] = []
    results: list[dict[str, int | float]] = []
    streak = 0
    for round_index in range(1, max_rounds + 1):
        user_prompt = (
            "Current Akane request-time context, current state, and output reminder. " * 50
            + f" Current user message for round {round_index}."
        )
        payload = runtime._build_completion_kwargs(
            bundle=runtime.chat,
            system_prompt=akane_system_prompt,
            user_prompt=user_prompt,
            temperature=0,
            stream=True,
            json_mode=True,
            prompt_cache_key="chat:final:deepseek-real-builder",
            native_tools=tools,
            native_tool_choice="auto",
            history_turns=[*base_history, *appended_history],
        )
        # Keep the live probe cheap. This field is after prompt construction and
        # does not change the tokenized input prefix under test.
        payload["max_tokens"] = 24
        stream = runtime.chat.client.chat.completions.create(**payload)
        usage = None
        try:
            for chunk in stream:
                if getattr(chunk, "usage", None) is not None:
                    usage = chunk.usage
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                close()
        if usage is None:
            raise RuntimeError("akane_builder_stream_usage_missing")
        prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        hit_tokens = int(getattr(usage, "prompt_cache_hit_tokens", 0) or 0)
        miss_tokens = int(getattr(usage, "prompt_cache_miss_tokens", 0) or 0)
        stats: dict[str, int | float] = {
            "input_tokens": prompt_tokens,
            "cached_tokens": hit_tokens,
            "cache_miss_tokens": miss_tokens,
            "hit_ratio": round(hit_tokens / prompt_tokens, 6) if prompt_tokens else 0.0,
        }
        emit("real_akane_payload_builder", round_index, stats)
        results.append(stats)
        appended_history.extend(
            [
                {"role": "user", "content": f"Bare persisted user message for round {round_index}."},
                {"role": "assistant", "content": f"Bare persisted assistant reply for round {round_index}."},
            ]
        )
        streak = streak + 1 if float(stats["hit_ratio"]) >= target_ratio else 0
        if streak >= required_streak:
            return results
        time.sleep(delay_seconds)
    return results


real_builder_only = os.environ.get("AKANE_DEEPSEEK_REAL_BUILDER_ONLY", "").strip() == "1"
standard_results = [] if real_builder_only else run_standard_multiturn()
replacement_results = (
    []
    if real_builder_only
    else run_until_stable(
        "akane_replaced_dynamic_tail",
        lambda round_index: [
            {"role": "system", "content": large_system_prompt},
            {"role": "user", "content": stable_memory_prefix},
            *fixed_history,
            {
                "role": "user",
                "content": (
                    f"Dynamic Akane current-turn context {round_index}. "
                    "This final block is intentionally replaced rather than replayed verbatim next round."
                ),
            },
        ],
        native_tools=tools,
    )
)
akane_builder_results = run_real_akane_payload_builder()


def stable(results: list[dict[str, int | float]]) -> bool:
    return len(results) >= required_streak and all(
        float(item["hit_ratio"]) >= target_ratio for item in results[-required_streak:]
    )


summary = {
    "model": model,
    "target_ratio": target_ratio,
    "required_streak": required_streak,
    "standard_append_only_stable": None if real_builder_only else stable(standard_results),
    "akane_replaced_dynamic_tail_stable": None if real_builder_only else stable(replacement_results),
    "real_akane_payload_builder_stable": stable(akane_builder_results),
}
print(json.dumps({"summary": summary}, ensure_ascii=False, sort_keys=True), flush=True)
required_results = [summary["real_akane_payload_builder_stable"]]
if not real_builder_only:
    required_results.extend(
        [summary["standard_append_only_stable"], summary["akane_replaced_dynamic_tail_stable"]]
    )
if not all(required_results):
    raise SystemExit(2)
