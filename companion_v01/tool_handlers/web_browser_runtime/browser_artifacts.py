"""Screenshot allocation, registration, cleanup and model image inputs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...browser_page_runtime import BrowserPageResult
from ..core import ToolExecutionContext, ToolExecutionResult


class BrowserArtifactsMixin:
    def _allocate_browser_screenshot(self, context: ToolExecutionContext) -> Path | ToolExecutionResult:
        if self.generated_file_service is None:
            return self._failure(
                BrowserPageResult(
                    ok=False,
                    status="unavailable",
                    action="screenshot",
                    reason="generated_file_service_unavailable",
                )
            )
        try:
            screenshot_output_path = self.generated_file_service.allocate_output_path(
                profile_user_id=context.profile_user_id,
                session_id=context.session_id,
                title="browser-screenshot",
                output_format="png",
                timestamp=context.now_ts,
            )
            screenshot_output_path.parent.mkdir(parents=True, exist_ok=True)
            return screenshot_output_path
        except Exception:
            return self._failure(
                BrowserPageResult(
                    ok=False,
                    status="unavailable",
                    action="screenshot",
                    reason="screenshot_output_allocation_failed",
                )
            )

    def _browser_images(
        self, *, normalized: BrowserPageResult, screenshot_output_path: Path | None,
        context: ToolExecutionContext, safe_url: str, safe_title: str,
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]], str] | ToolExecutionResult:
        note = ""
        generated_screenshot: dict[str, Any] | None = None
        model_image_inputs: list[dict[str, Any]] = []
        if normalized.action == "screenshot" and screenshot_output_path is not None:
            try:
                generated_screenshot = self.generated_file_service.register_generated_artifact(
                    profile_user_id=context.profile_user_id,
                    session_id=context.session_id,
                    output_path=screenshot_output_path,
                    output_title="browser-screenshot",
                    output_format="png",
                    mime_type="image/png",
                    content_card={"kind": "browser_screenshot", "url": safe_url, "title": safe_title},
                    summary=f"托管浏览器截图：{safe_title or safe_url or '当前页面'}",
                    created_by_tool="browser_page",
                    send_to_user=False,
                    timestamp=context.now_ts,
                )
            except Exception:
                self._discard_browser_screenshot(screenshot_output_path)
                return self._failure(
                    BrowserPageResult(
                        ok=False,
                        status="unavailable",
                        action=normalized.action,
                        reason="screenshot_registration_failed",
                    )
                )
            handle = str(generated_screenshot.get("generated_handle") or "").strip()
            if self.image_material_resolver is not None and handle:
                try:
                    image_result = self.image_material_resolver.build_model_image_inputs(
                        profile_user_id=context.profile_user_id,
                        session_id=context.session_id,
                        targets=[handle],
                        max_count=1,
                    )
                    model_image_inputs = list(image_result.get("images") or [])
                except Exception:
                    model_image_inputs = []
            if model_image_inputs:
                note = (
                    f"已截取当前可见区域并登记为 {handle}；截图像素已随本次工具结果直接交给你查看。"
                    "请先依据图像与页面状态继续；只有用户要收到文件时才调用 send_file。"
                )
            else:
                note = (
                    f"已截取当前可见区域并登记为 {handle}，但本次未能直接加载图像像素。"
                    f"需要看图时调用 load_material 加载 {handle}；用户要收到文件时调用 send_file。"
                )
        elif not model_image_inputs and getattr(normalized, "model_image", None):
            model_image_inputs = [normalized.model_image]
            note = ("【多模态视口截图】当前页面截图像素已随本次工具结果直接交给你查看，请结合界面视觉与可访问性证据继续。")
        return generated_screenshot, model_image_inputs, note

    @staticmethod
    def _discard_browser_screenshot(path: Path | None) -> None:
        if path is not None:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
