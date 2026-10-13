"""QQ messages delivery policy and its owned state."""
from __future__ import annotations

import time
from contextlib import nullcontext
from pathlib import Path
from typing import Any, Callable

from channelcore_onebot import (
    OutboundAction,
    OutboundTarget,
    ReplyReferenceLedger,
    build_message_action,
    mface_segment,
    music_segment,
    text_segment,
)

import config
from companion_v01.channels.qq.commands.parser import split_qq_command
from companion_v01.channels.qq.constants import QQ_MUSIC_PLATFORM_TO_ONEBOT
from companion_v01.channels.qq.context import QQMessageContext
from companion_v01.channels.qq.delivery.emotions import _normalize_mface_payload


class QQMessageDelivery:
    """Own messages behavior; transport and cross-domain operations use the injected port."""

    def __init__(self, *, gateway: Any) -> None:
        self.gateway = gateway
        self._reply_reference_ledger = ReplyReferenceLedger(max_claims=4096)

    def render_reply_text(self, frame: dict[str, Any]) -> str:
        return "\n".join(self.gateway.render_reply_messages(frame)).strip()

    @staticmethod
    def _trim_segment_ending(text: str) -> str:
        """Hide only an ordinary trailing Chinese full stop in QQ bubbles.

        Question/exclamation marks and punctuation clusters carry meaning and
        stay visible.  This is presentation-only: the authoritative ``speech``
        text used by memory and TTS keeps its original punctuation.
        """
        text = str(text or "").strip()
        if not text:
            return text
        if len(text) > 1 and text.endswith("。") and text[-2] not in "。！？!?…":
            return text[:-1].strip()
        return text

    def render_reply_messages(self, frame: dict[str, Any]) -> list[str]:
        segment_texts: list[str] = []
        max_segments = max(1, min(20, int(getattr(config, "QQ_REPLY_MAX_SEGMENTS", 8) or 8)))
        segments = frame.get("speech_segments")
        if isinstance(segments, list):
            for item in segments:
                text = self._trim_segment_ending(str(item or ""))
                if text:
                    segment_texts.append(text)

        if not segment_texts:
            speech = str(frame.get("speech") or "").replace("\r\n", "\n").replace("\r", "\n").strip()
            inferred = [self._trim_segment_ending(line) for line in speech.split("\n") if line.strip()]
            if len(inferred) > 1:
                segment_texts = inferred
            elif speech:
                segment_texts = [self._trim_segment_ending(speech)]

        if len(segment_texts) > max_segments:
            messages = [
                *segment_texts[: max_segments - 1],
                "\n".join(segment_texts[max_segments - 1 :]).strip(),
            ]
        else:
            messages = segment_texts

        code_snippet = str(frame.get("code_snippet") or "").strip()
        if code_snippet:
            if messages:
                messages[-1] = f"{messages[-1]}\n\n{code_snippet}".strip()
            else:
                messages.append(code_snippet)
        return [message for message in messages if message]

    def send_replies(self, context: QQMessageContext, messages: list[str]) -> dict[str, Any]:
        clean_messages = [str(message or "").strip() for message in messages if str(message or "").strip()]
        if not clean_messages:
            return {"ok": False, "reason": "empty_messages", "results": []}

        results: list[dict[str, Any]] = []
        delay_seconds = min(3.0, max(0.0, float(getattr(config, "QQ_REPLY_SEGMENT_DELAY_SECONDS", 0.8) or 0.0)))
        for index, message in enumerate(clean_messages):
            if index > 0:
                time.sleep(delay_seconds)
            results.append(self.gateway.send_reply(context, message, include_reply=index == 0))
        return {
            "ok": all(bool(result.get("ok")) for result in results),
            "count": len(results),
            "results": results,
        }

    def send_generated_files(
        self, context: QQMessageContext, tool_events: list[dict[str, Any]] | None,
        *, target_allowed: Callable | None = None,
    ) -> dict[str, Any]:
        events = [event for event in tool_events or [] if isinstance(event, dict)]
        targets: list[dict[str, Any]] = []
        seen_targets: set[tuple[str, str]] = set()
        for event in events:
            if not self._event_allows_qq_file_delivery(event):
                continue
            event_type = str(event.get("type") or "")
            if event_type not in {"generated_file_ready", "file_ready", "file_unavailable"}:
                continue
            if not bool(event.get("send_to_user")):
                continue
            if event_type in {"file_ready", "file_unavailable"}:
                file_ref = event.get("file") if isinstance(event.get("file"), dict) else {}
                if event_type == "file_unavailable":
                    if file_ref.get("source_type") == "shop_confirmation":
                        target = {"source_type": "shop_confirmation", "source_id": str(file_ref.get("source_id") or ""),
                                  "unavailable": True}
                        identity = self._qq_file_delivery_target_identity(target)
                        if identity not in seen_targets:
                            seen_targets.add(identity)
                            targets.append(target)
                    continue
                path = str(file_ref.get("absolute_path") or "").strip()
                name = str(file_ref.get("name") or file_ref.get("title") or Path(path).name).strip()
                mime_type = str(file_ref.get("mime_type") or "").strip().lower()
                ext = Path(name or path).suffix.lower().lstrip(".")
                if path:
                    target = {
                        "generated_id": str(file_ref.get("generated_id") or "").strip(),
                        "source_id": str(
                            file_ref.get("source_id")
                            or file_ref.get("attachment_id")
                            or file_ref.get("generated_id")
                            or ""
                        ).strip(),
                        "source_type": str(file_ref.get("source_type") or "").strip().lower() or "file",
                        "handle": str(
                            file_ref.get("handle")
                            or file_ref.get("attachment_handle")
                            or file_ref.get("generated_handle")
                            or ""
                        ).strip(),
                        "path": path,
                        "name": name or Path(path).name,
                        # Other image containers (SVG, TIFF, PSD, etc.) remain
                        # downloadable files; an image MIME is not QQ support.
                        "is_image": ext in {"png", "jpg", "jpeg", "webp", "gif"},
                        "is_audio": ext in {"mp3", "wav", "flac", "m4a", "aac", "ogg", "opus"}
                        or mime_type.startswith("audio/"),
                        "delivery_mode": str(event.get("delivery_mode") or "file").strip().lower(),
                    }
                    identity = self._qq_file_delivery_target_identity(target)
                    if identity in seen_targets:
                        continue
                    seen_targets.add(identity)
                    targets.append(target)
                continue

            generated = event.get("generated_file") if isinstance(event.get("generated_file"), dict) else {}
            path = str(generated.get("absolute_path") or "").strip()
            generated_id = str(generated.get("generated_id") or "").strip()
            title = str(generated.get("output_title") or generated.get("generated_handle") or "akane_output").strip()
            ext = str(generated.get("file_ext") or generated.get("output_format") or "").strip().lstrip(".")
            mime_type = str(generated.get("mime_type") or "").strip().lower()
            if path and generated_id:
                target = {
                    "generated_id": generated_id,
                    "source_id": generated_id,
                    "source_type": "generated",
                    "handle": str(generated.get("generated_handle") or "").strip(),
                    "path": path,
                    "name": f"{title}.{ext}" if ext and not title.lower().endswith(f".{ext.lower()}") else title,
                    "is_image": ext.lower() in {"png", "jpg", "jpeg", "webp", "gif"},
                    "is_audio": ext.lower() in {"mp3", "wav", "flac", "m4a", "aac", "ogg", "opus"}
                    or mime_type.startswith("audio/"),
                    "delivery_mode": str(event.get("delivery_mode") or "file").strip().lower(),
                }
                identity = self._qq_file_delivery_target_identity(target)
                if identity in seen_targets:
                    continue
                seen_targets.add(identity)
                targets.append(target)
        if not targets:
            return {"ok": True, "count": 0, "results": []}

        # The current turn's structured tool event is the delivery decision.
        # The gateway validates transport-facing boundaries above, then sends
        # exactly the files selected by the model instead of reinterpreting the
        # user's natural-language message with another intent classifier.
        return self._send_generated_file_targets(context, targets,
            **({"target_allowed": target_allowed} if target_allowed else {}))

    @staticmethod
    def _qq_file_delivery_target_identity(target: dict[str, Any]) -> tuple[str, str]:
        source_type = str(target.get("source_type") or "").strip().lower() or "file"
        source_id = str(
            target.get("source_id")
            or target.get("generated_id")
            or target.get("attachment_id")
            or target.get("handle")
            or target.get("path")
            or ""
        ).strip()
        return source_type, source_id

    def _send_generated_file_targets(
        self,
        context: QQMessageContext,
        targets: list[dict[str, Any]],
        *, target_allowed: Callable | None = None,
    ) -> dict[str, Any]:
        results: list[dict[str, Any]] = []
        for target in targets:
            from companion_v01.turn_coordination import cancellation_scope

            with cancellation_scope(lambda: not target_allowed(target)) if target_allowed else nullcontext():
                delivery_mode = str(target.get("delivery_mode") or "file").strip().lower()
                if delivery_mode not in {"file", "voice", "both"}:
                    delivery_mode = "file"
                is_audio = bool(target.get("is_audio"))
                if target.get("unavailable"):
                    result = {"ok": False, "status": "failed", "reason": "shop_card_unavailable"}
                elif delivery_mode in {"voice", "both"} and not is_audio:
                    result = {
                        "ok": False,
                        "status": "failed",
                        "reason": "voice_delivery_requires_audio",
                        "delivery_mode": delivery_mode,
                    }
                elif bool(target.get("is_image")):
                    result = dict(self.gateway.send_image(
                        context,
                        image_path=str(target.get("path") or ""),
                        name=str(target.get("name") or ""),
                    ))
                    result["delivery_mode"] = "file"
                elif is_audio:
                    if delivery_mode == "both":
                        voice_result = dict(self.gateway.send_voice(
                            context,
                            audio_path=str(target.get("path") or ""),
                            name=str(target.get("name") or ""),
                        ))
                        file_result = dict(self.gateway.send_file(
                            context,
                            file_path=str(target.get("path") or ""),
                            name=str(target.get("name") or ""),
                        ))
                        result = {
                            "ok": bool(voice_result.get("ok")) and bool(file_result.get("ok")),
                            "delivery_mode": "both",
                            "voice_result": voice_result,
                            "file_result": file_result,
                        }
                    elif delivery_mode == "file":
                        result = dict(self.gateway.send_file(
                            context,
                            file_path=str(target.get("path") or ""),
                            name=str(target.get("name") or ""),
                        ))
                        result["delivery_mode"] = "file"
                    else:
                        result = dict(self.gateway.send_voice(
                            context,
                            audio_path=str(target.get("path") or ""),
                            name=str(target.get("name") or ""),
                        ))
                        result["delivery_mode"] = "voice"
                else:
                    result = dict(self.gateway.send_file(
                        context,
                        file_path=str(target.get("path") or ""),
                        name=str(target.get("name") or ""),
                    ))
                    result["delivery_mode"] = "file"
            result["generated_id"] = str(target.get("generated_id") or "")
            if target.get("source_type") == "shop_confirmation":
                result["source_type"] = "shop_confirmation"
                result["source_id"] = str(target.get("source_id") or "")
            results.append(result)
        all_ok = bool(results) and all(bool(result.get("ok")) for result in results)
        any_ok = any(bool(result.get("ok")) or bool((result.get("voice_result") or {}).get("ok"))
                     or bool((result.get("file_result") or {}).get("ok")) for result in results)
        status = "sent" if all_ok else "partial" if any_ok else "failed"
        return {
            "ok": all_ok,
            "status": status,
            "count": len(results),
            "results": results,
        }

    def _event_allows_qq_file_delivery(self, event: dict[str, Any]) -> bool:
        event_mode = str(event.get("client_mode") or "").strip().lower()
        return not event_mode or event_mode == "qq_text"

    def send_stickers(self, context: QQMessageContext, tool_events: list[dict[str, Any]] | None) -> dict[str, Any]:
        events = [event for event in tool_events or [] if isinstance(event, dict)]
        targets: list[dict[str, Any]] = []
        for event in events:
            if str(event.get("type") or "") != "sticker_ready":
                continue
            if not bool(event.get("send_to_user")):
                continue
            sticker = event.get("sticker") if isinstance(event.get("sticker"), dict) else {}
            path = str(sticker.get("absolute_path") or "").strip()
            if not path:
                continue
            targets.append(
                {
                    "sticker_id": str(sticker.get("id") or "").strip(),
                    "path": path,
                    "name": str(sticker.get("display_name") or Path(path).stem).strip(),
                }
            )
        if not targets:
            return {"ok": True, "count": 0, "results": []}

        results: list[dict[str, Any]] = []
        for target in targets:
            result = self.gateway.send_image(
                context,
                image_path=str(target.get("path") or ""),
                name=str(target.get("name") or ""),
            )
            result["sticker_id"] = str(target.get("sticker_id") or "")
            results.append(result)
        return {
            "ok": all(bool(result.get("ok")) for result in results),
            "count": len(results),
            "results": results,
        }

    def send_music_card(
        self,
        context: QQMessageContext,
        *,
        platform: str,
        track_id: str,
    ) -> dict[str, Any]:
        """Send one QQ native music card; returns the real NapCat transport result."""
        clean_platform = str(platform or "").strip()
        clean_track_id = str(track_id or "").strip()
        onebot_type = QQ_MUSIC_PLATFORM_TO_ONEBOT.get(clean_platform)
        if onebot_type is None:
            return {"ok": False, "status": "unavailable", "reason": "unsupported_music_card_platform"}
        if not context.target_id or not clean_track_id:
            return {"ok": False, "reason": "empty_target_or_music"}
        try:
            plan = build_message_action(
                self._outbound_target(context),
                [music_segment(onebot_type, clean_track_id)],
                reply_to=self._reply_reference_for_content(context, ("music",)),
            )
        except ValueError as exc:
            return self._outbound_plan_failure(exc)
        result = self._send_outbound_plan(plan, timeout=8)
        result["platform"] = clean_platform
        result["track_id"] = clean_track_id
        return result

    def send_reply(
        self,
        context: QQMessageContext,
        message: str,
        *,
        include_reply: bool = True,
    ) -> dict[str, Any]:
        clean_message = self._trim_segment_ending(str(message or ""))
        if not context.target_id or not clean_message:
            return {"ok": False, "reason": "empty_target_or_message"}
        try:
            plan = build_message_action(
                self._outbound_target(context),
                [text_segment(clean_message)],
                reply_to=self._reply_reference_for_content(
                    context,
                    ("text",),
                    include_reply=include_reply,
                ),
            )
        except ValueError as exc:
            return self._outbound_plan_failure(exc)
        return self._send_outbound_plan(plan, timeout=8)

    def send_mface(self, context: QQMessageContext, *, mface: dict[str, Any]) -> dict[str, Any]:
        """Send a NapCat / OneBot marketplace emoji message segment."""
        data = _normalize_mface_payload(mface)
        if not context.target_id:
            return {"ok": False, "reason": "empty_target"}
        if not data:
            return {"ok": False, "reason": "invalid_mface_payload"}

        try:
            plan = build_message_action(
                self._outbound_target(context),
                [mface_segment(data)],
                reply_to=self._reply_reference_for_content(context, ("mface",)),
            )
        except ValueError as exc:
            return self._outbound_plan_failure(exc)
        return self._send_outbound_plan(plan, timeout=8)

    @staticmethod
    def _outbound_target(context: QQMessageContext) -> OutboundTarget:
        return OutboundTarget("group" if context.is_group else "private", context.target_id)

    def _reply_reference_for_content(
        self,
        context: QQMessageContext,
        content_types: tuple[str, ...],
        *,
        include_reply: bool = True,
    ) -> str:
        """Thin product adapter over channelcore's OneBot reply policy."""
        return self._reply_reference_ledger.claim(
            self._outbound_target(context),
            context.source_message_id,
            content_types,
            enabled=include_reply and split_qq_command(context.clean_message) is None,
        )

    def _send_outbound_plan(self, plan: OutboundAction, *, timeout: float) -> dict[str, Any]:
        return self.gateway._call_and_track_outbound(plan.action, plan.params(), timeout=timeout)

    @staticmethod
    def _outbound_plan_failure(exc: ValueError) -> dict[str, Any]:
        return {
            "ok": False,
            "status": "failed",
            "code": str(exc) or "onebot_outbound_plan_invalid",
            "action": "unknown",
            "data": {},
            "public_reason": "OneBot 出站消息参数无效。",
        }
