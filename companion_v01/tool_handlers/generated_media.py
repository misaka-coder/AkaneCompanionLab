"""Generated media and file handoff tool handlers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..generated_files_delivery import GeneratedFileReadError

from .core import (
    BaseToolHandler,
    ToolExecutionContext,
    ToolExecutionResult,
    ToolFollowupEnvelope,
    operation_tool_result,
)

def _normalize_file_targets(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw_items = [item.strip() for item in value.replace("，", ",").replace("、", ",").split(",")]
    elif isinstance(value, (list, tuple, set)):
        raw_items = list(value)
    else:
        raw_items = [value]
    sources: list[str] = []
    for item in raw_items:
        text = str(item or "").strip()
        if text and text not in sources:
            sources.append(text[:120])
    return sources[:20]

def _generated_media_capability_status(
    service: Any,
    *,
    getter_name: str,
    missing_reason: str,
) -> dict[str, Any]:
    getter = getattr(service, getter_name, None)
    if not callable(getter):
        return {
            "enabled": False,
            "status": "missing_executor",
            "reason": missing_reason,
        }
    try:
        status = getter()
    except Exception:
        return {
            "enabled": False,
            "status": "unavailable",
            "reason": f"{getter_name}_probe_failed",
        }
    if not isinstance(status, dict):
        return {
            "enabled": False,
            "status": "unavailable",
            "reason": f"{getter_name}_invalid",
        }
    return dict(status)


class InspectMediaInfoToolHandler(BaseToolHandler):
    tool_type = "inspect_media_info"

    def __init__(self, *, generated_file_service) -> None:
        self.generated_file_service = generated_file_service

    def capability_status(self) -> dict[str, Any]:
        return _generated_media_capability_status(
            self.generated_file_service,
            getter_name="media_inspection_status",
            missing_reason="media_inspection_status_missing",
        )

    def build_prompt_instruction(self) -> str:
        return (
            "- inspect_media_info：当用户问音频/视频的时长、编码、采样率、声道、码率、分辨率、帧率、是否有音轨，"
            "或你在转换/压缩/截取前需要先看媒体规格时使用。"
            '格式为 {"type":"inspect_media_info","source_id":"file_001|audio_001|gen_001"}。'
            "这个工具只读取媒体信息，不生成新文件；读取结果会告诉你真实规格。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        if str(value.get("type") or "").strip() != self.tool_type:
            return None
        source_id = str(
            value.get("source_id") or value.get("source") or value.get("target") or value.get("attachment_id") or ""
        ).strip()
        if not source_id:
            return None
        return {
            "type": self.tool_type,
            "source_id": source_id[:120],
        }

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        result = self.generated_file_service.inspect_media_info(
            profile_user_id=context.profile_user_id,
            session_id=context.session_id,
            source_target=str(call.get("source_id") or ""),
            timestamp=context.now_ts,
        )
        media_info = result.get("media_info") if isinstance(result, dict) else None
        events = []
        if isinstance(media_info, dict):
            events.append(
                {
                    "type": "media_info_inspected",
                    "source_id": str(call.get("source_id") or ""),
                    "media_info": media_info,
                }
            )
        return operation_tool_result(
            tool_type=self.tool_type,
            operation_result=result,
            success_events=events,
        )


class SendFileToolHandler(BaseToolHandler):
    tool_type = "send_file"

    def __init__(self, *, generated_file_service, project_workspace_service=None) -> None:
        self.generated_file_service = generated_file_service
        self.project_workspace_service = project_workspace_service

    def build_prompt_instruction(self) -> str:
        return (
            "- send_file：当用户要你发送已有文件时使用，可发送工作台材料 file_001/img_001/audio_001，"
            "也可发送生成物 gen_001/gen_002。"
            '格式为 {"type":"send_file","targets":["file_001","gen_001"]}，单个文件也可以用 target。'
            '只有路径时用 {"type":"send_file","path":"exports/report.md","cwd":"实际项目目录"}，'
            "原样登记后发送，无需先复制到当前工作区或另调登记工具。绝对路径省略 cwd。"
            "路径必须属于已授权目录；相对路径以调用任务/项目目录为准，不猜测插件进程目录。"
            "file_/img_/audio_ 等是用户上传或工作台已有的原始材料，gen_ 是工具生成的结果；"
            "根据用户指代和实际需要选择准确目标，用户只要结果时不要顺带发送原始材料，明确要原件和结果时可以一次选择多个。"
            "适合“把刚才那个视频发我”“把原视频和转写稿都发我”“再发一次 gen_002”。"
            "它只发送已有文件，不修改、不转码、不重新生成；如果用户要求修改内容、换格式或重新整理，应使用对应生成/转换工具。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        if str(value.get("type") or "").strip() != self.tool_type:
            return None
        if "path" in value:
            path, cwd = value["path"], value.get("cwd", "")
            if (not isinstance(path, str) or not path.strip() or not isinstance(cwd, str)
                    or "\x00" in path + cwd
                    or any(value.get(key) is not None for key in ("target", "targets", "generated_id", "file_id"))):
                return None
            return {"type": self.tool_type, "path": path.strip(), "cwd": cwd.strip(),
                    "delivery_action": self._normalize_delivery_action(value.get("delivery_action"))}
        if value.get("cwd"):
            return None
        targets_value = (
            value.get("targets")
            if value.get("targets") is not None
            else value.get("target")
            if value.get("target") is not None
            else value.get("generated_id")
            if value.get("generated_id") is not None
            else value.get("file_id")
        )
        targets = _normalize_file_targets(targets_value)
        return {
            "type": self.tool_type,
            "target": targets[0] if targets else "latest",
            "targets": targets,
            "delivery_action": self._normalize_delivery_action(
                value.get("delivery_action")
                or value.get("desktop_action")
                or value.get("handoff_action")
                or value.get("action")
            ),
        }

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        receipt = None
        if "path" in call:
            from .file_registration import register_file, registration_failure
            try:
                if any(call.get(key) is not None for key in ("target", "targets", "generated_id", "file_id")):
                    from ..project_workspace import ProjectWorkspaceError
                    raise ProjectWorkspaceError("artifact_target_conflict")
                receipt = register_file(service=self.project_workspace_service,
                    generated_files=self.generated_file_service, path=call["path"], cwd=call.get("cwd", ""),
                    context=context, tool_type=self.tool_type)
                call = {**call, "target": receipt["handle"], "targets": [receipt["handle"]]}
            except Exception as exc:
                return registration_failure(tool_type=self.tool_type, error=exc)
        result = self.generated_file_service.send_file(
            profile_user_id=context.profile_user_id,
            session_id=context.session_id,
            target=str(call.get("target") or "latest"),
            targets=list(call.get("targets") or []),
            timestamp=context.now_ts,
        )
        events = []
        files = result.get("files") if isinstance(result, dict) else None
        delivery_action = self._normalize_delivery_action(call.get("delivery_action"))
        allow_desktop_delivery = str(context.client_mode or "").strip() == "desktop_pet"
        if bool(result.get("ok")) and isinstance(files, list):
            for file_ref in files:
                if not isinstance(file_ref, dict):
                    continue
                event = {
                    "type": "file_ready",
                    "file": file_ref,
                    "send_to_user": True,
                    "client_mode": context.client_mode,
                }
                if delivery_action and allow_desktop_delivery:
                    event["delivery_action"] = delivery_action
                    event["desktop_delivery"] = {
                        "action": delivery_action,
                        # M66-D: path removed; use handle + /content route for byte transfer.
                        "name": str(file_ref.get("name") or file_ref.get("title") or ""),
                        "handle": str(file_ref.get("handle") or ""),
                    }
                events.append(event)
        execution = operation_tool_result(
            tool_type=self.tool_type,
            operation_result=result,
            success_events=events,
        )
        if receipt:
            execution.stream_events.insert(0, {"type": "generated_file_ready", "generated_file": receipt})
            execution.state_updates["generated_file_handles"] = [receipt["handle"]]
        return execution

    def _normalize_delivery_action(self, value: Any) -> str:
        text = str(value or "").strip().lower().replace("-", "_")
        if text in {"", "default", "send", "workspace", "hand_off", "handoff"}:
            return ""
        if text in {"open", "open_file", "打开"}:
            return "open"
        if text in {"reveal", "show", "show_in_folder", "show_folder", "folder", "location", "定位", "位置"}:
            return "reveal"
        if text in {"save_desktop", "export_desktop", "desktop", "save_to_desktop", "存桌面", "放桌面"}:
            return "save_desktop"
        if text in {"copy_path", "path", "clipboard", "复制路径"}:
            return "copy_path"
        return ""


class SendStickerToolHandler(BaseToolHandler):
    tool_type = "send_sticker"

    def __init__(self, *, sticker_service) -> None:
        self.sticker_service = sticker_service

    def build_prompt_instruction(self) -> str:
        sticker_list = self.sticker_service.build_prompt_list()
        return (
            "- send_sticker：当你想给用户发送当前可用表情包图片时使用。"
            f"可用表情：{sticker_list or '（当前没有可用表情）'}。"
            '格式为 {"type":"send_sticker","sticker":"biexiao|haoxingfu|tanshou|turan_chuxian|wainao|zaoba|zhuangsha|zhuangsi"}。'
            "它只负责发表情包，不生成文件、不修改附件；适合开心、吐槽、装傻、装死、突然冒泡等轻量情绪回应。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        if str(value.get("type") or "").strip() != self.tool_type:
            return None
        sticker = str(value.get("sticker") or "").strip()
        if not sticker:
            return None
        return {
            "type": self.tool_type,
            "sticker": sticker[:80],
        }

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        target = str(call.get("sticker") or "").strip()
        resolution = self.sticker_service.resolve(target)
        if not resolution.ok or not isinstance(resolution.sticker, dict):
            candidates = list(resolution.candidates or [])
            if candidates:
                candidate_text = "、".join(f"{item.get('id')}({item.get('display_name')})" for item in candidates[:8])
                return ToolExecutionResult(
                    tool_type=self.tool_type,
                    followup_context=(
                        f"你刚刚想发送表情包“{target}”，但匹配到多个候选：{candidate_text}。"
                        "请让用户确认具体要哪一个，或改用准确 sticker id。"
                    ),
                )
            return ToolExecutionResult(
                tool_type=self.tool_type,
                followup_context=(
                    f"你刚刚想发送表情包“{target}”，但没有找到对应资源。"
                    f"当前可用：{self.sticker_service.build_prompt_list() or '无'}。"
                    "请自然告诉用户可以换一个表情名。"
                ),
            )

        sticker = dict(resolution.sticker)
        if not bool(sticker.get("exists")):
            return ToolExecutionResult(
                tool_type=self.tool_type,
                followup_context=(
                    f"你找到了表情包“{sticker.get('display_name') or target}”，"
                    "但本地 PNG 文件不存在，暂时发不出去。请自然告诉用户资源文件缺失。"
                ),
            )

        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=[
                {
                    "type": "sticker_ready",
                    "sticker": {
                        "id": sticker.get("id"),
                        "display_name": sticker.get("display_name"),
                        "absolute_path": sticker.get("absolute_path"),
                        "public_path": sticker.get("public_path"),
                    },
                    "send_to_user": True,
                }
            ],
            followup_context=(
                f"你刚刚已经选择发送表情包“{sticker.get('display_name') or target}”。"
                "请用很短的一句话自然衔接，不要描述文件路径。"
            ),
        )


class InspectGeneratedFileToolHandler(BaseToolHandler):
    tool_type = "inspect_generated_file"

    def __init__(self, *, generated_file_service) -> None:
        self.generated_file_service = generated_file_service

    def build_prompt_instruction(self) -> str:
        return (
            "- inspect_generated_file：当你需要回头查看自己生成过的 gen_001 文件正文、结尾、zip 清单或 manifest 时使用。"
            '格式为 {"type":"inspect_generated_file","target":"gen_001|最近|文件标题",'
            '"section":"content|head|tail|summary|file_list|manifest|file:manifest.json","max_chars":12000}。'
            "它只读取生成物，不会发送、修改或删除文件；适合继续修改前先确认内容、查看转写稿、检查训练集 zip 的 manifest/README。"
            "正文还有未展示部分时结果会带 cursor；如果已展示内容足够可以直接回答，只有确实需要后续正文时才用 inspect_generated_file(cursor=\"...\") 继续。"
            "如果只是要把文件再发给用户，用 send_file；修改内容仅使用当前可用的写入能力。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        if str(value.get("type") or "").strip() != self.tool_type:
            return None
        cursor = str(value.get("cursor") or "").strip()
        if cursor:
            return {"type": self.tool_type, "cursor": cursor}
        target = (
            value.get("target")
            if value.get("target") is not None
            else value.get("generated_id")
            if value.get("generated_id") is not None
            else value.get("file_id")
            if value.get("file_id") is not None
            else "latest"
        )
        section = str(value.get("section") or value.get("part") or value.get("member") or "content").strip()
        max_chars = self._normalize_max_chars(value.get("max_chars") or value.get("limit"))
        return {
            "type": self.tool_type,
            "target": str(target or "latest").strip()[:120] or "latest",
            "section": section[:260] or "content",
            "max_chars": max_chars,
        }

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        cursor = str(call.get("cursor") or "").strip()
        if cursor:
            return self._execute_continuation(cursor=cursor, context=context)
        result = self.generated_file_service.inspect_generated_file(
            profile_user_id=context.profile_user_id,
            session_id=context.session_id,
            target=str(call.get("target") or "latest"),
            section=str(call.get("section") or "content"),
            max_chars=int(call.get("max_chars") or 12000),
        )
        events = []
        if bool(result.get("ok")):
            events.append(
                {
                    "type": "generated_file_inspected",
                    "generated_file": result.get("generated"),
                    "inspection": result.get("inspection"),
                }
            )
        execution = operation_tool_result(
            tool_type=self.tool_type, operation_result=result, success_events=events,
        )
        if bool(result.get("ok")):
            inspection = result.get("inspection") if isinstance(result.get("inspection"), dict) else {}
            truncated = bool(inspection.get("truncated"))
            generated = result.get("generated") if isinstance(result.get("generated"), dict) else {}
            content = str(inspection.get("content") or "")
            diagnostics = {
                "section": str(inspection.get("section") or ""),
                "source_kind": str(inspection.get("source_kind") or ""),
                "truncated": truncated,
                "shown_chars": len(content),
            }
            continuation = None
            extra_note = ""
            if truncated and str(inspection.get("section") or "") == "content" and generated.get("absolute_path"):
                cursor_value = self._issue_content_cursor(
                    generated=generated,
                    offset=len(content),
                    context=context,
                )
                if cursor_value:
                    continuation = {"type": self.tool_type, "cursor": cursor_value}
                    extra_note = (
                        "\n正文还有未展示部分。如果当前内容已经足够，可以直接回答；"
                        f"只有确实需要后续正文时，才调用 inspect_generated_file(cursor=\"{cursor_value}\")。"
                    )
                else:
                    extra_note = (
                        "\n正文还有未展示部分，但当前文件不满足连续读取条件；"
                        "可用 section=head/tail/summary 或其它读取路径查看。"
                    )
            envelope = ToolFollowupEnvelope(
                content=str(execution.followup_context or "").strip() + extra_note,
                producer_bounded=True,
                complete=not truncated,
                continuation=continuation,
                diagnostics=diagnostics,
            )
            execution.followup_context = envelope.content
            execution.followup_envelope = envelope
        return execution

    def _execute_continuation(self, *, cursor: str, context: ToolExecutionContext) -> ToolExecutionResult:
        from ..paged_reading import (
            NEXT_PAGE_BUDGET_CHARS,
            NEXT_PAGE_BUDGET_LINES,
            page_failure_feedback,
            parse_json_payload,
            parse_paged_cursor,
            slice_page,
        )

        owner_binding = self._inspection_owner_binding(context)
        payload = parse_json_payload(parse_paged_cursor(cursor, tool="gf", binding=owner_binding))
        if not isinstance(payload, dict):
            return self._continuation_failure("cursor_invalid", "cursor could not be decoded for this owner/session")
        target = str(payload.get("t") or "").strip()
        section = str(payload.get("s") or "content").strip()
        try:
            offset = max(0, int(payload.get("o") or 0))
        except (TypeError, ValueError):
            return self._continuation_failure("cursor_invalid", "cursor offset is invalid")
        expected_fingerprint = str(payload.get("f") or "")
        if not target:
            return self._continuation_failure("cursor_invalid", "cursor payload has no target")
        result = self.generated_file_service.inspect_generated_file(
            profile_user_id=context.profile_user_id,
            session_id=context.session_id,
            target=target,
            section=section,
            max_chars=40000,
        )
        if not bool(result.get("ok")):
            return self._continuation_failure(
                str((result or {}).get("error") or "generated_file_not_found"),
                "generated file could not be re-read for continuation",
            )
        generated = result.get("generated") if isinstance(result.get("generated"), dict) else {}
        absolute_path = generated.get("absolute_path")
        if not absolute_path:
            return self._continuation_failure("source_missing", "generated file body is missing on disk")
        try:
            resolved_path = Path(str(absolute_path))
            if not resolved_path.exists() or not resolved_path.is_file():
                return self._continuation_failure("source_missing", "generated file body is missing on disk")
            fingerprint = f"{int(resolved_path.stat().st_size)}:{int(resolved_path.stat().st_mtime_ns)}"
        except OSError:
            return self._continuation_failure("source_missing", "generated file body is unreadable")
        if expected_fingerprint and fingerprint != expected_fingerprint:
            return self._continuation_failure("stale_cursor", "generated file changed since the previous page")
        output_format = str(generated.get("output_format") or "").strip()
        service = self.generated_file_service
        # Read past this page's end rather than imposing a fixed extraction
        # window and accidentally reporting its boundary as end-of-file.
        try:
            text = str(
                service._read_generated_text_material(
                    path=resolved_path, output_format=output_format, max_chars=offset + NEXT_PAGE_BUDGET_CHARS,
                )
                or ""
            )
        except GeneratedFileReadError:
            return self._continuation_failure("generated_file_read_failed", "generated file body could not be read")
        if not text:
            return self._continuation_failure("unavailable", "generated file has no paged text material")
        if len(text) <= offset:
            return self._continuation_failure("stale_cursor", "generated file shrank since the previous page")
        page_text, next_offset, total_chars = slice_page(
            text,
            start=offset,
            budget_chars=NEXT_PAGE_BUDGET_CHARS,
            budget_lines=NEXT_PAGE_BUDGET_LINES,
        )
        complete = next_offset >= total_chars
        lines = [
            "【生成文件续读结果】",
            f"目标：{target}（section={section}）",
            page_text,
        ]
        continuation = None
        if not complete:
            next_cursor = self._issue_content_cursor(
                generated=generated,
                offset=next_offset,
                context=context,
            )
            if next_cursor:
                continuation = {"type": self.tool_type, "cursor": next_cursor}
                lines.append(
                    "正文还有未展示部分。如果当前内容已经足够，可以直接回答；"
                    f"只有确实需要后续正文时，才调用 inspect_generated_file(cursor=\"{next_cursor}\")。"
                )
            else:
                lines.append("正文还有未展示部分，但当前文件不满足连续读取条件。")
        else:
            lines.append(f"已读取该 section 的全部可读取正文（共 {total_chars} 字）。")
        content = "\n".join(lines)
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=[
                {
                    "type": "generated_file_inspected",
                    "generated_file": generated,
                    "inspection": {
                        "section": section,
                        "source_kind": output_format,
                        "truncated": not complete,
                        "content": page_text,
                    },
                }
            ],
            followup_context=content,
            followup_envelope=ToolFollowupEnvelope(
                content=content,
                producer_bounded=True,
                complete=complete,
                continuation=continuation,
                diagnostics={
                    "section": section,
                    "shown_chars": len(page_text),
                    ("total_chars" if complete else "readable_chars_at_least"): total_chars,
                    "complete": complete,
                },
            ),
        )

    def _issue_content_cursor(self, *, generated: dict[str, Any], offset: int, context: ToolExecutionContext) -> str:
        from ..paged_reading import cursor_binding, json_payload, make_paged_cursor

        absolute_path = generated.get("absolute_path")
        if not absolute_path:
            return ""
        try:
            resolved_path = Path(str(absolute_path))
            fingerprint = f"{int(resolved_path.stat().st_size)}:{int(resolved_path.stat().st_mtime_ns)}"
        except OSError:
            return ""
        target = str(generated.get("generated_id") or generated.get("generated_handle") or "")
        if not target:
            return ""
        return make_paged_cursor(
            tool="gf",
            binding=self._inspection_owner_binding(context),
            payload=json_payload({"t": target, "s": "content", "o": int(offset), "f": fingerprint}),
        )

    def _inspection_owner_binding(self, context: ToolExecutionContext) -> str:
        from ..paged_reading import cursor_binding

        return cursor_binding("inspect_generated_file", context.profile_user_id, context.session_id)

    def _continuation_failure(self, status: str, reason: str) -> ToolExecutionResult:
        from ..paged_reading import page_failure_feedback

        content = page_failure_feedback(status=status, tool=self.tool_type, detail=reason)
        execution = operation_tool_result(
            tool_type=self.tool_type,
            operation_result={"ok": False, "error": status, "followup_context": content},
        )
        execution.followup_envelope = ToolFollowupEnvelope(
            content=execution.followup_context, producer_bounded=True, complete=True,
            diagnostics={"status": status},
        )
        return execution

    def _normalize_max_chars(self, value: Any) -> int:
        try:
            parsed = int(float(str(value).strip()))
        except Exception:
            parsed = 12000
        return max(500, min(40000, parsed))


class ManageGeneratedFileToolHandler(BaseToolHandler):
    tool_type = "manage_generated_file"

    def __init__(self, *, generated_file_service, project_workspace_service=None) -> None:
        self.generated_file_service = generated_file_service
        self.project_workspace_service = project_workspace_service

    def build_prompt_instruction(self) -> str:
        return (
            '- manage_generated_file(action="register", path="report.md", cwd="可选项目目录")：'
            "把已有项目文件原样登记为 gen_* 产物，返回句柄和 SHA-256；不发送、不重写源文件。\n"
            "- manage_generated_file：当用户要清理、隐藏或删除你生成过的文件时使用，只管理 gen_001 这类生成物。"
            '格式为 {"type":"manage_generated_file","action":"archive|delete|purge",'
            '"targets":["gen_001","gen_002"],"reason":"清理原因"}。'
            "archive 只从生成文件工作台隐藏；delete 会同时删除本地生成文件；purge 会删除本地文件并清空生成物内容卡片。"
            "不要用它清理用户发来的 file_001/img_001，工作台材料应使用 clear_attachment_focus。"
            "用户笼统要求清理整个工作台时，应在同一轮同时调用 clear_attachment_focus(target=all) "
            "和 manage_generated_file(targets=[all])；普通清理用 archive，明确要求彻底删除才用 purge。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        if str(value.get("type") or "").strip() != self.tool_type:
            return None
        action = self._normalize_action(value.get("action") or value.get("operation"))
        if not action:
            return None
        if action == "register":
            path, cwd = value.get("path"), value.get("cwd", "")
            if not isinstance(path, str) or not path.strip() or not isinstance(cwd, str) or "\x00" in path + cwd:
                return None
            return {"type": self.tool_type, "action": action, "path": path.strip(), "cwd": cwd.strip()}
        targets_value = (
            value.get("targets")
            if value.get("targets") is not None
            else value.get("target")
            if value.get("target") is not None
            else value.get("generated_id")
            if value.get("generated_id") is not None
            else value.get("file_id")
        )
        targets = _normalize_file_targets(targets_value)
        return {
            "type": self.tool_type,
            "action": action,
            "targets": targets or ["latest"],
            "reason": str(value.get("reason") or value.get("why") or "").strip()[:200],
        }

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        if call.get("action") == "register":
            return self._register(call, context)
        result = self.generated_file_service.manage_generated_files(
            profile_user_id=context.profile_user_id,
            session_id=context.session_id,
            action=str(call.get("action") or ""),
            targets=list(call.get("targets") or []),
            reason=str(call.get("reason") or ""),
            timestamp=context.now_ts,
        )
        events = []
        managed = [dict(item) for item in list(result.get("managed") or []) if isinstance(item, dict)]
        failures = [dict(item) for item in list(result.get("failures") or []) if isinstance(item, dict)]
        if managed:
            events.append(
                {
                    "type": "generated_files_managed",
                    "action": str(result.get("action") or ""),
                    "managed": managed,
                    "unresolved": list(result.get("unresolved") or []),
                }
            )
        if failures:
            events.append(
                {
                    "type": "generated_files_manage_failed",
                    "action": str(result.get("action") or ""),
                    "failures": failures,
                }
            )
        followup_context = str(result.get("followup_context") or "") if isinstance(result, dict) else ""
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=events,
            followup_context=followup_context,
        )

    def _register(self, call, context):
        import json
        from .file_registration import register_file, registration_failure
        try:
            data = register_file(service=self.project_workspace_service, generated_files=self.generated_file_service,
                path=call["path"], cwd=call.get("cwd", ""), context=context, tool_type=self.tool_type)
            return ToolExecutionResult(tool_type=self.tool_type,
                followup_context=json.dumps(data, ensure_ascii=False),
                stream_events=[{"type": "generated_file_ready", "generated_file": data}],
                state_updates={"generated_file_handles": [data["handle"]]})
        except Exception as exc:
            return registration_failure(tool_type=self.tool_type, error=exc)

    def _normalize_action(self, value: Any) -> str:
        action = str(value or "").strip().lower()
        aliases = {
            "register": "register",
            "hide": "archive",
            "archive": "archive",
            "remove": "archive",
            "clear": "archive",
            "收起": "archive",
            "归档": "archive",
            "隐藏": "archive",
            "delete": "delete",
            "unlink": "delete",
            "删除": "delete",
            "删掉": "delete",
            "purge": "purge",
            "destroy": "purge",
            "彻底删除": "purge",
            "彻底清理": "purge",
        }
        return aliases.get(action, "")
