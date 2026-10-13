"""Card rendering for AttachmentInboxService."""

from __future__ import annotations

from typing import Any


class AttachmentCardRenderingMixin:
    def _render_media_info_lines(self, media_info: dict[str, Any], *, prefix: str = "   ") -> list[str]:
        lines: list[str] = []
        format_name = str(media_info.get("format_name") or "").strip()
        duration = self._format_duration_label(media_info.get("duration_seconds"))
        file_size = self._format_size(int(media_info.get("file_size") or 0)) if media_info.get("file_size") else ""
        basics = []
        if format_name:
            basics.append(f"容器/格式：{format_name}")
        if duration:
            basics.append(f"时长：{duration}")
        if file_size:
            basics.append(f"大小：{file_size}")
        if basics:
            lines.append(prefix + "媒体信息：" + "；".join(basics))
        audio = media_info.get("audio") if isinstance(media_info.get("audio"), dict) else {}
        if audio:
            audio_bits = [
                f"编码 {audio.get('codec') or '未知'}",
                f"{audio.get('sample_rate') or '未知'}Hz",
                f"{audio.get('channels') or '未知'}声道",
            ]
            if audio.get("bit_rate"):
                audio_bits.append(self._format_bitrate(int(audio.get("bit_rate") or 0)))
            lines.append(prefix + "音频：" + "，".join(bit for bit in audio_bits if bit) + "。")
        else:
            lines.append(prefix + "音频：未检测到音轨。")
        video = media_info.get("video") if isinstance(media_info.get("video"), dict) else {}
        if video:
            video_bits = [
                f"编码 {video.get('codec') or '未知'}",
                f"{video.get('width') or '?'}x{video.get('height') or '?'}",
            ]
            if isinstance(video.get("fps"), (int, float)) and video.get("fps") > 0:
                video_bits.append(f"{video.get('fps'):g}fps")
            lines.append(prefix + "视频：" + "，".join(video_bits) + "。")
        return lines

    def _render_remote_source_lines(self, remote_source: dict[str, Any], *, prefix: str = "   ") -> list[str]:
        from ..public_url_policy import public_url_display_origin
        platform = self._safe_prompt_label(remote_source.get("platform") or remote_source.get("extractor"))
        uploader = self._safe_prompt_label(remote_source.get("uploader"))
        display_origin = public_url_display_origin(remote_source.get("display_origin"))
        if not display_origin:
            display_origin = public_url_display_origin(
                remote_source.get("webpage_url") or remote_source.get("source_url")
            )
        pieces = []
        if platform:
            pieces.append(f"平台 {platform}")
        if uploader:
            pieces.append(f"发布者 {uploader}")
        if display_origin:
            pieces.append(f"链接 {display_origin}")
        if not pieces:
            return []
        return [prefix + "来源：" + "；".join(pieces)]

    def _render_manifest_item(self, index: int, item: dict[str, Any]) -> list[str]:
        detail = item.get("detail") if isinstance(item.get("detail"), dict) else {}
        parts = [
            f"{index}. {self._compact_item_label(item)}",
        ]
        meta = []
        file_kind = str(detail.get("file_kind") or item.get("file_ext") or "").strip()
        if file_kind:
            meta.append(f"格式={file_kind}")
        file_size = int(item.get("file_size") or detail.get("file_size") or 0)
        if file_size:
            meta.append(f"大小={self._format_size(file_size)}")
        source_label = self._source_label(item)
        if source_label:
            meta.append(f"来源={source_label}")
        if detail.get("line_count") is not None:
            meta.append(f"行数={detail.get('line_count')}")
        if detail.get("page_count") is not None:
            meta.append(f"页数={detail.get('page_count')}")
        if detail.get("paragraph_count") is not None:
            meta.append(f"段落={detail.get('paragraph_count')}")
        media_info = detail.get("media_info") if isinstance(detail.get("media_info"), dict) else {}
        if media_info:
            duration = self._format_duration_label(media_info.get("duration_seconds"))
            if duration:
                meta.append(f"时长={duration}")
            audio = media_info.get("audio") if isinstance(media_info.get("audio"), dict) else {}
            if audio:
                audio_bits = [
                    str(audio.get("codec") or "").strip(),
                    f"{audio.get('sample_rate')}Hz" if audio.get("sample_rate") else "",
                    f"{audio.get('channels')}声道" if audio.get("channels") else "",
                ]
                meta.append("音频=" + ",".join(bit for bit in audio_bits if bit))
            video = media_info.get("video") if isinstance(media_info.get("video"), dict) else {}
            if video:
                video_bits = [
                    str(video.get("codec") or "").strip(),
                    f"{video.get('width')}x{video.get('height')}" if video.get("width") and video.get("height") else "",
                    f"{video.get('fps'):g}fps" if isinstance(video.get("fps"), (int, float)) else "",
                ]
                meta.append("视频=" + ",".join(bit for bit in video_bits if bit))
        remote_source = detail.get("remote_source") if isinstance(detail.get("remote_source"), dict) else {}
        if remote_source:
            platform = str(remote_source.get("platform") or remote_source.get("extractor") or "").strip()
            uploader = str(remote_source.get("uploader") or "").strip()
            if platform:
                meta.append(f"来源={platform}")
            if uploader:
                meta.append(f"发布者={uploader}")
        sheet_names = self._normalize_text_list(detail.get("sheet_names") or [])
        if sheet_names:
            meta.append("工作表=" + ",".join(sheet_names[:6]))
        entities = self._normalize_text_list(detail.get("entities") or detail.get("objects") or [])
        if entities:
            meta.append("要素=" + ",".join(entities[:8]))
        tags = self._normalize_text_list(detail.get("mood_tags") or detail.get("tags") or detail.get("keywords") or [])
        if tags:
            meta.append("标签=" + ",".join(tags[:8]))
        if item.get("status") == "failed":
            failure = self._readable_failure_message(item)
            if failure:
                meta.append(f"失败={failure}")
        if meta:
            parts.append("   " + "；".join(meta))
        return parts
