"""Browser availability, conversation runners and shutdown."""

from __future__ import annotations

from typing import Any, Mapping

from ..core import ToolExecutionContext


class BrowserLifecycleMixin:
    def build_prompt_instruction(self) -> str:
        network_scope = (
            "当前宿主已允许访问 localhost 和私网地址，可用于本机 ComfyUI、控制中心或用户明确指定的局域网服务。"
            if self.allow_private_network_urls
            else "当前宿主只允许公开 http(s) 页面；localhost 和私网地址会被拒绝。"
        )
        return (
            "个人已登录 Chrome 使用同一 browser_page，session_source=personal_chrome：先 connect，再 list_tabs/select_tab，"
            "后续携带返回的 browser_session_id/device_epoch。必须先在绑定电脑开启连接并由主人完成 Chrome 许可。"
            "个人模式只使用当前观察的 ref，不接受 CSS/坐标；截图是临时模型输入，不会生成 gen_* 文件。"
            "与 computer_use 切换前调用 handoff，之后重选窗口或标签并重新观察。disconnect 只断开，不关闭用户浏览器。"
            "- browser_page：需要自己读取、总结、核对页面正文或操作页面时才使用 browser_page；"
            "它操作当前会话独立的 Akane 可见托管浏览器窗口，"
            "它不会接管用户手动打开的 Edge/Chrome 标签页。"
            '先用 {"type":"browser_page","action":"navigate","url":"https://..."} 打开页面；'
            "navigate 与 click/fill/press/scroll 会自动重新观察页面，返回最新可访问性树（AX），"
            "并在多模态视觉通道中直接提供当前视口截图像素供你查看（无需额外调用 screenshot）。"
            "click/fill/press 必须携带上一步返回的 observation_id；坐标点击可用 screenshot_id 绑定。"
            "坐标证据失效时先调用 snapshot(observation_mode=hybrid) 获取新图。"
            "用 snapshot 读取 accessibility snapshot 和 ref，或用 elements 读取带 candidate_index 的可见候选；"
            "随后可用 click/fill/press/scroll 完成操作，并根据返回的最新页面证据继续。"
            "显式 screenshot 会生成 gen_* 长期文件句柄；"
            "只有用户明确要收到图片文件时才继续调用 send_file。"
            "工具失败结果会说明状态、是否可恢复和建议的下一动作：按该反馈继续，不要把 browser_closed 当成网络失败，"
            "也不要编造未读取到的页面内容。页面关闭后，navigate 可新建窗口继续；stale_target 后重新 snapshot/elements。"
            "cursor 只续读同一份已捕获快照，不重复截图，也不会再次操作页面。"
            "capabilities 可查询后端差异；托管模式支持 upload(files=材料句柄)、select_option(values)、set_checked(checked)、hover。"
            "frame_selector 指定一个 iframe；先在相同 frame 观察，再操作其中控件。snapshot/read_text 可加 scope_selector 局部读取。"
            "run_actions 可执行至多 6 个同文档 selector 动作，遇导航/弹窗/失败后返回逐步进度；只继续尚未执行部分。"
            "fill 保留原文，空字符串表示清空；dialog 仅指定本次动作首个对话框的响应。个人 Chrome 不支持这些托管扩展。"
            "elements 只是候选，不要声称已经点击或输入。用户给出完整多步目标时持续执行到完成或真实阻塞，"
            "不要每完成一步就询问用户。"
            "click/fill/press 遵循当前能力审批；登录、支付、发布、删除、上传下载和私密表单需要相应授权。"
            '只需替用户打开链接而不读取时使用 open_browser；若还要额外在系统浏览器给用户打开，可加 "open_for_user":true；'
            f"只需检索资料时优先 web_search。{network_scope}"
        )

    def capability_status(self) -> dict[str, Any]:
        if self.personal_browser.available():
            return {"enabled":True, "status":"ready", "reason":"", "personal_chrome":"device_available"}
        status_fn = getattr(self.browser_runner, "capability_status", None)
        if callable(status_fn):
            try:
                status = status_fn()
            except Exception:
                return {"enabled": False, "status": "unavailable", "reason": "browser_runner_status_failed"}
            if isinstance(status, Mapping):
                return {
                    "enabled": bool(status.get("enabled")),
                    "status": str(status.get("status") or "unavailable").strip() or "unavailable",
                    "reason": str(status.get("reason") or "").strip()[:160],
                }
        return {"enabled": True, "status": "ready", "reason": ""}

    def close(self, *, timeout: float = 5.0) -> bool:
        shutdown = getattr(self.browser_runner, "shutdown", None)
        if not callable(shutdown):
            return True
        try:
            try:
                shutdown(timeout=timeout)
            except TypeError:
                shutdown()
            return True
        except Exception:
            return False

    def _runner_for_context(self, context: ToolExecutionContext) -> Any:
        get_runner = getattr(self.browser_runner, "runner_for_session", None)
        if callable(get_runner):
            runner = get_runner(context.profile_user_id, context.session_id)
        else:
            runner = self.browser_runner
        if hasattr(runner, "set_session_identity"):
            runner.set_session_identity(context.profile_user_id, context.session_id)
        if hasattr(runner, "set_attachment_service") and self.attachment_service is not None:
            runner.set_attachment_service(self.attachment_service)
        return runner
