"""Gemini 多模态真实能力探针（只读、合成图片、不执行外部副作用）。

三个探针，全部使用程序生成的小尺寸 PNG，绝不使用生产用户图片：

  探针 1  视觉结构化回复：单图 + 精简结构化输出要求 + streaming + JSON
  探针 2  视觉 + 工具续轮：图片 + 问题 + 无副作用工具 schema → 宿主回填 → 第二请求收尾
  探针 3  跨模型接力：文本模型调用“加载图片”工具 → 工具返回合成图 → 下一个请求交给 Gemini

约束：
  - 不打印 API key、base64、完整请求体；
  - 无真实 Gemini 配置/凭据时输出结构化 ``live_probe_not_run``，不 mock；
  - 探针只读，不部署、不改配置、不改代码。

运行：
  python maintenance/_multimodal_gemini_probe_<suffix>.py
"""

from __future__ import annotations

import base64
import io
import json
import os
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import config
from services.llm_client import build_llm_client

PROBE_NAME = "multimodal_gemini"
LIVE_NOT_RUN = {
    "status": "live_probe_not_run",
    "probe": PROBE_NAME,
    "reason": "gemini_credentials_unavailable",
    "detail": (
        "未检测到指向 Gemini 的 VISION_*（protocol=gemini）或 CHAT_* 配置。"
        "如需真实跑通，请在 .env 配置 VISION_BASE_URL=https://generativelanguage.googleapis.com "
        "VISION_API_PROTOCOL=gemini VISION_MODEL_NAME=gemini-3.5-flash 以及对应 VISION_API_KEY。"
    ),
}

STRUCTURED_OUTPUT_REQ = (
    "只输出一个合法的 JSON object，不要输出多余文字，不要用 Markdown 包裹。"
    '字段固定为：{"speech":"给用户的一句自然中文回复","emotion":"happy|neutral|concerned|excited|sad",'
    '"memory_metadata":{},"tool_call":null}。speech 必须实际描述这张图里能看到的内容。'
)

TOOL_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "lookup_demo_value",
            "description": "返回一个无害的演示查询结果（探针专用，无副作用）。",
            "parameters": {
                "type": "object",
                "properties": {"key": {"type": "string", "description": "任意查询词"}},
                "required": ["key"],
            },
        },
    }
]


def _synthetic_png_bytes() -> bytes:
    """Generate a tiny deterministic PNG (a 64x64 solid color square)."""
    import struct
    import zlib

    width, height = 64, 64
    raw = b""
    row = b"\x00" + b"\x42\x9b\xd5" * width  # solid blue
    for _ in range(height):
        raw += row
    def chunk(kind: bytes, data: bytes) -> bytes:
        checksum = struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        return struct.pack(">I", len(data)) + kind + data + checksum
    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 9))
    png += chunk(b"IEND", b"")
    return png


def _data_url() -> str:
    return "data:image/png;base64," + base64.b64encode(_synthetic_png_bytes()).decode("ascii")


def _redact(text: str) -> str:
    for key in (
        str(getattr(config, "VISION_API_KEY", "") or ""),
        str(getattr(config, "CHAT_API_KEY", "") or ""),
    ):
        if key:
            text = text.replace(key, "<redacted>")
    return text


def _value(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def _stream_text(chunks: Any) -> str:
    parts: list[str] = []
    for chunk in chunks:
        choices = _value(chunk, "choices", []) or []
        if not choices:
            continue
        delta = _value(choices[0], "delta", None)
        content = _value(delta, "content", "") if delta is not None else ""
        if content:
            parts.append(str(content))
    return "".join(parts).strip()


def _resolve_gemini_target() -> tuple[str, Any] | None:
    """Return (model, client) when a Gemini route is configured, else None."""
    settings_source = config
    vision_base = str(getattr(settings_source, "VISION_BASE_URL", "") or "").strip()
    vision_proto = str(getattr(settings_source, "VISION_API_PROTOCOL", "auto") or "auto").strip().lower()
    vision_model = str(getattr(settings_source, "VISION_MODEL_NAME", "") or "").strip()
    vision_key = str(getattr(settings_source, "VISION_API_KEY", "") or "").strip()
    if vision_base and vision_proto == "gemini" and vision_model and vision_key:
        client = build_llm_client(
            api_key=vision_key,
            base_url=vision_base,
            protocol="gemini",
            timeout=60.0,
            max_retries=0,
        )
        return vision_model, client
    chat_base = str(getattr(settings_source, "CHAT_BASE_URL", "") or "").strip()
    chat_proto = str(getattr(settings_source, "CHAT_API_PROTOCOL", "auto") or "auto").strip().lower()
    chat_model = str(getattr(settings_source, "CHAT_MODEL_NAME", "") or "").strip()
    chat_key = str(getattr(settings_source, "CHAT_API_KEY", "") or "").strip()
    if chat_base and chat_proto == "gemini" and chat_model and chat_key:
        client = build_llm_client(
            api_key=chat_key,
            base_url=chat_base,
            protocol="gemini",
            timeout=60.0,
            max_retries=0,
        )
        return chat_model, client
    return None


def _probe_1(model: str, client: Any) -> dict[str, Any]:
    image_url = _data_url()
    messages = [
        {"role": "system", "content": STRUCTURED_OUTPUT_REQ},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "描述这张图里能看到的内容。"},
                {"type": "image_url", "image_url": {"url": image_url}},
            ],
        },
    ]
    try:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.2,
            max_tokens=1024,
            response_format={"type": "json_object"},
            stream=True,
        )
        text = _stream_text(response)
        parsed = json.loads(text)
    except Exception as exc:
        return {
            "probe": 1,
            "status": "failed",
            "error": _redact(str(exc))[:600],
        }
    speech = str(parsed.get("speech") or "").strip()
    describes_image = bool(speech and any(token in speech for token in ("蓝", "blue", "方块", "色块")))
    markdown_wrapped = text.startswith("```")
    return {
        "probe": 1,
        "status": "passed" if describes_image and not markdown_wrapped else "failed",
        "json_parseable": True,
        "streaming": True,
        "forced_json": True,
        "speech_describes_image": describes_image,
        "speech_preview": speech[:80],
        "markdown_wrapped": markdown_wrapped,
        "emotion": parsed.get("emotion"),
    }


def _probe_2(model: str, client: Any) -> dict[str, Any]:
    image_url = _data_url()
    messages = [
        {"role": "system", "content": STRUCTURED_OUTPUT_REQ},
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": (
                        "先调用 lookup_demo_value 查询“红色”，然后根据结果和这张图给出最终回复；"
                        "最终 speech 必须原样包含 DEMO_RESULT_OK。"
                    ),
                },
                {"type": "image_url", "image_url": {"url": image_url}},
            ],
        },
    ]
    try:
        first = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=TOOL_SCHEMA,
            temperature=0.2,
            max_tokens=1024,
            response_format={"type": "json_object"},
        )
        first_message = first.choices[0].message
        tool_calls = getattr(first_message, "tool_calls", None) or []
        if not tool_calls:
            return {"probe": 2, "status": "failed", "reason": "no_tool_call_emitted", "raw": _redact(str(first_message.content or ""))[:200]}
        call = tool_calls[0]
        call_id = str(getattr(call, "id", "") or "")
        name = str(getattr(call.function, "name", "") or "")
        messages.append({"role": "assistant", "content": None, "tool_calls": [{"id": call_id, "type": "function", "function": {"name": name, "arguments": getattr(call.function, "arguments", "{}")}}]})
        messages.append(
            {
                "role": "tool",
                "tool_call_id": call_id,
                "content": '{"value":"demo:red","marker":"DEMO_RESULT_OK"}',
            }
        )
        second = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=TOOL_SCHEMA,
            temperature=0.2,
            max_tokens=1024,
            response_format={"type": "json_object"},
        )
        text = str(second.choices[0].message.content or "").strip()
        parsed = json.loads(text)
    except Exception as exc:
        return {"probe": 2, "status": "failed", "error": _redact(str(exc))[:600]}
    speech = str(parsed.get("speech") or "")
    valid_call = bool(call_id and name == "lookup_demo_value")
    used_result = "DEMO_RESULT_OK" in speech
    return {
        "probe": 2,
        "status": "passed" if valid_call and used_result else "failed",
        "tool_call_emitted": valid_call,
        "tool_name": name,
        "tool_call_id_valid": bool(call_id),
        "final_json_parseable": True,
        "tools_with_forced_json": True,
        "tool_result_used": used_result,
        "speech_preview": speech[:80],
    }


def _probe_3(model: str, client: Any) -> dict[str, Any]:
    """Cross-model relay: text model emits a tool call; host returns a real
    synthetic image; the next request goes to Gemini and sees both."""
    # Probe 3 needs a separate text chat model to emit the load-image call.
    # If none is configured, report skipped rather than pretending.
    chat_base = str(getattr(config, "CHAT_BASE_URL", "") or "").strip()
    chat_key = str(getattr(config, "CHAT_API_KEY", "") or "").strip()
    chat_model = str(getattr(config, "CHAT_MODEL_NAME", "") or "").strip()
    chat_proto = str(getattr(config, "CHAT_API_PROTOCOL", "auto") or "auto").strip().lower()
    if not (chat_base and chat_key and chat_model):
        return {"probe": 3, "status": "skipped", "reason": "no_chat_model_configured"}
    image_url = _data_url()
    fake_tool_result = {
        "type": "image",
        "handle": "gen_probe_001",
        "data_url": image_url,
        "caption": "这是一张探针生成的蓝色方块图。",
        "marker": "PROBE_TOOL_OK",
    }
    try:
        chat_client = build_llm_client(
            api_key=chat_key,
            base_url=chat_base,
            protocol=chat_proto,
            timeout=60.0,
            max_retries=0,
        )
        first = chat_client.chat.completions.create(
            model=chat_model,
            messages=[
                {"role": "system", "content": "只输出 JSON。当你需要图片时调用 load_demo_image 工具。"},
                {"role": "user", "content": "请加载一张探针图片并描述它。"},
            ],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "load_demo_image",
                        "description": "加载一张演示图片（探针专用，无副作用）。",
                        "parameters": {"type": "object", "properties": {}, "required": []},
                    },
                }
            ],
            temperature=0.2,
            max_tokens=1024,
        )
        first_message = first.choices[0].message
        tool_calls = getattr(first_message, "tool_calls", None) or []
        if not tool_calls:
            return {"probe": 3, "status": "failed", "reason": "chat_model_did_not_emit_tool_call", "raw": _redact(str(first_message.content or ""))[:200]}
        call = tool_calls[0]
        call_id = str(getattr(call, "id", "") or "")
        # Relay: the host materializes the image and hands the next request to Gemini.
        relay_messages = [
            {"role": "system", "content": STRUCTURED_OUTPUT_REQ},
            {"role": "user", "content": "请加载一张图片并描述它。"},
            {"role": "assistant", "content": None, "tool_calls": [{"id": call_id, "type": "function", "function": {"name": "load_demo_image", "arguments": "{}"}}]},
            {
                "role": "tool",
                "tool_call_id": call_id,
                "content": json.dumps(fake_tool_result, ensure_ascii=False),
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "工具已经加载了这张图；请直接依据图片内容给出最终回复，"
                            "并在 speech 中原样包含工具标记 PROBE_TOOL_OK。"
                        ),
                    },
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            },
        ]
        second = client.chat.completions.create(
            model=model,
            messages=relay_messages,
            temperature=0.2,
            max_tokens=1024,
            response_format={"type": "json_object"},
        )
        text = str(second.choices[0].message.content or "").strip()
        parsed = json.loads(text)
    except Exception as exc:
        return {"probe": 3, "status": "failed", "error": _redact(str(exc))[:600]}
    speech = str(parsed.get("speech") or "")
    saw_image = any(token in speech for token in ("蓝", "blue", "方块", "色块"))
    saw_tool_result = "PROBE_TOOL_OK" in speech
    return {
        "probe": 3,
        "status": "passed" if saw_image and saw_tool_result else "failed",
        "chat_model": chat_model,
        "tool_call_relayed": True,
        "gemini_saw_image": saw_image,
        "gemini_saw_tool_result": saw_tool_result,
        "speech_preview": speech[:80],
    }


def main() -> int:
    print("=" * 60)
    print(f"Probe: {PROBE_NAME} (synthetic images only, read-only)")
    print("=" * 60)
    target = _resolve_gemini_target()
    if target is None:
        print(json.dumps(LIVE_NOT_RUN, ensure_ascii=False, indent=2))
        return 0
    model, client = target
    print(f"gemini target model: {model}")
    results = [
        _probe_1(model, client),
        _probe_2(model, client),
        _probe_3(model, client),
    ]
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
