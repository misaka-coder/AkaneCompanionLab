from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

import config


QQ_TEXT_CAPABILITIES = (
    "speech_segments",
    "file_drop",
    "choices",
    "tool_actions",
)


@dataclass(frozen=True)
class QQMessageContext:
    should_respond: bool
    reason: str
    is_group: bool = False
    target_id: int = 0
    user_id: int = 0
    group_id: int = 0
    session_id: str = ""
    profile_user_id: str = ""
    clean_message: str = ""
    raw_message: str = ""
    extra_context: str = ""
    sender_label: str = ""
    attachments: list[dict[str, Any]] | None = None

    def to_turn_payload(self) -> dict[str, Any]:
        message = self.clean_message
        if self.is_group:
            label = self.sender_label or (f"QQ {self.user_id}" if self.user_id else "群成员")
            message = f"【{label}】{message}"
        return {
            "user_id": self.session_id,
            "real_user_id": self.profile_user_id,
            "message": message,
            "client_mode": "qq_text",
            "client_capabilities": list(QQ_TEXT_CAPABILITIES),
            "extra_context": self.extra_context,
        }


class NapCatQQGateway:
    def __init__(self) -> None:
        self.group_follow_state: dict[str, dict[str, Any]] = {}
        self.recent_event_fingerprints: dict[str, float] = {}
        self.attachment_debounce_state: dict[str, dict[str, Any]] = {}
        self._attachment_debounce_lock = threading.RLock()

    def status(self) -> dict[str, Any]:
        return {
            "enabled": bool(getattr(config, "QQ_BRIDGE_ENABLED", False)),
            "onebot_http_url": self.onebot_http_url,
            "master_qq": str(getattr(config, "MASTER_QQ", "") or ""),
            "bot_qq": self.bot_qq,
            "group_plaintext_enabled": bool(getattr(config, "QQ_GROUP_PLAINTEXT_ENABLED", False)),
            "active_group_attachment_buffer_count": len(self.group_follow_state),
            "active_attachment_debounce_count": len(self.attachment_debounce_state),
        }

    @property
    def onebot_http_url(self) -> str:
        return str(getattr(config, "QQ_ONEBOT_HTTP_URL", "http://127.0.0.1:3001") or "").strip().rstrip("/") or "http://127.0.0.1:3001"

    @property
    def bot_qq(self) -> str:
        return str(getattr(config, "QQ_BOT_QQ", "") or "").strip()

    @property
    def master_qq(self) -> str:
        value = str(getattr(config, "MASTER_QQ", "") or "").strip()
        return value if value.isdigit() else ""

    def build_message_context(self, event: dict[str, Any]) -> QQMessageContext:
        if str(event.get("post_type") or "").strip().lower() != "message":
            return QQMessageContext(False, "not_message_event")

        message_type = str(event.get("message_type") or "").strip().lower()
        is_private = message_type == "private"
        is_group = message_type == "group" or bool(event.get("group_id"))
        if not (is_private or is_group):
            return QQMessageContext(False, "unsupported_message_type")

        user_id = self._safe_int(event.get("user_id"))
        group_id = self._safe_int(event.get("group_id"))
        self_id = self._safe_int(event.get("self_id"))
        if user_id and user_id in {self._safe_int(self.bot_qq), self_id}:
            return QQMessageContext(False, "self_message")

        if self._is_duplicate_event(event):
            return QQMessageContext(False, "duplicate_event")

        raw_message = self.extract_message_text(event)
        clean_message = self.clean_message_text(event, raw_message)
        attachments = self.extract_attachments(event)
        if not clean_message:
            return QQMessageContext(False, "empty_message")

        mentions_bot = self.message_mentions_bot(event, raw_message)
        session_id, profile_user_id = self.resolve_identity(user_id=user_id, group_id=group_id)
        sender_label = self.resolve_sender_label(event=event, user_id=user_id)
        allow_group_plaintext = self._is_group_plaintext_allowed(
            session_id=session_id,
            user_id=user_id,
        )
        allow_group_attachment_buffer = self._is_group_attachment_buffer_allowed(
            session_id=session_id,
            user_id=user_id,
            attachments=attachments,
        )

        if is_group:
            if mentions_bot:
                self._arm_group_attachment_buffer(
                    session_id=session_id,
                    user_id=user_id,
                    reason="group_mention",
                )
            elif allow_group_attachment_buffer:
                pass
            elif not allow_group_plaintext:
                return QQMessageContext(False, "group_message_without_mention")

        return QQMessageContext(
            should_respond=True,
            reason="private"
            if is_private
            else ("group_mention" if mentions_bot else ("group_attachment_buffer" if allow_group_attachment_buffer else "group_follow")),
            is_group=is_group,
            target_id=group_id if is_group else user_id,
            user_id=user_id,
            group_id=group_id,
            session_id=session_id,
            profile_user_id=profile_user_id,
            clean_message=clean_message,
            raw_message=raw_message,
            sender_label=sender_label,
            attachments=attachments,
            extra_context=self.build_extra_context(
                event=event,
                is_group=is_group,
                user_id=user_id,
                group_id=group_id,
                sender_label=sender_label,
            ),
        )

    def extract_message_text(self, event: dict[str, Any]) -> str:
        raw_message = str(event.get("raw_message") or "").strip()
        if raw_message:
            return raw_message

        segments = event.get("message")
        if not isinstance(segments, list):
            return str(segments or "").strip()

        rendered: list[str] = []
        for item in segments:
            if not isinstance(item, dict):
                continue
            seg_type = str(item.get("type") or "").strip().lower()
            seg_data = item.get("data") if isinstance(item.get("data"), dict) else {}
            if seg_type == "text":
                rendered.append(str(seg_data.get("text") or ""))
            elif seg_type == "at":
                rendered.append(f"[CQ:at,qq={str(seg_data.get('qq') or '').strip()}]")
            elif seg_type == "image":
                url_value = str(seg_data.get("url") or "").strip()
                file_value = str(seg_data.get("file") or "").strip()
                if url_value:
                    rendered.append(f"[CQ:image,url={url_value}]")
                elif file_value:
                    rendered.append(f"[CQ:image,file={file_value}]")
                else:
                    rendered.append("[CQ:image]")
            elif seg_type == "file":
                name_value = str(seg_data.get("name") or seg_data.get("file") or "").strip()
                if name_value:
                    rendered.append(f"[CQ:file,name={name_value}]")
                else:
                    rendered.append("[CQ:file]")
            elif seg_type in {"record", "voice"}:
                rendered.append("[CQ:record]")
        return "".join(rendered).strip()

    def clean_message_text(self, event: dict[str, Any], raw_message: str) -> str:
        text = str(raw_message or "").strip()
        has_image = bool(re.search(r"\[CQ:image(?:,[^\]]*)?\]", text))
        has_file = bool(re.search(r"\[CQ:file(?:,[^\]]*)?\]", text))
        has_record = bool(re.search(r"\[CQ:record(?:,[^\]]*)?\]", text))
        text = re.sub(r"\[CQ:image(?:,[^\]]*)?\]", " [图片] ", text)
        text = re.sub(r"\[CQ:file(?:,[^\]]*)?\]", " [文件] ", text)
        text = re.sub(r"\[CQ:record(?:,[^\]]*)?\]", " [语音] ", text)
        for bot_id in self._bot_target_ids(event):
            text = text.replace(f"[CQ:at,qq={bot_id}]", "")
        text = re.sub(r"\[CQ:at,qq=\d+\]", "", text)
        text = re.sub(r"\[CQ:[^\]]+\]", "", text)
        text = re.sub(r"\s+", " ", text).strip()
        if text in {"[图片]", "[文件]", "[语音]"}:
            text = ""
        if text in {"[图片] [文件]", "[文件] [图片]"}:
            text = ""
        if not text:
            if has_image and has_file:
                return "发来了图片和文件。"
            if has_image:
                return "发来了一张图片。"
            if has_file:
                return "发来了一个文件。"
            if has_record:
                return "发来了一段语音。"
        return text

    def extract_attachments(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        attachments: list[dict[str, Any]] = []
        message_id = str(event.get("message_id") or "").strip()
        segments = event.get("message")
        if isinstance(segments, list):
            for index, item in enumerate(segments, start=1):
                if not isinstance(item, dict):
                    continue
                seg_type = str(item.get("type") or "").strip().lower()
                seg_data = item.get("data") if isinstance(item.get("data"), dict) else {}
                parsed = self._segment_to_attachment(seg_type=seg_type, data=seg_data)
                if parsed:
                    parsed["source_message_id"] = message_id
                    parsed["source_event_id"] = str(event.get("message_id") or event.get("time") or "").strip()
                    parsed["segment_index"] = index
                    attachments.append(parsed)
            return attachments

        raw_message = self.extract_message_text(event)
        for match in re.finditer(r"\[CQ:(image|file|record)(?:,([^\]]*))?\]", raw_message):
            seg_type = match.group(1)
            data = self._parse_cq_params(match.group(2) or "")
            parsed = self._segment_to_attachment(seg_type=seg_type, data=data)
            if parsed:
                parsed["source_message_id"] = message_id
                parsed["source_event_id"] = str(event.get("message_id") or event.get("time") or "").strip()
                attachments.append(parsed)
        return attachments

    def _segment_to_attachment(self, *, seg_type: str, data: dict[str, Any]) -> dict[str, Any] | None:
        if seg_type == "image":
            file_value = str(data.get("file") or data.get("filename") or data.get("name") or "").strip()
            return {
                "kind": "image",
                "file": file_value,
                "url": str(data.get("url") or "").strip(),
                "path": str(data.get("path") or data.get("local_path") or "").strip(),
                "origin_name": file_value,
                "mime_type": "image/jpeg" if file_value.lower().endswith((".jpg", ".jpeg")) else "",
                "file_size": self._safe_int(data.get("size") or data.get("file_size")),
            }
        if seg_type == "file":
            file_value = str(data.get("file") or data.get("filename") or "").strip()
            origin_name = str(data.get("name") or file_value or "").strip()
            return {
                "kind": "document",
                "file": file_value or origin_name,
                "url": str(data.get("url") or "").strip(),
                "path": str(data.get("path") or data.get("local_path") or "").strip(),
                "origin_name": origin_name,
                "mime_type": str(data.get("mime_type") or "").strip(),
                "file_size": self._safe_int(data.get("size") or data.get("file_size")),
            }
        if seg_type in {"record", "voice"}:
            file_value = str(data.get("file") or data.get("filename") or data.get("name") or "").strip()
            return {
                "kind": "audio",
                "file": file_value,
                "url": str(data.get("url") or "").strip(),
                "path": str(data.get("path") or data.get("local_path") or "").strip(),
                "origin_name": file_value,
                "mime_type": str(data.get("mime_type") or "audio/mpeg").strip(),
                "file_size": self._safe_int(data.get("size") or data.get("file_size")),
            }
        return None

    def _parse_cq_params(self, raw: str) -> dict[str, str]:
        params: dict[str, str] = {}
        for part in str(raw or "").split(","):
            if "=" not in part:
                continue
            key, value = part.split("=", 1)
            params[key.strip()] = value.strip()
        return params

    def message_mentions_bot(self, event: dict[str, Any], raw_message: str) -> bool:
        if bool(event.get("to_me")):
            return True
        raw_text = str(raw_message or "")
        for bot_id in self._bot_target_ids(event):
            if f"[CQ:at,qq={bot_id}]" in raw_text:
                return True

        segments = event.get("message")
        if isinstance(segments, list):
            for item in segments:
                if not isinstance(item, dict):
                    continue
                if str(item.get("type") or "").strip().lower() != "at":
                    continue
                qq_value = str((item.get("data") or {}).get("qq") or "").strip()
                if qq_value in self._bot_target_ids(event):
                    return True
        return False

    def resolve_identity(self, *, user_id: int, group_id: int = 0) -> tuple[str, str]:
        user_text = str(user_id or "")
        if group_id:
            shared_group_id = f"qq_group_shared_{group_id}"
            return shared_group_id, shared_group_id
        if self.master_qq and user_text == self.master_qq:
            return "master", "master"
        return f"qq_pri_{user_id}", f"qq_{user_id}"

    def resolve_sender_label(self, *, event: dict[str, Any], user_id: int) -> str:
        sender = event.get("sender") if isinstance(event.get("sender"), dict) else {}
        nickname = str(sender.get("card") or sender.get("nickname") or "").strip()
        if nickname:
            return nickname
        return f"QQ {user_id}" if user_id else "群成员"

    def build_extra_context(
        self,
        *,
        event: dict[str, Any],
        is_group: bool,
        user_id: int,
        group_id: int,
        sender_label: str = "",
    ) -> str:
        sender_label = str(sender_label or self.resolve_sender_label(event=event, user_id=user_id)).strip()
        lines = [
            "【QQ 客户端上下文】",
            f"本轮来自：{'QQ群聊' if is_group else 'QQ私聊'}",
            f"发送者 QQ：{user_id or 'unknown'}",
        ]
        if sender_label:
            lines.append(f"发送者标识：{sender_label}")
        if is_group:
            lines.append(f"群号：{group_id or 'unknown'}")
            lines.append("群聊消息会带有【昵称】标记；这是说话人标记，不是用户正文。")
        lines.append("这是纯文字客户端；不需要切换场景、BGM 或立绘。")
        return "\n".join(lines)

    def render_reply_text(self, frame: dict[str, Any]) -> str:
        return "\n".join(self.render_reply_messages(frame)).strip()

    def render_reply_messages(self, frame: dict[str, Any]) -> list[str]:
        messages: list[str] = []
        segments = frame.get("speech_segments")
        if isinstance(segments, list):
            for item in segments:
                text = str(item or "").strip()
                if text:
                    messages.append(text[:1800].strip())
                if len(messages) >= 3:
                    break

        if not messages:
            speech = str(frame.get("speech") or "").replace("\r\n", "\n").replace("\r", "\n").strip()
            inferred = [line.strip() for line in speech.split("\n") if line.strip()]
            if 1 < len(inferred) <= 3:
                messages = [line[:1800].strip() for line in inferred]
            elif speech:
                messages = [speech[:1800].strip()]

        code_snippet = str(frame.get("code_snippet") or "").strip()
        if code_snippet:
            if messages:
                messages[-1] = f"{messages[-1]}\n\n{code_snippet}".strip()[:1800].strip()
            else:
                messages.append(code_snippet[:1800].strip())
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
            results.append(self.send_reply(context, message))
        return {
            "ok": all(bool(result.get("ok")) for result in results),
            "count": len(results),
            "results": results,
        }

    def send_generated_files(self, context: QQMessageContext, tool_events: list[dict[str, Any]] | None) -> dict[str, Any]:
        events = [event for event in tool_events or [] if isinstance(event, dict)]
        targets: list[dict[str, Any]] = []
        for event in events:
            event_type = str(event.get("type") or "")
            if event_type not in {"generated_file_ready", "file_ready"}:
                continue
            if not bool(event.get("send_to_user")):
                continue
            if event_type == "file_ready":
                file_ref = event.get("file") if isinstance(event.get("file"), dict) else {}
                path = str(file_ref.get("absolute_path") or "").strip()
                name = str(file_ref.get("name") or file_ref.get("title") or Path(path).name).strip()
                if path:
                    targets.append(
                        {
                            "generated_id": str(file_ref.get("generated_id") or "").strip(),
                            "source_id": str(file_ref.get("source_id") or "").strip(),
                            "source_type": str(file_ref.get("source_type") or "").strip(),
                            "path": path,
                            "name": name or Path(path).name,
                        }
                    )
                continue

            generated = event.get("generated_file") if isinstance(event.get("generated_file"), dict) else {}
            path = str(generated.get("absolute_path") or "").strip()
            generated_id = str(generated.get("generated_id") or "").strip()
            title = str(generated.get("output_title") or generated.get("generated_handle") or "akane_output").strip()
            ext = str(generated.get("file_ext") or generated.get("output_format") or "").strip().lstrip(".")
            if path and generated_id:
                targets.append(
                    {
                        "generated_id": generated_id,
                        "source_id": generated_id,
                        "source_type": "generated",
                        "path": path,
                        "name": f"{title}.{ext}" if ext and not title.lower().endswith(f".{ext.lower()}") else title,
                    }
                )
        if not targets:
            return {"ok": True, "count": 0, "results": []}

        results: list[dict[str, Any]] = []
        for target in targets:
            result = self.send_file(
                context,
                file_path=str(target.get("path") or ""),
                name=str(target.get("name") or ""),
            )
            result["generated_id"] = str(target.get("generated_id") or "")
            results.append(result)
        return {
            "ok": all(bool(result.get("ok")) for result in results),
            "count": len(results),
            "results": results,
        }

    def send_reply(self, context: QQMessageContext, message: str) -> dict[str, Any]:
        clean_message = str(message or "").strip()
        if not context.target_id or not clean_message:
            return {"ok": False, "reason": "empty_target_or_message"}

        action = "send_group_msg" if context.is_group else "send_private_msg"
        payload = (
            {"group_id": context.target_id, "message": clean_message}
            if context.is_group
            else {"user_id": context.target_id, "message": clean_message}
        )
        try:
            response = requests.post(f"{self.onebot_http_url}/{action}", json=payload, timeout=8)
            response.raise_for_status()
            data = response.json()
            return {"ok": True, "action": action, "data": data}
        except Exception as exc:
            return {"ok": False, "action": action, "reason": str(exc)}

    def send_file(self, context: QQMessageContext, *, file_path: str, name: str = "") -> dict[str, Any]:
        clean_path = str(file_path or "").strip()
        if not context.target_id or not clean_path:
            return {"ok": False, "reason": "empty_target_or_file"}

        action = "upload_group_file" if context.is_group else "upload_private_file"
        payload = (
            {"group_id": context.target_id, "file": clean_path, "name": name or Path(clean_path).name}
            if context.is_group
            else {"user_id": context.target_id, "file": clean_path, "name": name or Path(clean_path).name}
        )
        try:
            response = requests.post(f"{self.onebot_http_url}/{action}", json=payload, timeout=20)
            response.raise_for_status()
            data = response.json()
            return {"ok": True, "action": action, "data": data}
        except Exception as exc:
            return {"ok": False, "action": action, "reason": str(exc)}

    def register_attachment_debounce(
        self,
        context: QQMessageContext,
        *,
        attachment_ids: list[str],
    ) -> dict[str, Any]:
        """Merge bursty QQ attachment events into one final LLM turn.

        Every event still registers its attachments immediately. The debounce
        only decides which HTTP callback is allowed to wake Akane, so older
        image-only events do not cause "I haven't seen it yet" replies.
        """
        normalized_ids = [str(item or "").strip() for item in attachment_ids or [] if str(item or "").strip()]
        delay_seconds = min(5.0, max(0.0, float(getattr(config, "QQ_ATTACHMENT_DEBOUNCE_SECONDS", 1.2) or 0.0)))
        if not normalized_ids or delay_seconds <= 0:
            return {
                "enabled": False,
                "process": True,
                "delay_seconds": 0.0,
                "attachment_ids": normalized_ids,
            }

        now_ts = time.time()
        key = self._attachment_debounce_key(context)
        with self._attachment_debounce_lock:
            self._prune_attachment_debounce_locked(now_ts=now_ts)
            state = self.attachment_debounce_state.get(key)
            generation = int((state or {}).get("generation") or 0) + 1
            merged_ids = list((state or {}).get("attachment_ids") or [])
            for item_id in normalized_ids:
                if item_id not in merged_ids:
                    merged_ids.append(item_id)
            self.attachment_debounce_state[key] = {
                "generation": generation,
                "attachment_ids": merged_ids,
                "updated_at": now_ts,
                "expires_at": now_ts + max(10.0, delay_seconds + 5.0),
            }
        return {
            "enabled": True,
            "process": True,
            "key": key,
            "generation": generation,
            "delay_seconds": delay_seconds,
            "attachment_ids": merged_ids,
        }

    def consume_attachment_debounce(self, token: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(token, dict) or not bool(token.get("enabled")):
            return {
                "process": True,
                "reason": "disabled",
                "attachment_ids": list(token.get("attachment_ids") or []) if isinstance(token, dict) else [],
            }

        key = str(token.get("key") or "").strip()
        generation = int(token.get("generation") or 0)
        with self._attachment_debounce_lock:
            state = self.attachment_debounce_state.get(key)
            if not state:
                return {"process": False, "reason": "debounce_state_missing", "attachment_ids": []}
            if int(state.get("generation") or 0) != generation:
                return {
                    "process": False,
                    "reason": "superseded_by_newer_attachment_event",
                    "attachment_ids": list(state.get("attachment_ids") or []),
                }
            self.attachment_debounce_state.pop(key, None)
            return {
                "process": True,
                "reason": "latest_attachment_event",
                "attachment_ids": list(state.get("attachment_ids") or []),
            }

    def _attachment_debounce_key(self, context: QQMessageContext) -> str:
        # Group memory is shared, but burst merging should stay tied to the
        # sender who is currently feeding Akane attachments.
        return f"{context.session_id or context.target_id}:{context.user_id or 0}"

    def _prune_attachment_debounce_locked(self, *, now_ts: float) -> None:
        stale_keys = [
            key
            for key, state in self.attachment_debounce_state.items()
            if float(state.get("expires_at") or 0.0) <= now_ts
        ]
        for key in stale_keys:
            self.attachment_debounce_state.pop(key, None)

    def _bot_target_ids(self, event: dict[str, Any]) -> set[str]:
        ids: set[str] = set()
        for candidate in (self.bot_qq, event.get("self_id")):
            text = str(candidate or "").strip()
            if text and text != "0":
                ids.add(text)
        return ids

    def _is_group_plaintext_allowed(self, *, session_id: str, user_id: int) -> bool:
        return bool(getattr(config, "QQ_GROUP_PLAINTEXT_ENABLED", False))

    def _is_group_attachment_buffer_allowed(
        self,
        *,
        session_id: str,
        user_id: int,
        attachments: list[dict[str, Any]] | None,
    ) -> bool:
        if not attachments:
            return False
        state = self._get_group_follow_state(session_id)
        return bool(state and int(state.get("user_id") or 0) == int(user_id or 0))

    def _get_group_follow_state(self, session_id: str) -> dict[str, Any] | None:
        state = self.group_follow_state.get(session_id)
        if not isinstance(state, dict):
            return None
        if float(state.get("expires_at") or 0.0) <= time.time():
            self.group_follow_state.pop(session_id, None)
            return None
        return state

    def _arm_group_attachment_buffer(self, *, session_id: str, user_id: int, reason: str) -> None:
        ttl = max(
            20,
            int(
                getattr(
                    config,
                    "QQ_GROUP_ATTACHMENT_BUFFER_TTL_SECONDS",
                    getattr(config, "QQ_GROUP_FOLLOW_TTL_SECONDS", 180),
                )
                or 180
            ),
        )
        self.group_follow_state[session_id] = {
            "user_id": int(user_id or 0),
            "expires_at": time.time() + ttl,
            "reason": reason,
        }

    def _arm_group_follow(self, *, session_id: str, user_id: int, reason: str) -> None:
        self._arm_group_attachment_buffer(session_id=session_id, user_id=user_id, reason=reason)

    def _is_duplicate_event(self, event: dict[str, Any], *, ttl_seconds: float = 300.0) -> bool:
        now_ts = time.time()
        stale_keys = [key for key, seen_at in self.recent_event_fingerprints.items() if now_ts - seen_at > ttl_seconds]
        for key in stale_keys:
            self.recent_event_fingerprints.pop(key, None)

        fingerprints = self._event_fingerprints(event)
        if not fingerprints:
            return False
        if any(
            (seen_at := self.recent_event_fingerprints.get(fingerprint)) is not None and now_ts - seen_at <= ttl_seconds
            for fingerprint in fingerprints
        ):
            for fingerprint in fingerprints:
                self.recent_event_fingerprints[fingerprint] = now_ts
            return True
        for fingerprint in fingerprints:
            self.recent_event_fingerprints[fingerprint] = now_ts
        return False

    def _event_fingerprint(self, event: dict[str, Any]) -> str:
        fingerprints = self._event_fingerprints(event)
        return fingerprints[0] if fingerprints else ""

    def _event_fingerprints(self, event: dict[str, Any]) -> list[str]:
        fingerprints: list[str] = []
        message_type = str(event.get("message_type") or "").strip()
        user_id = str(event.get("user_id") or "").strip()
        group_id = str(event.get("group_id") or "").strip()
        self_id = str(event.get("self_id") or "").strip()

        message_id = str(event.get("message_id") or "").strip()
        if message_id:
            fingerprints.append(f"id:{message_id}")
            fingerprints.append(f"peer:{self_id}|{message_type}|{group_id}|{user_id}|{message_id}")
        for key in ("real_id", "message_seq", "msg_id"):
            value = str(event.get(key) or "").strip()
            if value:
                fingerprints.append(f"{key}:{self_id}|{message_type}|{group_id}|{user_id}|{value}")

        timestamp = str(event.get("time") or "").strip()
        raw_message = self.extract_message_text(event)[:200]
        if user_id or group_id or raw_message:
            fingerprints.append(f"fallback:{self_id}|{message_type}|{user_id}|{group_id}|{timestamp}|{raw_message}")

        seen: set[str] = set()
        unique: list[str] = []
        for fingerprint in fingerprints:
            if fingerprint and fingerprint not in seen:
                seen.add(fingerprint)
                unique.append(fingerprint)
        return unique

    @staticmethod
    def _safe_int(value: Any) -> int:
        try:
            return int(value or 0)
        except Exception:
            return 0
