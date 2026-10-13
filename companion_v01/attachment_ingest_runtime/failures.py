"""Stable attachment failure codes and user-readable bounded explanations."""

from __future__ import annotations

from typing import Any
from ..public_url_policy import PublicUrlPolicyError
from .definitions import (
    AttachmentMaterializationError,
)


_REMOTE_FETCH_ERROR_MESSAGES = {
    "remote_url_invalid": "这个链接格式不合法，当前无法读取。",
    "remote_url_scheme_forbidden": "这个链接不是公开的 http/https 地址，出于安全原因无法读取。",
    "remote_url_credentials_forbidden": "这个链接包含账号或凭据，出于安全原因无法读取；请提供不含凭据的公开链接。",
    "remote_url_host_forbidden": "这个链接指向本机或内部主机，出于安全原因无法读取；请提供公开直链。",
    "remote_url_dns_failed": "这个链接的公开地址暂时无法解析，请检查链接或稍后重试。",
    "remote_url_synthetic_dns_address": "本机 DNS 返回了代理虚拟地址，当前未启用公网解析兼容；这是访问环境问题，不代表你提供的是内网链接。",
    "remote_url_public_dns_unavailable": "本机 DNS 返回了代理虚拟地址，但加密公网解析暂时失败；这是访问环境问题，尚未读取链接内容。",
    "remote_url_request_target_mismatch": "请求目标与已校验的公网地址不一致，已停止读取。",
    "remote_url_private_address": "这个链接指向本机、局域网或内部地址，出于安全原因无法读取；请提供公开直链。",
    "remote_url_peer_unverifiable": "无法确认远端连接的公开地址，出于安全原因已停止读取。",
    "remote_url_peer_mismatch": "远端连接地址与预检查结果不一致，出于安全原因已停止读取。",
    "remote_url_redirect_missing": "远端返回了无效跳转，当前无法继续读取。",
    "remote_url_redirect_limit": "这个链接跳转次数过多，当前已停止读取。",
    "remote_url_https_downgrade": "这个链接从 HTTPS 跳转到不安全的 HTTP，当前已停止读取。",
    "remote_media_provider_not_allowed": "这个网页平台当前不在可调用的媒体下载范围内；请提供受支持平台链接或公开媒体直链。",
    "remote_media_ytdlp_unavailable": "当前媒体获取服务暂时不可用；链接本身不一定有问题。",
    "remote_media_bilibili_identity_missing": "这个 B 站链接里没有解析出明确的单个视频编号。",
    "remote_media_bilibili_api_failed": "B 站公开媒体接口暂时没有返回可用信息，请稍后重试。",
    "remote_media_bilibili_stream_missing": "B 站公开媒体接口没有返回可下载的视频流。",
    "remote_media_multi_segment_unsupported": "这个视频由多个分段组成，而当前环境暂时无法安全合并这些分段。",
    "remote_media_playlist_not_supported": "这个链接更像播放列表/合集，当前只支持单个视频或音频链接。",
    "remote_media_cookie_database_locked": (
        "当前媒体获取服务没能读取已配置的站点凭据；这是服务端配置问题，不需要用户处理。"
    ),
    "remote_media_cookie_unavailable": "当前媒体获取服务的站点凭据不可用；这是服务端配置问题。",
    "remote_media_cookie_domain_forbidden": "yt-dlp Cookie 文件包含非受支持媒体平台的域，出于安全原因未加载。",
    "remote_media_browser_cookies_forbidden": (
        "当前媒体获取服务拒绝直接读取浏览器凭据；这是服务端配置问题。"
    ),
    "remote_media_precondition_failed": (
        "远端拒绝了这次媒体信息请求，像是平台风控或前置校验失败；"
        "可以稍后重试或换原始公开链接。"
    ),
    "remote_media_forbidden": "远端拒绝了这次下载请求，可能有权限或地区限制。",
    "remote_media_not_found": "这个链接对应的页面或媒体文件似乎不存在了。",
    "attachment_download_timeout": "下载超时了，可能是网络不稳定，或者远端响应太慢。",
    "attachment_too_large": "远程媒体超过当前大小限制，请换较小的文件或先压缩后重试。",
    "remote_media_extract_failed": "这次没有解析出可用的媒体信息，请检查公开链接或稍后重试。",
    "remote_media_download_missing": "下载完成后没有生成可用的媒体文件，请稍后重试。",
    "remote_media_download_failed": "这次媒体下载没有成功，请检查网络、权限或稍后重试。",
}


class AttachmentFailuresMixin:
    """Methods use the state owned by AttachmentIngestService."""

    def _remote_fetch_failure_needs_ytdlp_hint(self, failures: list[dict[str, str]]) -> bool:
        for entry in failures:
            error = str(entry.get("error") or "").strip().lower()
            if "yt-dlp 无法导入" in error or "没有可用的公开视频下载器" in error:
                return True
            if "没有可用的 yt-dlp" in error or "安装或修复 yt-dlp" in error:
                return True
            if "no module named" in error and "yt" in error and "dlp" in error:
                return True
        return False

    def _humanize_remote_fetch_error(self, error: str) -> str:
        text = str(error or "").strip()
        lowered = text.lower()
        if lowered in _REMOTE_FETCH_ERROR_MESSAGES:
            return _REMOTE_FETCH_ERROR_MESSAGES[lowered]
        if "playlist" in lowered or "合集" in text:
            return "这个链接更像播放列表/合集，当前只支持单个视频或音频链接。"
        if "could not copy" in lowered and "cookie database" in lowered:
            return "当前媒体获取服务没能读取已配置的站点凭据；这是服务端配置问题，不需要用户处理。"
        if "yt-dlp" in lowered:
            return "当前媒体获取服务暂时不可用；链接本身不一定有问题。"
        if "attachment handle allocation failed" in lowered or (
            "unique constraint failed" in lowered and "attachment_inbox_items" in lowered
        ):
            return "这次把链接素材放进工作台时出了点临时问题，请稍后再试一次。"
        if "unsupported url" in lowered or "unsupported" in lowered:
            return "这个链接当前下载器还不认识，可能不是公开可抓取的媒体页面。"
        if "private" in lowered or "login" in lowered or "sign in" in lowered:
            return "这个链接可能需要登录、会员或额外权限，当前不能直接抓取。"
        if "412" in lowered or "precondition failed" in lowered:
            return (
                "远端拒绝了这次媒体信息请求，像是平台风控或前置校验失败；"
                "可以稍后重试或换原始公开链接。"
            )
        if "403" in lowered or "forbidden" in lowered:
            return "远端拒绝了这次下载请求，可能有权限或地区限制。"
        if "cookie" in lowered and ("not found" in lowered or "no such file" in lowered or "cannot" in lowered):
            return "当前媒体获取服务的站点凭据不可用；这是服务端配置问题。"
        if "404" in lowered or "not found" in lowered:
            return "这个链接对应的页面或媒体文件似乎不存在了。"
        if "timeout" in lowered or "timed out" in lowered:
            return "下载超时了，可能是网络不稳定，或者远端响应太慢。"
        if "过大" in text or "max_filesize" in lowered:
            return "远程媒体超过当前大小限制，请换较小的文件或先压缩后重试。"
        return "链接媒体获取失败，请检查公开链接或稍后重试。"

    def _material_failure_code(self, error: Any) -> str:
        if isinstance(error, AttachmentMaterializationError):
            return error.code
        if isinstance(error, PublicUrlPolicyError):
            return error.code
        text = str(error or "").strip()
        lowered = text.lower()
        stable_codes = {
            'attachment_materialization_failed',
            'attachment_source_unavailable',
            'attachment_trusted_local_missing',
            'attachment_untrusted_local_source',
            'attachment_vision_unavailable',
            'image_content_invalid',
            'image_pixel_limit',
            'onebot_temporary_link_expired',
        }
        if lowered in stable_codes or lowered in _REMOTE_FETCH_ERROR_MESSAGES:
            return lowered
        if "bad request" in lowered or "400 client error" in lowered:
            return "onebot_temporary_link_expired"
        if "视觉模型" in text or "vision" in lowered:
            return "attachment_vision_unavailable"
        if "timeout" in lowered or "timed out" in lowered:
            return "attachment_download_timeout"
        if "附件过大" in text or "图像过大" in text or "too large" in lowered:
            return "attachment_too_large"
        if "没有可下载" in text:
            return "attachment_source_unavailable"
        return "attachment_materialization_failed"

    def _humanize_failure(self, error: str, *, kind: str = "") -> str:
        text = str(error or "").strip()
        lowered = text.lower()
        normalized_kind = self._normalize_kind(kind)
        kind_label = (
            "图片"
            if normalized_kind == "image"
            else "音频"
            if normalized_kind == "audio"
            else "文件"
            if normalized_kind in ("document", "file")
            else "附件"
        )
        if lowered == "onebot_temporary_link_expired":
            return (
                f"QQ 临时{kind_label}链接已过期。建议：请用户在 QQ 上重新发送一次这个{kind_label}，"
                f"新生成的临时链接就可以用了。"
            )
        if lowered == "attachment_vision_unavailable":
            return "图片已接收，但视觉模型暂时不可用。建议：稍后重试，或先用文字描述需要关注的内容。"
        if lowered == "image_content_invalid":
            return "文件含有图片标记，但图像数据未通过校验；未提交视觉分析，请重新导出或发送原图。"
        if lowered == "image_pixel_limit":
            return "图片像素尺寸超过安全读取上限；未提交视觉分析，请缩小尺寸后重发。"
        if lowered == "attachment_download_timeout":
            return (
                f"{kind_label}下载超时（可能是网络波动或文件较大）。"
                f"建议：请用户稍后重试，或将{kind_label}通过其他方式发送（如电脑端直接拖拽）。"
            )
        if lowered == "attachment_too_large":
            return f"{kind_label}超过当前大小限制。建议：请用户压缩{kind_label}后重发，或使用文件传输助手等替代方式。"
        if lowered.startswith(("remote_url_", "remote_media_")):
            return self._humanize_remote_fetch_error(lowered)
        if lowered in {
            "attachment_source_unavailable",
            "attachment_untrusted_local_source",
            "attachment_trusted_local_missing",
        }:
            return (
                f"没有拿到{kind_label}的可下载地址或本地缓存。"
                f"建议：请用户在 QQ 上重新发送一次这个{kind_label}；如果仍失败，可尝试通过桌面端拖拽发送。"
            )
        return f"{kind_label}处理失败。建议：稍后重试，或换一种方式发送。"

    def _remote_fetch_error_code(self, error: Any) -> str:
        if isinstance(error, (AttachmentMaterializationError, PublicUrlPolicyError)):
            return error.code
        return self._classify_ytdlp_error(error, phase="download")

    def _classify_ytdlp_error(self, error: Any, *, phase: str) -> str:
        text = str(error or "").strip()
        lowered = text.lower()
        if "playlist" in lowered or "合集" in text:
            return "remote_media_playlist_not_supported"
        if "could not copy" in lowered and "cookie database" in lowered:
            return "remote_media_cookie_database_locked"
        if "cookie" in lowered and ("not found" in lowered or "no such file" in lowered or "cannot" in lowered):
            return "remote_media_cookie_unavailable"
        if "412" in lowered or "precondition failed" in lowered:
            return "remote_media_precondition_failed"
        if "403" in lowered or "forbidden" in lowered:
            return "remote_media_forbidden"
        if "404" in lowered or "not found" in lowered:
            return "remote_media_not_found"
        if "timeout" in lowered or "timed out" in lowered:
            return "attachment_download_timeout"
        if "max_filesize" in lowered or "too large" in lowered or "过大" in text:
            return "attachment_too_large"
        if "unsupported url" in lowered or "unsupported" in lowered or "no suitable extractor" in lowered:
            return "remote_media_provider_not_allowed"
        return "remote_media_extract_failed" if phase == "extract" else "remote_media_download_failed"
