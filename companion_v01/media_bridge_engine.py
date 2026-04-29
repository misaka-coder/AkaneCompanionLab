from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from .text_utils import normalize_text


def prefetch_remote_media_links_for_message(
    engine: Any,
    *,
    profile_user_id: str,
    session_id: str,
    message: str,
    timestamp: int | None = None,
) -> dict[str, Any]:
    """Deterministically fetch explicit media links before the final reply."""
    urls = extract_prefetchable_remote_media_urls(message)
    if not urls and message_requests_remote_media_retry(message):
        urls = recent_prefetchable_remote_media_urls(
            engine,
            profile_user_id=profile_user_id,
            session_id=session_id,
        )
    if not urls:
        return {}
    if not message_requests_remote_media_fetch(message, urls=urls):
        return {}
    service = engine._get_attachment_ingest_service()
    if service is None:
        return {}
    return service.fetch_media_from_urls(
        profile_user_id=profile_user_id,
        session_id=session_id,
        urls=urls,
        timestamp=timestamp,
    )


def recent_prefetchable_remote_media_urls(
    engine: Any,
    *,
    profile_user_id: str,
    session_id: str,
    limit: int = 24,
) -> list[str]:
    messages = engine.store.get_session_messages(
        profile_user_id=profile_user_id,
        session_id=session_id,
        limit=limit,
    )
    for item in reversed(messages):
        urls = extract_prefetchable_remote_media_urls(str(item.get("content") or ""))
        if urls:
            return urls[:6]
    return []


def message_requests_remote_media_retry(message: str) -> bool:
    raw_text = str(message or "")
    text = normalize_text(raw_text).lower()
    if not text:
        return False
    retry_markers = (
        "再试",
        "重试",
        "重新试",
        "重新下载",
        "再下载",
        "再来一次",
        "试一次",
        "继续试",
        "完整报错",
        "报错",
        "一字不落",
    )
    media_markers = (
        "下载",
        "链接",
        "视频",
        "音频",
        "素材",
        "工具",
        "报错",
        "工作台",
    )
    haystacks = (raw_text, text)
    has_retry = any(marker in haystack for haystack in haystacks for marker in retry_markers)
    has_media = any(marker in haystack for haystack in haystacks for marker in media_markers)
    return has_retry and has_media


def extract_prefetchable_remote_media_urls(message: str) -> list[str]:
    text = str(message or "")
    if not text:
        return []
    candidates = re.findall(r"https?://[^\s<>\]）)\"'，。；、]+", text)
    normalized: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        url = candidate.rstrip(".,!?;:，。！？；：")
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            continue
        if url in seen:
            continue
        seen.add(url)
        normalized.append(url)
        if len(normalized) >= 6:
            break
    return normalized


def message_requests_remote_media_fetch(message: str, *, urls: list[str]) -> bool:
    text = normalize_text(message).lower()
    if not urls:
        return False
    intent_keywords = (
        "下载",
        "拉进",
        "拉到",
        "获取",
        "转写",
        "转录",
        "字幕",
        "总结",
        "处理",
        "视频",
        "音频",
        "媒体",
        "这个链接",
        "链接",
    )
    if any(keyword in text for keyword in intent_keywords):
        return True
    known_media_hosts = (
        "b23.tv",
        "bilibili.com",
        "youtube.com",
        "youtu.be",
        "douyin.com",
        "iesdouyin.com",
        "ixigua.com",
        "kuaishou.com",
    )
    for url in urls:
        host = urlparse(url).netloc.lower()
        if any(host == known or host.endswith("." + known) for known in known_media_hosts):
            return True
    return False


def wait_for_qq_attachments_settled(
    engine: Any,
    *,
    profile_user_id: str,
    session_id: str,
    attachment_ids: list[str],
    timeout_seconds: float = 8.0,
) -> dict[str, Any]:
    service = engine._get_attachment_inbox_service()
    if service is None:
        return {"ok": True, "ready": [], "failed": [], "pending": [], "missing": []}
    return service.wait_for_attachments_settled(
        profile_user_id=profile_user_id,
        session_id=session_id,
        attachment_ids=attachment_ids,
        timeout_seconds=timeout_seconds,
    )


def mark_generated_file_delivery(
    engine: Any,
    *,
    profile_user_id: str,
    session_id: str,
    generated_id: str,
    delivery_status: str,
    timestamp: int | None = None,
) -> dict[str, Any] | None:
    service = engine._get_generated_file_service()
    if service is None:
        return None
    return service.mark_delivery_status(
        profile_user_id=profile_user_id,
        session_id=session_id,
        generated_id=generated_id,
        delivery_status=delivery_status,
        timestamp=timestamp,
    )
