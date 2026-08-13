"""Model-directed QQ music-card and generic audio delivery tools.

Both handlers execute through a narrow request-scoped delivery port and return
the real transport outcome to the same tool loop.  They never silently switch
delivery surfaces: a failed card remains a failed card, so the model can decide
whether a verified URL or existing audio handle should be sent as QQ voice.
"""

from __future__ import annotations

import mimetypes
import re
from typing import Any
from urllib.parse import urlparse

from .core import BaseToolHandler, ToolExecutionContext, ToolExecutionResult, operation_tool_result


MUSIC_PLATFORM_LABELS = {
    "netease_music": "网易云",
}

# NetEase track ids are pure decimal song ids.
MUSIC_TRACK_ID_PATTERNS = {
    "netease_music": re.compile(r"^[0-9]{1,20}$"),
}


class SendMusicCardToolHandler(BaseToolHandler):
    tool_type = "send_music_card"

    def __init__(self, *, delivery_port: Any | None = None) -> None:
        self.delivery_port = delivery_port

    def bind_delivery_port(self, delivery_port: Any | None) -> None:
        self.delivery_port = delivery_port

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        if str(value.get("type") or "").strip() != self.tool_type:
            return None
        platform = str(value.get("platform") or "").strip()
        if platform not in MUSIC_PLATFORM_LABELS:
            return None
        track_id = str(value.get("track_id") or "").strip()
        pattern = MUSIC_TRACK_ID_PATTERNS.get(platform)
        if not pattern or not pattern.fullmatch(track_id):
            return None
        return {"type": self.tool_type, "platform": platform, "track_id": track_id}

    def build_prompt_instruction(self) -> str:
        return (
            "- send_music_card：当用户明确希望在当前 QQ 会话收到原生音乐卡片时使用。"
            "platform 当前只支持 netease_music，track_id 是网易云纯数字歌曲 ID；"
            "track_id 必须来自本工具结果、用户输入或已展开工具轨迹中真实出现的精确 ID，严禁根据歌名猜测或编造。"
            "工具结果就是本次 QQ 卡片传输的真实结果。失败时不会暗中改发语音；"
            "如仍需可播放内容，应根据失败反馈取得真实公开音频 URL，再调用 send_audio。"
        )

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        platform = str(call.get("platform") or "").strip()
        track_id = str(call.get("track_id") or "").strip()
        label = MUSIC_PLATFORM_LABELS.get(platform)
        pattern = MUSIC_TRACK_ID_PATTERNS.get(platform)
        if label is None:
            return ToolExecutionResult(
                tool_type=self.tool_type,
                followup_context="音乐交付参数无效：原生卡片当前只支持 netease_music。",
            )
        if not pattern or not pattern.fullmatch(track_id):
            return ToolExecutionResult(
                tool_type=self.tool_type,
                followup_context="网易云 track_id 必须是纯数字歌曲 ID，请使用搜索结果里的精确 ID。",
            )
        if self.delivery_port is None:
            return _delivery_failure(
                tool_type=self.tool_type,
                reason="qq_delivery_port_unavailable",
                feedback="当前 QQ 卡片交付通道不可用，没有发送任何卡片。请直接说明暂时无法推送。",
                surface="music_card",
            )
        try:
            delivery = dict(
                self.delivery_port.send_music_card(
                    request_context=context.request_context,
                    platform=platform,
                    track_id=track_id,
                )
            )
        except Exception:
            delivery = {"ok": False, "status": "failed", "reason": "qq_music_card_transport_exception"}
        if bool(delivery.get("ok")):
            return operation_tool_result(
                tool_type=self.tool_type,
                operation_result={
                    "ok": True,
                    "followup_context": (
                        f"{label}原生音乐卡片已由 QQ 真实发送成功。"
                        "可以自然继续回复；不要再发送同一张卡片。"
                    ),
                },
                success_events=[_delivery_event("music_card", "sent", source=platform)],
            )
        reason = _delivery_reason(delivery)
        return _delivery_failure(
            tool_type=self.tool_type,
            reason=reason,
            feedback=(
                f"{label}原生音乐卡片没有发送成功（{reason}）。本次没有自动改发语音。"
                "如果用户仍想听歌，可取得一个真实、公开可访问的音频 URL 后调用 send_audio；"
                "若没有可用音源，就如实说明并可尝试其他版本。"
            ),
            surface="music_card",
        )


class SendAudioToolHandler(BaseToolHandler):
    tool_type = "send_audio"

    def __init__(self, *, generated_file_service: Any, delivery_port: Any | None = None) -> None:
        self.generated_file_service = generated_file_service
        self.delivery_port = delivery_port

    def bind_delivery_port(self, delivery_port: Any | None) -> None:
        self.delivery_port = delivery_port

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict) or str(value.get("type") or "").strip() != self.tool_type:
            return None
        source = str(value.get("source") or value.get("target") or value.get("url") or "").strip()
        if not source:
            return None
        return {
            "type": self.tool_type,
            "source": source[:2048],
            "name": str(value.get("name") or value.get("title") or "").strip()[:160],
        }

    def build_prompt_instruction(self) -> str:
        return (
            "- send_audio：把真实音频交付到当前 QQ 会话。source 使用公开 HTTP(S) 音频直链，"
            "或当前上下文中精确的 audio_/file_/gen_ 音频句柄；不要传平台名、歌曲 ID、网页地址或猜测路径。"
            "本工具只发 QQ 语音；需要普通文件时调用 send_file，需要两种表现时分别调用两个工具。"
            "工具会返回真实发送结果，失败时按反馈换音源、换句柄或向用户解释，不要声称已经发送。"
        )

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        if str(context.client_mode or "").strip().lower() != "qq_text":
            return _delivery_failure(
                tool_type=self.tool_type,
                reason="qq_audio_delivery_unavailable_for_client",
                feedback="send_audio 只在 QQ 会话可用；当前没有发送任何音频。",
                surface="voice",
            )
        if self.delivery_port is None:
            return _delivery_failure(
                tool_type=self.tool_type,
                reason="qq_delivery_port_unavailable",
                feedback="当前 QQ 音频交付通道不可用，没有发送任何音频。",
                surface="voice",
            )
        source = str(call.get("source") or "").strip()
        name = str(call.get("name") or "").strip()
        parsed = urlparse(source)
        is_url = parsed.scheme.lower() in {"http", "https"}
        if is_url:
            try:
                delivery = dict(
                    self.delivery_port.send_audio_url(
                        request_context=context.request_context,
                        audio_url=source,
                        name=name,
                    )
                )
            except Exception:
                delivery = {"ok": False, "status": "failed", "reason": "qq_audio_transport_exception"}
            return self._result_from_delivery(delivery, source_label="公开音频直链", surface="voice")

        resolution = dict(
            self.generated_file_service.send_file(
                profile_user_id=context.profile_user_id,
                session_id=context.session_id,
                target=source,
                targets=[source],
                timestamp=context.now_ts,
            )
        )
        files = [item for item in list(resolution.get("files") or []) if isinstance(item, dict)]
        if not bool(resolution.get("ok")) or len(files) != 1:
            reason = str(resolution.get("error") or "audio_handle_not_found")
            return _delivery_failure(
                tool_type=self.tool_type,
                reason=reason,
                feedback=(str(resolution.get("followup_context") or "").strip() or f"没有找到音频句柄 {source}。")
                + " 本次没有发送任何音频。",
                surface="voice",
            )
        file_ref = files[0]
        if not self._is_audio_file_ref(file_ref):
            return _delivery_failure(
                tool_type=self.tool_type,
                reason="voice_delivery_requires_audio",
                feedback=f"{source} 不是可确认的音频文件，本次没有发送。请先转码为音频或选择正确句柄。",
                surface="voice",
            )
        absolute_path = str(file_ref.get("absolute_path") or "").strip()
        if not absolute_path:
            return _delivery_failure(
                tool_type=self.tool_type,
                reason="audio_file_path_unavailable",
                feedback=f"{source} 的音频文件本体当前不可用，本次没有发送。",
                surface="voice",
            )
        try:
            delivery = dict(
                self.delivery_port.send_audio_file(
                    request_context=context.request_context,
                    absolute_path=absolute_path,
                    name=name or str(file_ref.get("name") or file_ref.get("title") or source),
                )
            )
        except Exception:
            delivery = {"ok": False, "status": "failed", "reason": "qq_audio_transport_exception"}
        return self._result_from_delivery(delivery, source_label=source, surface="voice")

    def _result_from_delivery(
        self,
        delivery: dict[str, Any],
        *,
        source_label: str,
        surface: str,
    ) -> ToolExecutionResult:
        status = str(delivery.get("status") or ("sent" if delivery.get("ok") else "failed")).strip().lower()
        if bool(delivery.get("ok")):
            return operation_tool_result(
                tool_type=self.tool_type,
                operation_result={
                    "ok": True,
                    "followup_context": f"{source_label} 已通过 QQ {self._surface_label(surface)}真实发送成功。",
                },
                success_events=[_delivery_event(surface, status or "sent", source=source_label)],
            )
        reason = _delivery_reason(delivery)
        return _delivery_failure(
            tool_type=self.tool_type,
            reason=reason,
            feedback=(
                f"{source_label} 的 QQ {self._surface_label(surface)}"
                f"没有发送成功（{reason}）。"
                "请依据用户目标决定重试另一条真实音源、改用其他交付方式，或如实说明；不要声称已经完整发送。"
            ),
            surface=surface,
            status="failed",
        )

    @staticmethod
    def _surface_label(value: str) -> str:
        return {"voice": "语音"}.get(value, "音频")

    @staticmethod
    def _is_audio_file_ref(file_ref: dict[str, Any]) -> bool:
        mime_type = str(file_ref.get("mime_type") or "").split(";", 1)[0].strip().lower()
        if mime_type.startswith("audio/"):
            return True
        name = str(file_ref.get("name") or file_ref.get("absolute_path") or "").strip()
        return str(mimetypes.guess_type(name)[0] or "").lower().startswith("audio/")


def _delivery_reason(value: dict[str, Any]) -> str:
    reason = str(value.get("reason") or value.get("code") or value.get("message") or "transport_failed").strip()
    return re.sub(r"[\r\n\t]+", " ", reason)[:160] or "transport_failed"


def _delivery_event(surface: str, status: str, *, source: str = "") -> dict[str, Any]:
    return {
        "type": "qq_delivery_result",
        "delivery_surface": str(surface or ""),
        "status": str(status or ""),
        "source": str(source or "")[:160],
    }


def _delivery_failure(
    *,
    tool_type: str,
    reason: str,
    feedback: str,
    surface: str,
    status: str = "failed",
) -> ToolExecutionResult:
    return operation_tool_result(
        tool_type=tool_type,
        operation_result={"ok": False, "error": reason, "followup_context": feedback},
        state_updates={
            "qq_delivery": {
                "surface": str(surface or ""),
                "status": str(status or "failed"),
                "reason": str(reason or "transport_failed")[:160],
            }
        },
    )
