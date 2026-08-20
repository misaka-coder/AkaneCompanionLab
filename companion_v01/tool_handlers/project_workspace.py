from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from ..project_workspace import ProjectWorkspaceError, ProjectWorkspaceService
from ..project_workspace_specs import (
    MANAGE_PROJECT_WORKSPACE_TOOL_SPEC,
    PROJECT_INSPECT_TOOL_SPEC,
    WORKSPACE_PATCH_TOOL_SPEC,
    WORKSPACE_WRITE_TOOL_SPEC,
)
from .core import BaseToolHandler, ToolExecutionContext, ToolExecutionResult, ToolFollowupEnvelope


class _ProjectWorkspaceHandler(BaseToolHandler):
    policy_accepted_native_tool = True

    def __init__(self, *, service: ProjectWorkspaceService) -> None:
        self.service = service

    def _scope(self, context: ToolExecutionContext):
        request_context = context.request_context if isinstance(context.request_context, dict) else {}
        return self.service.scope_for(
            profile_user_id=context.profile_user_id,
            session_id=context.session_id,
            client_mode=context.client_mode,
            actor_stable_id=str(request_context.get("actor_stable_id") or ""),
            actor_profile_user_id=str(request_context.get("actor_profile_user_id") or ""),
        )

    def _result(self, payload: Mapping[str, Any]) -> ToolExecutionResult:
        data = dict(payload)
        status = str(data.get("status") or "failed")
        reason = str(data.get("reason") or "")
        ok = status in {"ok", "succeeded"}
        content = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        if ok:
            feedback = f"项目工作区操作已完成。实际返回数据：{content}"
        else:
            feedback = (
                f"项目工作区操作没有完成（reason={reason or status}）。实际返回数据：{content}。"
                "请依据 reason 调整；不要声称文件或项目已经修改。"
            )
        event = {
            "type": "project_workspace_result",
            "tool_type": self.tool_type,
            "status": "succeeded" if ok else status,
        }
        if reason:
            event["reason"] = reason
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=[event],
            followup_context=feedback,
            followup_envelope=ToolFollowupEnvelope(content=feedback, producer_bounded=True, complete=True),
            state_updates={"project_workspace": data},
        )

    def _execute(self, operation) -> ToolExecutionResult:
        try:
            return self._result(operation())
        except ProjectWorkspaceError as exc:
            return self._result({"status": "rejected", "reason": exc.reason, **exc.details})
        except Exception:
            return self._result({"status": "failed", "reason": "project_workspace_internal_error"})


class ManageProjectWorkspaceToolHandler(_ProjectWorkspaceHandler):
    tool_type = "manage_project_workspace"

    def tool_spec(self):
        return MANAGE_PROJECT_WORKSPACE_TOOL_SPEC

    def build_prompt_instruction(self) -> str:
        return (
            "- manage_project_workspace：管理当前用户跨私聊/群聊共享的持久项目目录。开始多文件/可执行项目时先 current/list；"
            "没有合适项目就 create，继续旧项目时按 workspace_id select；已有宿主目录用 open 注册。"
            "当前选择按会话隔离，选中后 Shell 的 cwd 使用 alias:project。"
            "archive 只归档，不删除项目文件。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict) or str(value.get("type") or "") != self.tool_type:
            return None
        action = str(value.get("action") or "").strip()
        if action not in {"list", "create", "open", "select", "archive", "current"}:
            return None
        normalized = {"type": self.tool_type, "action": action}
        if action == "create":
            name = str(value.get("display_name") or "").strip()
            if not name:
                return None
            normalized["display_name"] = name
        if action == "open":
            path = str(value.get("path") or "").strip()
            if not path:
                return None
            normalized["path"] = path
            display_name = str(value.get("display_name") or "").strip()
            if display_name:
                normalized["display_name"] = display_name
        if action in {"select", "archive"}:
            workspace_id = str(value.get("workspace_id") or "").strip()
            if not workspace_id:
                return None
            normalized["workspace_id"] = workspace_id
        if action == "list":
            normalized["include_archived"] = bool(value.get("include_archived"))
        return normalized

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        scope = lambda: self._scope(context)
        action = str(call.get("action") or "")
        if action == "create":
            return self._execute(lambda: {"status": "succeeded", **self.service.create(scope=scope(), display_name=call["display_name"])})
        if action == "open":
            return self._execute(
                lambda: {
                    "status": "succeeded",
                    **self.service.bind_existing(
                        scope=scope(),
                        host_directory=call["path"],
                        display_name=str(call.get("display_name") or ""),
                    ),
                }
            )
        if action == "select":
            return self._execute(lambda: {"status": "succeeded", **self.service.select(scope=scope(), workspace_id=call["workspace_id"])})
        if action == "archive":
            return self._execute(lambda: {"status": "succeeded", **self.service.archive(scope=scope(), workspace_id=call["workspace_id"])})
        if action == "current":
            return self._execute(
                lambda: {
                    "status": "succeeded",
                    "workspace": self.service.current(scope=scope()),
                }
            )
        return self._execute(lambda: self.service.list(scope=scope(), include_archived=bool(call.get("include_archived"))))


class ProjectInspectToolHandler(_ProjectWorkspaceHandler):
    tool_type = "project_inspect"

    def tool_spec(self):
        return PROJECT_INSPECT_TOOL_SPEC

    def build_prompt_instruction(self) -> str:
        return (
            "- project_inspect：只读检查当前编程项目。list 发现相对路径，search 按文本或正则定位到行号，"
            "read 按行读取 UTF-8 源码并返回 SHA-256。长结果按完整条目或完整代码片段分页；"
            "续读时原样重复 action 与选择参数并带 cursor，源码变化会明确返回 stale_cursor。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict) or str(value.get("type") or "") != self.tool_type:
            return None
        action = str(value.get("action") or "").strip()
        if action not in {"list", "search", "read"}:
            return None
        normalized: dict[str, Any] = {
            "type": self.tool_type,
            "action": action,
            "path": str(value.get("path") or ("" if action == "read" else ".")).strip(),
        }
        if not normalized["path"]:
            return None
        if value.get("workspace_id"):
            normalized["workspace_id"] = str(value.get("workspace_id")).strip()
        if value.get("cursor"):
            normalized["cursor"] = str(value.get("cursor")).strip()
        if action == "list":
            normalized["pattern"] = str(value.get("pattern") or "*").strip() or "*"
            normalized["max_depth"] = self._bounded_int(value.get("max_depth"), default=2, minimum=0, maximum=20)
            normalized["include_hidden"] = bool(value.get("include_hidden", False))
        elif action == "search":
            query = str(value.get("query") or "")
            if not query:
                return None
            normalized.update(
                {
                    "query": query,
                    "include": str(value.get("include") or "*").strip() or "*",
                    "regex": bool(value.get("regex", False)),
                    "case_sensitive": bool(value.get("case_sensitive", True)),
                    "include_hidden": bool(value.get("include_hidden", False)),
                }
            )
        else:
            normalized["start_line"] = self._bounded_int(
                value.get("start_line"), default=1, minimum=1, maximum=2_147_483_647
            )
            normalized["line_count"] = self._bounded_int(
                value.get("line_count"), default=400, minimum=1, maximum=2000
            )
        return normalized

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        try:
            action = str(call.get("action") or "")
            params_hash = self._params_hash(call)
            cursor_state = self._parse_cursor(call=call, context=context, params_hash=params_hash)
            if action == "list":
                data = self.service.inspect_list(
                    scope=self._scope(context),
                    workspace_id=str(call.get("workspace_id") or ""),
                    path=str(call.get("path") or "."),
                    pattern=str(call.get("pattern") or "*"),
                    max_depth=int(call.get("max_depth") or 0),
                    include_hidden=bool(call.get("include_hidden")),
                )
                return self._render_entry_page(
                    action=action,
                    call=call,
                    context=context,
                    data=data,
                    items=list(data["entries"]),
                    start=int(cursor_state.get("i") or 0),
                    expected_fingerprint=str(cursor_state.get("f") or ""),
                    render=self._render_list_entry,
                    heading="项目目录",
                )
            if action == "search":
                data = self.service.inspect_search(
                    scope=self._scope(context),
                    workspace_id=str(call.get("workspace_id") or ""),
                    path=str(call.get("path") or "."),
                    query=str(call.get("query") or ""),
                    include=str(call.get("include") or "*"),
                    regex=bool(call.get("regex")),
                    case_sensitive=bool(call.get("case_sensitive")),
                    include_hidden=bool(call.get("include_hidden")),
                )
                return self._render_entry_page(
                    action=action,
                    call=call,
                    context=context,
                    data=data,
                    items=list(data["matches"]),
                    start=int(cursor_state.get("i") or 0),
                    expected_fingerprint=str(cursor_state.get("f") or ""),
                    render=self._render_search_match,
                    heading="项目搜索",
                )
            data = self.service.inspect_read(
                scope=self._scope(context),
                workspace_id=str(call.get("workspace_id") or ""),
                path=str(call.get("path") or ""),
            )
            return self._render_read_page(
                call=call,
                context=context,
                data=data,
                cursor_state=cursor_state,
            )
        except ProjectWorkspaceError as exc:
            return self._failure(exc.reason, **exc.details)
        except ValueError as exc:
            return self._failure(str(exc) or "cursor_invalid")
        except Exception:
            return self._failure("project_inspect_internal_error")

    def _render_entry_page(
        self,
        *,
        action: str,
        call: dict[str, Any],
        context: ToolExecutionContext,
        data: dict[str, Any],
        items: list[dict[str, Any]],
        start: int,
        expected_fingerprint: str,
        render,
        heading: str,
    ) -> ToolExecutionResult:
        fingerprint = str(data["fingerprint"])
        if expected_fingerprint and expected_fingerprint != fingerprint:
            return self._failure("stale_cursor", detail="project inspection result changed since the previous page")
        if start < 0 or start > len(items):
            return self._failure("cursor_invalid", detail="cursor position is outside the result")
        budget = 32_000
        rows: list[str] = []
        end = start
        used = 0
        for item in items[start:]:
            row = render(item)
            if rows and used + len(row) + 1 > budget:
                break
            rows.append(row)
            used += len(row) + 1
            end += 1
        complete = end >= len(items)
        lines = [f"【{heading}】", f"项目：{data['workspace_id']}  路径：{data['path']}"]
        if action == "search":
            lines.append(
                f"查询：{data['query']}  文件过滤：{data['include']}  扫描：{data['scanned_files']} 文件 / {data['scanned_bytes']} 字节"
            )
        lines.extend(rows or ["(没有匹配结果)"])
        if not bool(data.get("scan_complete", True)):
            lines.append("扫描触及明确技术边界；以上结果真实有效，但不是整个项目的完整结果。请缩小 path 或 pattern/include 后重试。")
        continuation = None
        if not complete:
            cursor = self._make_cursor(
                call=call,
                context=context,
                payload={"i": end, "f": fingerprint},
            )
            continuation = self._continuation_call(call, cursor)
            lines.append(
                f"本页展示完整条目 {start + 1}-{end} / {len(items)}。当前证据够用即可继续；需要后续结果时按 continuation 调用。"
            )
        else:
            lines.append(f"已展示全部 {len(items)} 个结果。")
        return self._success(
            action=action,
            content="\n".join(lines),
            complete=complete,
            continuation=continuation,
            diagnostics={
                "workspace_id": data["workspace_id"],
                "shown": len(rows),
                "shown_through": end,
                "total": len(items),
                "scan_complete": bool(data.get("scan_complete", True)),
            },
        )

    def _render_read_page(
        self,
        *,
        call: dict[str, Any],
        context: ToolExecutionContext,
        data: dict[str, Any],
        cursor_state: dict[str, Any],
    ) -> ToolExecutionResult:
        lines_source = list(data["lines"])
        expected_fingerprint = str(cursor_state.get("f") or "")
        fingerprint = str(data["sha256"])
        if expected_fingerprint and expected_fingerprint != fingerprint:
            return self._failure("stale_cursor", detail="project file changed since the previous page")
        requested_start = int(call.get("start_line") or 1) - 1
        if lines_source and requested_start >= len(lines_source):
            return self._failure(
                "start_line_out_of_range",
                path=data["path"],
                start_line=requested_start + 1,
                total_lines=len(lines_source),
            )
        line_index = int(cursor_state.get("l") if "l" in cursor_state else requested_start)
        column = int(cursor_state.get("c") or 0)
        requested_stop = min(len(lines_source), requested_start + int(call.get("line_count") or 400))
        if line_index < requested_start or line_index > requested_stop or column < 0:
            return self._failure("cursor_invalid", detail="cursor position is outside the requested line range")
        budget = 50_000
        rendered: list[str] = []
        used = 0
        while line_index < requested_stop and len(rendered) < 2000:
            source_line = lines_source[line_index]
            prefix = f"{line_index + 1:>6} | " if column == 0 else f"{line_index + 1:>6}:{column + 1} | "
            available = max(1, budget - used - len(prefix) - 1)
            remaining = source_line[column:]
            if rendered and len(prefix) + len(remaining) + 1 > available:
                break
            chunk = remaining[:available]
            rendered.append(prefix + chunk)
            used += len(prefix) + len(chunk) + 1
            column += len(chunk)
            if column < len(source_line):
                break
            line_index += 1
            column = 0
            if used >= budget:
                break
        complete = line_index >= requested_stop
        lines = [
            "【项目源码读取】",
            f"项目：{data['workspace_id']}  文件：{data['path']}",
            f"SHA-256：{fingerprint}  文件大小：{data['bytes']} 字节  总行数：{len(lines_source)}",
            *rendered,
        ]
        if not lines_source:
            lines.append("(文件为空)")
        continuation = None
        if not complete:
            cursor = self._make_cursor(
                call=call,
                context=context,
                payload={"l": line_index, "c": column, "f": fingerprint},
            )
            continuation = self._continuation_call(call, cursor)
            lines.append("请求范围仍有内容未展示；当前证据够用即可继续，需要后续源码时按 continuation 调用。")
        else:
            if lines_source:
                lines.append(f"已完整展示请求行范围 {requested_start + 1}-{requested_stop}。")
            else:
                lines.append("该文件没有可读取的文本行。")
        return self._success(
            action="read",
            content="\n".join(lines),
            complete=complete,
            continuation=continuation,
            diagnostics={
                "workspace_id": data["workspace_id"],
                "path": data["path"],
                "sha256": fingerprint,
                "start_line": requested_start + 1,
                "end_line": requested_stop,
                "total_lines": len(lines_source),
            },
        )

    def _success(
        self,
        *,
        action: str,
        content: str,
        complete: bool,
        continuation: dict[str, Any] | None,
        diagnostics: dict[str, Any],
    ) -> ToolExecutionResult:
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=[{"type": "project_inspected", "status": "succeeded", "action": action}],
            followup_context=content,
            followup_envelope=ToolFollowupEnvelope(
                content=content,
                producer_bounded=True,
                complete=complete,
                continuation=continuation,
                diagnostics=diagnostics,
            ),
        )

    def _failure(self, reason: str, **details: Any) -> ToolExecutionResult:
        payload = {"status": "rejected", "reason": str(reason or "project_inspect_failed"), **details}
        content = (
            "项目源码检查没有完成。实际返回数据："
            + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            + "。不要声称已经读取未返回的文件或结果；请依据 reason 调整。"
        )
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=[{"type": "project_inspected", **payload}],
            followup_context=content,
            followup_envelope=ToolFollowupEnvelope(
                content=content,
                producer_bounded=True,
                complete=True,
                continuation=None,
                diagnostics=payload,
            ),
        )

    def _make_cursor(
        self,
        *,
        call: dict[str, Any],
        context: ToolExecutionContext,
        payload: dict[str, Any],
    ) -> str:
        from ..paged_reading import cursor_binding, json_payload, make_paged_cursor

        owner = cursor_binding(self.tool_type, context.profile_user_id, context.session_id)
        return make_paged_cursor(
            tool="pi",
            binding=owner,
            payload=json_payload({"p": self._params_hash(call), **payload}),
        )

    def _parse_cursor(
        self,
        *,
        call: dict[str, Any],
        context: ToolExecutionContext,
        params_hash: str,
    ) -> dict[str, Any]:
        from ..paged_reading import cursor_binding, parse_json_payload, parse_paged_cursor

        cursor = str(call.get("cursor") or "").strip()
        if not cursor:
            return {}
        owner = cursor_binding(self.tool_type, context.profile_user_id, context.session_id)
        payload = parse_json_payload(parse_paged_cursor(cursor, tool="pi", binding=owner))
        if not isinstance(payload, dict) or str(payload.get("p") or "") != params_hash:
            raise ValueError("cursor_invalid")
        return payload

    @staticmethod
    def _params_hash(call: dict[str, Any]) -> str:
        params = {key: value for key, value in call.items() if key not in {"type", "cursor"}}
        encoded = json.dumps(params, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _continuation_call(call: dict[str, Any], cursor: str) -> dict[str, Any]:
        return {key: value for key, value in call.items() if key != "cursor"} | {"cursor": cursor}

    @staticmethod
    def _render_list_entry(item: dict[str, Any]) -> str:
        suffix = "/" if str(item.get("kind") or "") == "directory" and not str(item.get("path") or "").endswith("/") else ""
        return f"- {item.get('path', '')}{suffix}  kind={item.get('kind', '')}  bytes={item.get('bytes', 0)}"

    @staticmethod
    def _render_search_match(item: dict[str, Any]) -> str:
        suffix = ""
        if item.get("preview_truncated"):
            suffix = (
                f" [preview columns {item.get('preview_start_column', 0)}-{item.get('preview_end_column', 0)}"
                f" of {item.get('line_chars', 0)}; read this line for full text]"
            )
        return f"- {item.get('path', '')}:{item.get('line', 0)}:{item.get('column', 0)} | {item.get('text', '')}{suffix}"

    @staticmethod
    def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
        try:
            parsed = int(default if value is None else value)
        except (TypeError, ValueError):
            parsed = default
        return max(minimum, min(maximum, parsed))


class WorkspaceWriteToolHandler(_ProjectWorkspaceHandler):
    tool_type = "workspace_write"

    def tool_spec(self):
        return WORKSPACE_WRITE_TOOL_SPEC

    def build_prompt_instruction(self) -> str:
        return (
            "- workspace_write：在当前持久项目内原子创建或替换一个 UTF-8 文件；只传项目相对 path。"
            "已有文件优先带 expected_sha256，冲突时重新读取再修改。源码不要经 Shell 命令传输。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict) or str(value.get("type") or "") != self.tool_type:
            return None
        path = str(value.get("path") or "").strip()
        if not path or "content" not in value:
            return None
        normalized = {
            "type": self.tool_type,
            "path": path,
            "content": str(value.get("content") or ""),
            "mode": str(value.get("mode") or "create_or_replace"),
        }
        for key in ("workspace_id", "expected_sha256"):
            if value.get(key):
                normalized[key] = str(value.get(key)).strip()
        return normalized

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        return self._execute(
            lambda: self.service.write(
                scope=self._scope(context),
                workspace_id=str(call.get("workspace_id") or ""),
                path=str(call.get("path") or ""),
                content=str(call.get("content") or ""),
                expected_sha256=str(call.get("expected_sha256") or ""),
                mode=str(call.get("mode") or "create_or_replace"),
            )
        )


class WorkspacePatchToolHandler(_ProjectWorkspaceHandler):
    tool_type = "workspace_patch"

    def tool_spec(self):
        return WORKSPACE_PATCH_TOOL_SPEC

    def build_prompt_instruction(self) -> str:
        return (
            "- workspace_patch：对当前持久项目中的 UTF-8 文件应用 unified diff。"
            "支持修改、新建、删除和重命名；所有 hunk 先校验再提交，失败时不会留下半应用结果。"
            "可用 expected_files 绑定既有文件的旧 sha256。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict) or str(value.get("type") or "") != self.tool_type:
            return None
        patch_text = str(value.get("patch") or "")
        if not patch_text.strip():
            return None
        expected = value.get("expected_files")
        if expected is not None and not isinstance(expected, dict):
            return None
        normalized = {
            "type": self.tool_type,
            "patch": patch_text,
            "expected_files": {str(key): str(item) for key, item in dict(expected or {}).items()},
        }
        if value.get("workspace_id"):
            normalized["workspace_id"] = str(value.get("workspace_id")).strip()
        return normalized

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        return self._execute(
            lambda: self.service.patch(
                scope=self._scope(context),
                workspace_id=str(call.get("workspace_id") or ""),
                patch_text=str(call.get("patch") or ""),
                expected_files=dict(call.get("expected_files") or {}),
            )
        )


__all__ = [
    "ManageProjectWorkspaceToolHandler",
    "ProjectInspectToolHandler",
    "WorkspacePatchToolHandler",
    "WorkspaceWriteToolHandler",
]
