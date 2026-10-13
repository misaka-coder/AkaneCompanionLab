"""Truthful browser failure states and next-action guidance."""

from __future__ import annotations

import json
import re
from typing import Any

from ...browser_page_runtime import BrowserPageResult
from ..core import ToolExecutionResult


class BrowserFeedbackMixin:
    def _failure(self, result: BrowserPageResult, *, open_event: dict[str, Any] | None = None) -> ToolExecutionResult:
        status = str(result.status or "unavailable").strip()[:120] or "unavailable"
        reason = self._clip(self._sanitize_output(result.reason), 180)
        events = []
        if open_event:
            events.append(open_event)
        feedback, retryable, next_action = self._browser_failure_feedback(result)
        if result.step_results:
            feedback = "批次逐步结果：" + json.dumps(result.step_results, ensure_ascii=False) + "\n" + feedback
            feedback += "\n不要重放已执行的步骤；重新观察后只继续未执行的部分。"
        events.append(
            {
                "type": "browser_page_read",
                "provider": "managed_browser",
                "action": str(result.action or "current").strip() or "current",
                "status": status,
                "reason": reason or status,
                "retryable": retryable,
                "next_action": next_action,
                "action_state": result.action_state,
                "observation_state": result.observation_state,
            }
        )
        open_note = "已另外请求桌宠打开该公开网页给用户看；" if open_event else ""
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=events,
            followup_context=(
                f"{open_note}{feedback}"
            ),
            state_updates={
                "browser_page_status": status,
                "browser_page_reason": reason or status,
                "browser_page_retryable": retryable,
                "browser_page_next_action": next_action,
                "browser_open_requested": bool(open_event),
            },
        )

    def _browser_failure_feedback(self, result: BrowserPageResult) -> tuple[str, bool, str]:
        status = str(result.status or "unavailable").strip() or "unavailable"
        reason = self._clip(self._sanitize_output(result.reason), 180)
        retryable = bool(result.retryable)
        next_action = str(result.next_action or "").strip()
        if result.action == "download_status" and reason == "download_not_found":
            return (
                "未找到所查询的下载任务；这不表示浏览器不可用。页面预览不等于文件已下载。"
                "可检查页面的下载控件，或使用已授权的文件获取工具。",
                False,
                "",
            )
        if result.action_state in {"executed", "unknown"} and result.action in self.CONTROL_ACTIONS | {"navigate", "scroll"}:
            next_action = "current"
            retryable = True
            state_note = "动作已执行，但后续处理失败" if result.action_state == "executed" else "动作是否生效尚不能确认"
            message = f"{state_note}。只重新观察当前页面核实结果，不要自动重复点击、输入或提交。"
        elif status in {"no_page", "browser_closed"}:
            retryable = True
            next_action = next_action or "navigate"
            message = (
                "当前会话没有可读取的浏览器页面，可能是窗口尚未打开或已被关闭；这不是网页内容或网络结论。"
                "如果任务里已有目标 URL，下一步调用 browser_page.navigate，宿主会创建新窗口继续。"
            )
        elif status in {"stale_target", "target_not_found"} or reason == "candidate_not_found":
            retryable = True
            next_action = next_action or "snapshot"
            message = "目标元素已变化或不存在。下一步重新调用 snapshot 或 elements 获取当前 ref/候选，再继续操作；不要复用旧 ref。"
        elif status in {"navigation_timeout", "site_unreachable"}:
            retryable = True
            next_action = next_action or "navigate"
            message = "页面导航没有完成。可以用同一 URL 再调用一次 navigate；若仍失败，再向用户说明站点或网络暂时不可达。"
        elif status == "invalid_request":
            retryable = False
            message = "浏览器调用参数无效。根据 reason 修正参数后重新调用；不要把这次失败描述成已执行。"
        elif status == "missing_executor" or reason == "playwright_not_installed":
            retryable = False
            message = (
                "浏览器能力暂时不可用：当前宿主没有浏览器执行器，无法在本轮自行恢复；"
                "请明确告诉用户该能力未安装，不要编造页面结果。"
            )
        else:
            message = "浏览器页面暂时不可用，没有产生可用页面证据。不要编造页面结果；可按 next_action 再试一次，仍失败再向用户说明。"
        detail = f"状态：{status}；原因：{reason or '未提供'}；可恢复：{'是' if retryable else '否'}"
        if next_action:
            detail += f"；建议下一动作：browser_page.{next_action}"
        return f"{detail}。{message}", retryable, next_action

    def _build_browser_next_hint(self, action: str, text: str) -> str:
        clean_action = str(action or "").strip()
        clean_text = str(text or "")
        has_visible_candidates = "Visible link/video candidates:" in clean_text
        has_refs = "[ref=" in clean_text
        if clean_action in {"navigate", "snapshot", "current", "read_text", "scroll"}:
            if has_visible_candidates:
                return (
                    "下一步提示：如果用户目标需要进入某个可见链接/视频，可继续调用 "
                    "browser_page click 并使用 candidate_index；如果只是要总结当前可见内容，就停止工具并回答。"
                )
            if has_refs:
                return (
                    "下一步提示：如果用户目标需要操作当前可见控件，可继续调用 browser_page click/fill/press 并使用 ref；"
                    "如果只是阅读当前页，就基于已有内容回答。"
                )
            return (
                "下一步提示：如果用户明确还要继续查看后续内容，可继续调用 browser_page scroll；"
                "如果当前内容已经足够，就停止工具并回答。"
            )
        if clean_action == "elements":
            return (
                "下一步提示：如果元素摘要里有目标，可继续用 ref 或 candidate_index 操作；"
                "如果没有目标，先 snapshot 或 scroll 获取更多上下文。"
            )
        if clean_action == "screenshot":
            return (
                "下一步提示：截图像素会直接随工具结果供你观察；结合图像完成判断。"
                "只有用户要求接收截图文件时，才调用 send_file 发送返回的句柄。"
            )
        if clean_action in self.CONTROL_ACTIONS:
            return (
                "下一步提示：控制动作后应先 snapshot 或 read_text 观察页面变化；"
                "不要假设点击、输入或按键已经产生了未返回的新内容。"
            )
        return ""

    def _build_open_event(self, url: str, title: str = "", *, client_mode: str = "") -> dict[str, Any] | None:
        safe_url = self._normalize_public_url(url)
        if not safe_url:
            return None
        return {
            "type": "browser_open_requested",
            "url": safe_url,
            "label": self._clip(self._sanitize_output(title), 80),
            "reason": "browser_page_open_for_user",
            "client_mode": str(client_mode or ""),
            "requires_confirmation": False,
        }

    def _count_element_summary_lines(self, text: str) -> int:
        return sum(1 for line in str(text or "").splitlines() if re.match(r"^\d+\.\s+", line.strip()))

    def _sanitize_output(self, value: str, *, strip: bool = True) -> str:
        text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
        text = re.sub(r"(?i)authorization:\s*bearer\s+[^\s]+", "Authorization: Bearer [redacted]", text)
        text = re.sub(r"(?i)\b(api[_-]?key|password|secret|token)\s*[:=]\s*[^\s,;]+", r"\1=[redacted]", text)
        text = re.sub(r"(?i)([?&](?:api[_-]?key|password|secret|token)=)[^&#\s]+", r"\1[redacted]", text)
        return text.strip() if strip else text
