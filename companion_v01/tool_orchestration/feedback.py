"""Model feedback, tool round budgets, and producer-owned continuation."""

from __future__ import annotations

import json
from typing import Any, Iterable, Mapping

import config

from ..tool_handlers.core import ToolFollowupEnvelope


def _bounded_int(raw_value: Any, *, default: int, lower: int = 1, upper: int = 16) -> int:
    try:
        value = int(raw_value)
    except Exception:
        value = default
    return max(lower, min(upper, value))


def max_tool_rounds() -> int:
    try:
        return max(0, int(getattr(config, "TOOL_ROUND_HARD_LIMIT", 0) or 0))
    except Exception:
        return 0


def tool_round_warning_remaining(*, hard_limit: int | None = None) -> int:
    hard = max_tool_rounds() if hard_limit is None else max(0, int(hard_limit or 0))
    if hard <= 0:
        return 0
    try:
        configured = max(0, int(getattr(config, "TOOL_ROUND_WARNING_REMAINING", 8) or 0))
    except Exception:
        configured = 8
    return min(max(0, hard - 1), configured)


def tool_metadata_dict(handler: Any, *, tool_type: str = "") -> dict[str, Any]:
    raw_metadata: Any = None
    if handler is not None and hasattr(handler, "tool_metadata"):
        try:
            raw_metadata = handler.tool_metadata()
        except Exception:
            raw_metadata = None
    if isinstance(raw_metadata, Mapping):
        metadata = dict(raw_metadata)
    elif raw_metadata is not None:
        metadata = {
            "family": getattr(raw_metadata, "family", ""),
            "operation": getattr(raw_metadata, "operation", ""),
            "risk": getattr(raw_metadata, "risk", ""),
            "default_round_budget": getattr(raw_metadata, "default_round_budget", 3),
            "background": getattr(raw_metadata, "background", False),
        }
    else:
        metadata = {}
    metadata["tool_type"] = str(tool_type or getattr(handler, "tool_type", "") or "").strip()
    metadata["family"] = str(metadata.get("family") or "general").strip() or "general"
    metadata["operation"] = str(metadata.get("operation") or "mixed").strip() or "mixed"
    metadata["risk"] = str(metadata.get("risk") or "medium").strip() or "medium"
    metadata["default_round_budget"] = _bounded_int(
        metadata.get("default_round_budget", 3),
        default=max_tool_rounds(),
    )
    metadata["background"] = bool(metadata.get("background"))
    return metadata


def tool_call_signature(tool_call: dict[str, Any]) -> str:
    payload = {str(key): value for key, value in dict(tool_call or {}).items() if not str(key).startswith("_tool_")}
    if str(payload.get("type") or "").strip() == "web_search":
        action = str(payload.get("action") or "search").strip() or "search"
        if action in {"search", "batch_search"}:
            payload.pop("max_results", None)
    try:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        return repr(sorted((str(key), str(value)) for key, value in payload.items()))


def describe_tool_call_for_prompt(tool_call: dict[str, Any]) -> str:
    tool_type = str(tool_call.get("type") or "unknown").strip() or "unknown"
    details = {
        str(key): value
        for key, value in dict(tool_call or {}).items()
        if key != "type" and not str(key).startswith("_tool_") and value not in (None, "", [], {})
    }
    if not details:
        return tool_type
    try:
        return f"{tool_type} {json.dumps(details, ensure_ascii=False, sort_keys=True, default=str)[:500]}"
    except Exception:
        return f"{tool_type} {details!r}"[:500]


def append_structured_artifact_receipts(
    followup_context: Any,
    *,
    stream_events: Iterable[Mapping[str, Any]] | None,
) -> str:
    """Ensure newly created artifacts are visible in the tool result once.

    Tool implementations may describe their result naturally, but managed
    artifact bridges are also allowed to report only a structured
    ``generated_file_ready`` event.  The model still needs the stable handle
    in the very next tool round so it can send or reuse the file directly.
    This projection is based only on the result event contract; it never
    guesses from user wording and never injects a persistent workspace list.
    """

    text = str(followup_context or "").strip()
    receipts: list[str] = []
    seen_handles: set[str] = set()
    for raw_event in stream_events or ():
        if not isinstance(raw_event, Mapping):
            continue
        if str(raw_event.get("type") or "").strip() != "generated_file_ready":
            continue
        generated = raw_event.get("generated_file")
        if not isinstance(generated, Mapping):
            continue
        handle = " ".join(str(generated.get("generated_handle") or "").split())[:120]
        if not handle or handle in seen_handles or handle in text:
            continue
        seen_handles.add(handle)
        title = " ".join(str(generated.get("output_title") or "").split())[:160]
        output_format = " ".join(str(generated.get("output_format") or generated.get("file_ext") or "").split())[:40]
        attributes = [f"handle={handle}"]
        if title:
            attributes.append(f"title={title}")
        if output_format:
            attributes.append(f"format={output_format}")
        receipts.append("- generated_file " + " ".join(attributes))
    if not receipts:
        return text
    receipt_block = "\n".join(
        [
            "【本轮新生成文件】",
            *receipts,
            "这些 handle 已可直接交给后续工具；如果用户已明确要收到文件，可直接调用 send_file，无需先查询生成文件工作台。",
        ]
    )
    return "\n\n".join(part for part in (text, receipt_block) if part)


def shape_tool_followup(
    followup_context: Any,
    *,
    tool_type: str,
) -> str:
    """Discipline the tool result text fed back to the model (Claude Code-aligned).

    Rules applied at the single point where a tool result becomes model-facing
    feedback:
    - empty-but-successful -> stable placeholder, never an empty tool result
      (mirrors Claude Code's empty tool_result guard; an empty result tail can
      make some models end the turn with no output).
    - producer-bounded envelope -> preserve the producer's complete logical
      units and continuation cursor instead of applying another character cut.
    - all other internal results -> preserve them unchanged. Long-result producers
      own paging; this shared boundary must not silently remove evidence.

    No paths are introduced here. Producer-owned continuation and independent
    storage-boundary secret/path sanitization remain separate contracts.
    """
    tool_name = str(tool_type or "tool").strip() or "tool"
    envelope = followup_context if isinstance(followup_context, ToolFollowupEnvelope) else None
    text = str(envelope.content if envelope is not None else followup_context or "").strip()
    if not text:
        return f"（{tool_name} 执行成功，但没有返回可展示的内容。）"
    if envelope is not None and envelope.producer_bounded:
        if not envelope.complete and not dict(envelope.continuation or {}):
            text = (
                f"{text}\n"
                "（该工具声明结果尚未完整，但没有提供可执行 continuation；"
                "这次读取链路不完整，请勿假装已经读完。）"
            )
        else:
            return text
    return text


def build_multi_tool_followup_context(
    tool_followups: list[str],
    *,
    allow_more: bool,
    stop_reason: str = "",
) -> str:
    lines: list[str] = ["【本轮工具执行记录】"]
    if tool_followups:
        lines.extend([str(item).strip() for item in tool_followups if str(item).strip()])
    else:
        lines.append("(暂时没有可用的工具结果。)")
    if allow_more:
        lines.append(
            "如果任务还没完成，可以继续使用本轮实际提供的工具入口：请求中直接附带的工具直接调用，"
            "只有“兼容 JSON 工具”清单里的工具才写入 JSON tool_call；"
            "如果用户已经明确交代了下一步，且下一步仍在安全边界和授权范围内，不要为了确认而停下询问；"
            "每条结果只证明其中明确给出的状态、数据和产物。结果已经足够、下一步不明确、或遇到真实阻塞时，"
            "停止调用，并按当前客户端协议自然回复主人；证据缺失的部分要明确说明，不要补猜。"
            "凡是会对外发送或改变状态的动作，查询/准备/生成参数都不等于完成；必须实际调用对应动作工具，"
            "并看到该工具的成功回执后才能向用户宣称已完成。"
        )
    else:
        if str(stop_reason or "").strip() == "tool_budget_exhausted":
            lines.append(
                "本轮工具预算已经用完；最后一批工具已经真实执行，其结果就在上方。"
                "现在请按原有交付格式向用户说明本轮实际完成的内容、验证结果、尚未完成或无法确认的部分。"
                "如果任务没有完成，可以请用户发送“继续”；不要宣称未经验证的事项已经完成。"
            )
        elif str(stop_reason or "").strip() == "tool_unavailable":
            lines.append(
                "刚才的工具返回不可用或失败状态；工具阶段到此结束。请使用此前已经取得的可靠证据回答，"
                "并明确这次未能取得的部分；没有证据的内容不要编造。"
            )
        elif str(stop_reason or "").strip() == "finance_no_progress":
            lines.append(
                "连续金融查询没有带来新证据，系统已触发无进展保险丝。请停止查询，基于现有来源、as_of 与程序计算"
                "给出当前最可靠的完整答复，并明确证据缺口和置信度。"
            )
        lines.append(
            "本轮不要再调用工具（无论原生或兼容），请立即按当前客户端协议输出完整、可交付的最终回复。"
            "不得只回复“仍在处理”“还没完成”“需要继续查询”或类似占位语；即使证据不足，也要给出当前可支持的结论、"
            "限制与下一步建议。"
        )
    return "\n\n".join(lines)


def build_tool_round_warning(
    *,
    used_rounds: int,
    hard_limit: int,
    memcore_enabled: bool,
) -> str:
    """Explain the one-time continuation warning without changing permissions."""

    used = max(0, int(used_rounds or 0))
    hard = max(1, int(hard_limit or 1))
    remaining = max(0, hard - used)
    if memcore_enabled:
        history_rule = (
            "当前开放回合中的工具调用与结果现在都以完整形式可见。本回合结束并进入下一次用户请求后，"
            "MemCore 会继续保留普通工具调用参数；较短工具结果保留原文，只有满足压缩收益条件的较长结果"
            "才会替换为可通过 open_memory 回读的卡片。"
        )
    else:
        history_rule = "当前开放回合中的工具调用与结果现在都以完整形式可见；回合结束后的历史可能因上下文维护而精简。"
    return (
        f"【工具预算提醒：已使用 {used}/{hard}，还剩 {remaining} 轮】\n"
        "工具仍然可用。"
        f"{history_rule}\n"
        "单次卡片可以恢复对应结果，但不会自动把分散在多轮调用中的任务目标、关键决策、修改位置、"
        "验证状态与剩余工作整理成可靠的续作说明。如果预计不能在剩余预算内完成，请趁工具仍可使用，"
        "在真实项目中写入或更新一份可检查的续作记录，说明当前目标、已完成改动、关键文件与位置、"
        "实际测试结果、剩余事项、已知失败和下一步。不要复制大段已有输出，不要记录未经验证的结论。"
        "若能在本轮完成，则继续正常执行，无需额外创建文件。"
    )
