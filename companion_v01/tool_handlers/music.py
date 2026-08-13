"""QQ native music-card delivery handler (thin product adapter).

The handler never talks to NapCat directly. It validates the model-visible
``platform``/``track_id`` pair against the supported provider ID format and emits a
``music_share_ready`` delivery event that the QQ Gateway turns into a OneBot
music segment. The OneBot segment itself is constructed by the
``channelcore-onebot`` package (M63 thin-adapter boundary).
"""

from __future__ import annotations

import re
from typing import Any

from .core import BaseToolHandler, ToolExecutionContext, ToolExecutionResult


MUSIC_PLATFORM_LABELS = {
    "netease_music": "网易云",
    "qq_music": "QQ音乐",
}

# NetEase track ids are pure decimal song ids.
MUSIC_TRACK_ID_PATTERNS = {
    "netease_music": re.compile(r"^[0-9]{1,20}$"),
    "qq_music": re.compile(r"^(?=.*[A-Za-z])[0-9A-Za-z_-]{1,64}$"),
}


class SendMusicCardToolHandler(BaseToolHandler):
    tool_type = "send_music_card"

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
            "- send_music_card：当用户要在当前 QQ 会话收到网易云或 QQ音乐时使用。"
            "platform 为 netease_music（track_id 是纯数字歌曲 ID）或 qq_music（track_id 是字母数字 songmid）；"
            "track_id 必须来自本工具结果、用户输入或已展开工具轨迹中真实出现的精确 ID，严禁根据歌名猜测或编造。"
            "工具结果只表示进入交付队列，不代表传输已经成功；"
            "网易云会先尝试原生卡片，卡片被 QQ 拒绝后再改发公开音频语音；"
            "QQ音乐原生卡片已知不可用，仅在匿名公开接口真实返回可播地址时直接发语音。"
        )

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        platform = str(call.get("platform") or "").strip()
        track_id = str(call.get("track_id") or "").strip()
        label = MUSIC_PLATFORM_LABELS.get(platform)
        pattern = MUSIC_TRACK_ID_PATTERNS.get(platform)
        if label is None:
            return ToolExecutionResult(
                tool_type=self.tool_type,
                followup_context="音乐交付参数无效：platform 只支持 netease_music 或 qq_music。",
            )
        if not pattern or not pattern.fullmatch(track_id):
            hint = (
                "网易云 track_id 必须是纯数字歌曲 ID，请使用搜索结果里的精确 ID。"
                if platform == "netease_music"
                else "QQ音乐 track_id 必须是歌曲 songmid（字母数字组合），不是数字 songid。"
            )
            return ToolExecutionResult(
                tool_type=self.tool_type,
                followup_context=hint,
            )
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=[
                {
                    "type": "music_share_ready",
                    "music": {"platform": platform, "track_id": track_id},
                    "send_to_user": True,
                    "client_mode": "qq_text",
                }
            ],
            followup_context=f"{label}音乐卡片已进入本轮 QQ 交付队列。这不是最终传输成功回执。",
        )
