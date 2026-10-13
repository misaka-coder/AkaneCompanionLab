"""Render browser observations, continuation and public result state."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ...browser_page_runtime import BrowserPageResult
from ..core import ToolExecutionContext, ToolExecutionResult, ToolFollowupEnvelope
from .browser_events import build_browser_success_events


class BrowserResultMixin:
    def _finish_browser_result(
        self, *, normalized: BrowserPageResult, context: ToolExecutionContext,
        open_event: dict[str, Any] | None, screenshot_output_path: Path | None, scroll_delta: int,
    ) -> ToolExecutionResult:
        safe_url = self._sanitize_output(normalized.url)[:800]
        safe_title = self._clip(self._sanitize_output(normalized.title), 180)
        safe_text = self._sanitize_output(normalized.text, strip=False)
        lines = self._browser_result_lines(normalized, safe_url=safe_url, safe_title=safe_title)
        images = self._browser_images(
            normalized=normalized, screenshot_output_path=screenshot_output_path,
            context=context, safe_url=safe_url, safe_title=safe_title,
        )
        if isinstance(images, ToolExecutionResult):
            return images
        generated_screenshot, model_image_inputs, image_note = images
        if image_note:
            lines.append(image_note)
        if safe_text:
            lines.append("元素摘要：" if normalized.action == "elements" else "页面状态快照：")
            lines.append(safe_text)
            if normalized.action == "elements":
                lines.append("这些只是可见候选元素摘要，不表示已经点击或输入。需要实际操作时必须等待后续确认能力。")
            elif normalized.action in self.CONTROL_ACTIONS:
                lines.append("高风险浏览器控制动作已在授权边界内执行；请基于当前页面状态继续，不要追加未授权动作。")
            else:
                lines.append("请只基于这份公开页面状态回答；没读到或不确定的内容要明确说明。")
        else:
            lines.append("当前页没有拿到可用正文。不要声称已经读取到未出现在这里的内容。")
        if open_event:
            lines.append("同时已请求桌宠把该公开网页交给系统浏览器打开给用户看。")
        else:
            lines.append("页面已在 Akane 托管浏览器窗口中处理；这不是用户手动打开的系统浏览器标签页。")
        next_hint = self._build_browser_next_hint(normalized.action, safe_text)
        if next_hint:
            lines.append(next_hint)
        continuation, diagnostics = self._browser_result_continuation(normalized, context=context, lines=lines)
        events = build_browser_success_events(
            normalized, context=context, open_event=open_event, generated_screenshot=generated_screenshot,
            safe_url=safe_url, safe_title=safe_title, scroll_delta=scroll_delta,
            element_count=self._count_element_summary_lines(safe_text) if normalized.action == "elements" else 0,
        )
        content = "\n".join(lines)
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=events,
            followup_context=content,
            followup_envelope=ToolFollowupEnvelope(
                content=content,
                producer_bounded=True,
                complete=bool(normalized.complete),
                continuation=continuation,
                diagnostics=diagnostics,
            ),
            state_updates={
                "browser_page_status": normalized.status,
                "browser_page_url": safe_url,
                "browser_page_title": safe_title,
                "browser_observation_id": str(getattr(normalized, "observation_id", "") or ""),
                "browser_open_requested": bool(open_event),
                "browser_page_element_count": self._count_element_summary_lines(safe_text)
                if normalized.action == "elements"
                else 0,
                "browser_page_next_hint": next_hint,
                "browser_control_status": normalized.status if normalized.action in self.CONTROL_ACTIONS else "",
                "browser_screenshot_handle": (
                    str(generated_screenshot.get("generated_handle") or "")
                    if generated_screenshot is not None
                    else ""
                ),
            },
            model_image_inputs=model_image_inputs,
        )

    def _browser_result_lines(
        self, normalized: BrowserPageResult, *, safe_url: str, safe_title: str,
    ) -> list[str]:
        lines = ["【Akane 托管浏览器窗口】", f"动作：{normalized.action}"]
        if normalized.step_results:
            lines.append("批次逐步结果：" + json.dumps(normalized.step_results, ensure_ascii=False))
        if safe_url:
            lines.append(f"URL: {safe_url}")
        if safe_title:
            lines.append(f"标题：{safe_title}")
        if normalized.page_revision:
            lines.append(f"页面版本：{normalized.page_revision}")
        if getattr(normalized, "observation_id", ""):
            lines.append(f"观察编号：{normalized.observation_id}")
        if normalized.screenshot_id:
            width, height = normalized.viewport
            image_width, image_height = normalized.screenshot_dimensions
            lines.append(
                f"截图编号 screenshot_id：{normalized.screenshot_id}；"
                f"视口：{width} × {height} CSS 像素；截图：{image_width} × {image_height} 像素。"
                "coordinate=[x,y] 使用视口左上角为原点的 CSS 像素，须绑定本截图编号。"
            )
        if normalized.visual_status == "visual_unavailable":
            lines.append("当前视觉观察不可用；请先使用文本证据或重新观察，不得猜测截图坐标。")
        if normalized.reason:
            lines.append(f"补充状态：{self._clip(self._sanitize_output(normalized.reason), 180)}")
        if normalized.next_action:
            lines.append(f"建议下一动作：browser_page.{normalized.next_action}，不要重放已执行的操作。")
        if getattr(normalized, "download_id", ""):
            dl_status = getattr(normalized, "download_status", "") or "completed"
            dl_line = f"【文件下载】标识: {normalized.download_id} | 状态: {dl_status}"
            if getattr(normalized, "download_filename", ""):
                dl_line += f" | 文件名: {normalized.download_filename}"
            if getattr(normalized, "attachment_handle", ""):
                dl_line += f" | 工作台材料句柄: {normalized.attachment_handle}（可通过 inspect_attachment 查看或 send_file 发送）"
            lines.append(dl_line)
        if getattr(normalized, "dialog_events", ()):
            for dialog in normalized.dialog_events:
                dialog_type = str(dialog.get("type") or "dialog")[:40]
                dialog_message = str(dialog.get("message") or "")[:200]
                lines.append(f"【浏览器对话框】{dialog.get('state', 'unknown')} / {dialog.get('response', 'unknown')} {dialog_type}: {dialog_message}")
        if normalized.action in self.CONTROL_ACTIONS:
            lines.append(
                f"动作状态：{getattr(normalized, 'action_state', 'executed')}；"
                f"观察状态：{getattr(normalized, 'observation_state', 'complete')}；"
                f"页面变化：{getattr(normalized, 'page_changed', 'unknown')}"
            )
        return lines

    def _browser_result_continuation(
        self, normalized: BrowserPageResult, *, context: ToolExecutionContext, lines: list[str],
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        continuation = None
        diagnostics: dict[str, Any] = {
            "action": normalized.action,
            "complete": bool(normalized.complete),
            "shown_chars": int(normalized.shown_chars or 0),
            "total_chars": int(normalized.total_chars or 0),
            "snapshot_id": str(normalized.snapshot_id or ""),
            "observation_id": str(getattr(normalized, "observation_id", "") or ""),
        }
        if not normalized.complete and normalized.snapshot_id and normalized.next_cursor:
            cursor = self._issue_browser_cursor(
                snapshot_id=normalized.snapshot_id,
                offset=normalized.next_cursor,
                context=context,
            )
            if cursor:
                continuation = {"type": self.tool_type, "cursor": cursor}
                lines.append(
                    f"本页之后还有未展示的快照内容（已展示 {normalized.shown_chars}/{normalized.total_chars} 字）。"
                    "如果当前内容已经足够回答，可以直接回答；只有确实需要同一份快照的后续内容时，才调用："
                    f'browser_page(cursor="{cursor}")'
                )
        return continuation, diagnostics
