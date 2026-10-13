"""QQ reply segmentation, occurrence-preserving replay filtering and staged delivery."""

from __future__ import annotations

import re
from typing import Any

from memcore import segment_speech


def _normalize_reply_text(value: Any) -> str:
    return " ".join(str(value or "").replace("\r\n", "\n").replace("\r", "\n").split()).strip()


def _canonical_reply_text(value: Any) -> str:
    return re.sub(r"[\s，。！？!?~～、,.;；:：…—-]+", "", str(value or "").strip().lower())


def _strip_canonical_prefix(text: str, canonical_prefix: str) -> str:
    prefix = str(canonical_prefix or "")
    if not prefix:
        return str(text or "").strip()
    matched = ""
    for index, character in enumerate(str(text or "")):
        canonical_character = _canonical_reply_text(character)
        if not canonical_character:
            continue
        matched += canonical_character
        if not prefix.startswith(matched):
            return str(text or "").strip()
        if matched == prefix:
            return str(text or "")[index + 1 :].strip(" \t\r\n，。！？!?~～、,.;；:：…—-")
    return str(text or "").strip()


def _reply_prefix_at_boundary(text: str, prefix: str) -> bool:
    matched = ""
    for index, char in enumerate(text):
        matched += _canonical_reply_text(char)
        if matched == prefix:
            return index + 1 == len(text) or not _canonical_reply_text(text[index + 1])
        if not prefix.startswith(matched):
            return False
    return False


def _filter_unsent_reply_messages(messages: list[str], sent_messages: list[str]) -> list[str]:
    """Subtract delivered text across segment boundaries, preserving occurrences."""
    remaining = [line.strip() for item in sent_messages for line in str(item).splitlines()
                 if _canonical_reply_text(line)]
    unsent: list[str] = []
    for message in messages:
        text = str(message or "").strip()
        while text and remaining:
            canonical = _canonical_reply_text(text)
            best = None
            for start in range(len(remaining)):
                prefix = ""
                raw_prefix = ""
                for end in range(start, len(remaining)):
                    prefix += _canonical_reply_text(remaining[end])
                    raw_prefix += "\n" + remaining[end]
                    if not (canonical.startswith(prefix) or prefix.startswith(canonical)):
                        break
                    if not ((_reply_prefix_at_boundary(text, prefix) and canonical.startswith(prefix))
                            or (_reply_prefix_at_boundary(raw_prefix, canonical) and prefix.startswith(canonical))):
                        continue
                    matched = min(len(canonical), len(prefix))
                    if matched and (best is None or matched > best[0]):
                        best = (matched, start)
            if best is None:
                break
            count, start = best
            text = _strip_canonical_prefix(text, canonical[:count])
            while count and start < len(remaining):
                sent = _canonical_reply_text(remaining[start])
                if len(sent) <= count:
                    count -= len(sent)
                    remaining.pop(start)
                else:
                    remaining[start] = _strip_canonical_prefix(remaining[start], sent[:count])
                    count = 0
        if text:
            unsent.append(text)
    return unsent


class _StageSpeechReplay:
    """Skip an exact leading replay across model stages without buffering speech.

    New text leaves push() immediately. Repetitions within one stage retain
    their count; only prefixes already accepted by an earlier stage are skipped.
    """

    def __init__(self):
        self.previous: list[str] = []
        self.current = ""
        self.candidates: list[str] = []

    def push(self, text: str) -> str:
        before = _canonical_reply_text(self.current)
        self.current += "\n" + text
        canonical = _canonical_reply_text(self.current)
        continuing = [old for old in self.candidates if _canonical_reply_text(old).startswith(canonical)
                      and _reply_prefix_at_boundary(old, canonical)]
        completed = [old for old in self.candidates if canonical.startswith(_canonical_reply_text(old))
                     and len(_canonical_reply_text(old)) > len(before)
                     and _reply_prefix_at_boundary(self.current, _canonical_reply_text(old))]
        if continuing:
            self.candidates = continuing
            return ""
        self.candidates = []
        if completed:
            longest = max((_canonical_reply_text(old) for old in completed), key=len)
            return _strip_canonical_prefix(text, longest[len(before):])
        return text

    def remember_delivered(self, text: str) -> None:
        if text not in self.previous:
            self.previous.append(text)
        if not self.current:
            self.candidates = list(self.previous)

    def finish_stage(self) -> None:
        if self.current and self.current not in self.previous:
            self.previous.append(self.current)
        self.current = ""
        self.candidates = list(self.previous)


def _send_pending_stage_messages(
    *,
    qq_gateway: Any,
    context: Any,
    pending_messages: list[str],
    deferred_messages: list[str],
    streamed_messages: list[str],
    stream_send_results: list[dict[str, Any]],
    max_streamed: int,
) -> list[str]:
    for text in pending_messages:
        normalized = _normalize_reply_text(text)
        if not normalized:
            continue
        chunks = _split_reply_for_transport(text)
        for chunk in chunks:
            if not chunk:
                continue
            if max_streamed > 0 and len(streamed_messages) >= max_streamed:
                deferred_messages.append(chunk)
                continue
            result = qq_gateway.send_reply(context, chunk)
            stream_send_results.append(result)
            if bool(result.get("ok")):
                streamed_messages.append(chunk)
            else:
                # A failed immediate send is still model-authored content.
                # Keep it for the ordinary end-of-turn delivery attempt.
                deferred_messages.append(chunk)
    return []


def _coalesce_deferred_stream_messages(messages: list[str], *, max_chars: int = 1800) -> list[str]:
    """Merge delayed stream segments without deleting model-authored text."""

    visible = [str(item or "") for item in messages if str(item or "").strip()]
    if not visible:
        return []
    joined = "\n".join(visible).strip()
    return _split_reply_for_transport(joined, max_chars=max_chars)


def _split_reply_for_transport(text: str, *, max_chars: int = 1800) -> list[str]:
    """Coalesce safe segments; the delivery target must not break nested pairs."""
    parts = segment_speech(text, min_chars=1, max_chars=max_chars)
    chunks: list[str] = []
    for part in parts:
        if chunks and len(chunks[-1]) + len(part) + 1 <= max_chars:
            chunks[-1] += "\n" + part
        else:
            chunks.append(part)
    return chunks
