from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import config  # noqa: E402
from companion_v01.tool_orchestration_engine import native_web_search_tool_schema  # noqa: E402
from services.llm_client import build_llm_client  # noqa: E402


SECRET_PATTERNS = (
    re.compile(r"(?i)(bearer\s+)([A-Za-z0-9._~+/=-]{8,})"),
    re.compile(r"(?i)((?:api[_-]?key|authorization|x-api-key)\s*[:=]\s*)([^\s,;]+)"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Probe a provider/model for native tool calling behavior. "
            "Runs A/B/C/D checks and prints a suggested ProviderToolProfile row."
        )
    )
    parser.add_argument("--base-url", default="", help="Provider base URL. Defaults to CHAT_BASE_URL.")
    parser.add_argument("--protocol", default="", help="API protocol. Defaults to CHAT_API_PROTOCOL.")
    parser.add_argument("--api-key", default="", help="API key. Defaults to CHAT_API_KEY; never printed.")
    parser.add_argument("--model", default="", help="One model to probe. Defaults to CHAT_MODEL_NAME.")
    parser.add_argument(
        "--models",
        default="",
        help="Comma-separated models to probe. Overrides --model.",
    )
    parser.add_argument("--temperature", type=float, default=0.0)
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    base_url = str(args.base_url or config.CHAT_BASE_URL or "").strip()
    protocol = str(args.protocol or config.CHAT_API_PROTOCOL or "auto").strip()
    api_key = str(args.api_key or config.CHAT_API_KEY or "").strip()
    raw_models = str(args.models or args.model or config.CHAT_MODEL_NAME or "").strip()
    models = [item.strip() for item in raw_models.split(",") if item.strip()]
    if not base_url or not models:
        print(
            json.dumps(
                {
                    "status": "error",
                    "message": "base_url and model are required. Set CHAT_BASE_URL/CHAT_MODEL_NAME or pass flags.",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2

    client = build_llm_client(
        api_key=api_key,
        base_url=base_url,
        protocol=protocol,
        timeout=60.0,
        max_retries=0,
    )
    reports = [
        probe_model(
            client=client,
            base_url=base_url,
            protocol=protocol,
            model=model,
            temperature=float(args.temperature),
        )
        for model in models
    ]
    allowlist = ",".join(
        entry
        for entry in (str(report.get("suggested_allowlist_entry") or "").strip() for report in reports)
        if entry
    )
    print(
        json.dumps(
            {
                "status": "ok",
                # Ready to paste into NATIVE_TOOL_PROVIDER_ALLOWLIST. Empty means
                # no probed model supports native tools -> keep fail-closed.
                "suggested_native_tool_provider_allowlist": allowlist,
                "reports": reports,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def suggested_allowlist_entry(*, base_url: str, model: str, suggested: dict[str, Any]) -> str:
    """Render the ready-to-paste NATIVE_TOOL_PROVIDER_ALLOWLIST entry, or "" when
    the probe says native tools are unsupported.

    Empty is the fail-closed signal: no entry means the runtime keeps prompt-only
    JSON for this provider/model (it never auto-enables). The format mirrors the
    runtime parser (host:model[:json]); host is the base_url hostname so it
    matches how the runtime derives it from the live client.
    """
    if not bool(suggested.get("supports_native_tools")):
        return ""
    raw = str(base_url or "").strip()
    if raw and "://" not in raw:
        raw = f"https://{raw}"
    try:
        host = str(urlparse(raw).hostname or "").strip().lower()
    except Exception:
        host = ""
    clean_model = str(model or "").strip().lower()
    if not host or not clean_model:
        return ""
    entry = f"{host}:{clean_model}"
    if bool(suggested.get("native_tools_coexist_with_forced_json")):
        entry = f"{entry}:json"
    return entry


def probe_model(*, client: Any, base_url: str, protocol: str, model: str, temperature: float) -> dict[str, Any]:
    cases = [
        {
            "id": "A_only_tools",
            "tools": True,
            "forced_json": False,
            "prompt": "查一下今天上海天气。If needed, call web_search.",
        },
        {
            "id": "B_tools_forced_json",
            "tools": True,
            "forced_json": True,
            "prompt": "查一下今天上海天气。Return JSON only if you answer directly.",
        },
        {
            "id": "C_tools_prompt_json",
            "tools": True,
            "forced_json": False,
            "prompt": "查一下今天上海天气。Return JSON only if you answer directly.",
        },
        {
            "id": "D_no_tool_prompt_json",
            "tools": False,
            "forced_json": False,
            "prompt": "法国首都是哪里？Return only JSON: {\"speech\":\"...\",\"tool_call\":null}",
        },
    ]
    results = [
        run_probe_case(
            client=client,
            model=model,
            temperature=temperature,
            case=case,
        )
        for case in cases
    ]
    by_id = {str(item.get("id")): item for item in results}
    a_tools = int(by_id.get("A_only_tools", {}).get("tool_call_count") or 0) > 0
    b_tools = int(by_id.get("B_tools_forced_json", {}).get("tool_call_count") or 0) > 0
    c_tools = int(by_id.get("C_tools_prompt_json", {}).get("tool_call_count") or 0) > 0
    d_json = bool(by_id.get("D_no_tool_prompt_json", {}).get("content_is_json"))
    supports_native_tools = bool(a_tools or c_tools)
    suggested = {
        "supports_native_tools": supports_native_tools,
        "native_tools_coexist_with_forced_json": bool(supports_native_tools and b_tools),
        "json_strategy": "forced_json" if supports_native_tools and b_tools else "prompt_only",
        "native_call_shape": "openai_tool_calls",
        "verified": all(not item.get("error") for item in results),
        "probe_passed_prompt_json_no_tool": d_json,
    }
    return {
        "base_url": base_url,
        "protocol": protocol,
        "model": model,
        "cases": results,
        "suggested_profile": suggested,
        "suggested_allowlist_entry": suggested_allowlist_entry(
            base_url=base_url, model=model, suggested=suggested
        ),
    }


def run_probe_case(*, client: Any, model: str, temperature: float, case: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "temperature": temperature,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a provider capability probe. Use web_search when current public information "
                    "is needed. If you answer directly, return only a JSON object with speech and tool_call."
                ),
            },
            {"role": "user", "content": str(case["prompt"])},
        ],
    }
    if case.get("tools"):
        payload["tools"] = [native_web_search_tool_schema()]
        payload["tool_choice"] = "auto"
    if case.get("forced_json"):
        payload["response_format"] = {"type": "json_object"}

    try:
        response = client.chat.completions.create(**payload)
        choice = response.choices[0]
        message = choice.message
        content = str(_get(message, "content") or "")
        tool_calls = _get(message, "tool_calls")
        normalized_tool_calls = normalize_tool_calls(tool_calls)
        return {
            "id": str(case["id"]),
            "finish_reason": str(_get(choice, "finish_reason") or ""),
            "tool_call_count": len(normalized_tool_calls),
            "tool_calls": normalized_tool_calls,
            "content_preview": content[:240],
            "content_is_json": is_json_object(content),
            "error": "",
        }
    except Exception as exc:
        return {
            "id": str(case["id"]),
            "finish_reason": "",
            "tool_call_count": 0,
            "tool_calls": [],
            "content_preview": "",
            "content_is_json": False,
            "error": sanitize_error(str(exc or exc.__class__.__name__)),
        }


def normalize_tool_calls(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in value:
        function = _get(item, "function")
        normalized.append(
            {
                "id": str(_get(item, "id") or ""),
                "name": str(_get(function, "name") or ""),
                "arguments_preview": str(_get(function, "arguments") or "")[:240],
            }
        )
    return normalized


def is_json_object(value: str) -> bool:
    try:
        return isinstance(json.loads(str(value or "").strip()), dict)
    except Exception:
        return False


def _get(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        return value.get(key)
    return getattr(value, key, None)


def sanitize_error(value: str) -> str:
    text = str(value or "")
    for pattern in SECRET_PATTERNS:
        if pattern.groups >= 2:
            text = pattern.sub(lambda match: f"{match.group(1)}[redacted]", text)
        else:
            text = pattern.sub("[redacted]", text)
    if len(text) > 1000:
        return text[:997].rstrip() + "..."
    return text


if __name__ == "__main__":
    raise SystemExit(main())
