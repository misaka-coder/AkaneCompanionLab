from __future__ import annotations

import hashlib
import json
from pathlib import Path
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

    def __init__(self, *, service: ProjectWorkspaceService, execution_provider: Any | None = None) -> None:
        self.service = service
        self.execution_provider = execution_provider

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
            if reason in {"group_actor_required", "group_actor_profile_required"}:
                feedback += (
                    "当前事件没有可用于跨会话项目目录归属的群成员身份。"
                    "这不限制当前任务的源码操作：project_inspect、workspace_write、workspace_patch 和 exec_run "
                    "省略 workspace_id/cwd 时会共同使用受信任执行根，无需重复调用本工具或要求用户授权。"
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

    def _operation_location(
        self,
        *,
        context: ToolExecutionContext,
        workspace_id: str = "",
        cwd: str = "",
        path: str = "",
        directory_path: bool = False,
    ) -> dict[str, Any]:
        """Resolve project identity or the executor's cwd using one path contract."""

        clean_workspace_id = str(workspace_id or "").strip()
        clean_cwd = str(cwd or "").strip()
        clean_path = str(path or "").strip()
        if clean_workspace_id and clean_cwd:
            raise ProjectWorkspaceError("workspace_and_cwd_conflict")

        path_value = Path(clean_path).expanduser() if clean_path else None
        if path_value is not None and path_value.is_absolute():
            if clean_workspace_id:
                raise ProjectWorkspaceError("workspace_and_absolute_path_conflict")
            if clean_cwd:
                raise ProjectWorkspaceError("cwd_and_absolute_path_conflict")
            if directory_path:
                clean_cwd = str(path_value)
                clean_path = "."
            else:
                clean_cwd = str(path_value.parent)
                clean_path = path_value.name

        if clean_workspace_id:
            return {"workspace_id": clean_workspace_id, "operation_root": None, "path": clean_path}

        provider = self.execution_provider
        if not clean_cwd and context.execution_scope is not None:
            clean_cwd = context.execution_scope.working_directory
        if not clean_cwd:
            try:
                scope = self._scope(context)
                selected = self.service.current(scope=scope)
            except ProjectWorkspaceError as exc:
                # A host-triggered group continuation can legitimately have no
                # member identity.  It has no actor-scoped selection to inherit,
                # so ordinary file operations keep using the execution root.
                if exc.reason not in {"group_actor_required", "group_actor_profile_required"}:
                    raise
                selected = None
            if selected is not None:
                return {
                    "workspace_id": str(selected["workspace_id"]),
                    "operation_root": None,
                    "path": clean_path,
                }
        resolver = getattr(provider, "resolve_workdir", None)
        if not callable(resolver):
            raise ProjectWorkspaceError("execution_path_authority_unavailable")
        if clean_cwd == "alias:project" or clean_cwd.startswith("alias:project/"):
            scope = self._scope(context)
            clean_cwd = self.service.execution_cwd(
                scope=scope,
                alias_value=clean_cwd,
                execution_provider=provider,
            )
        try:
            root = resolver(clean_cwd)
        except Exception as exc:
            raise ProjectWorkspaceError("invalid_execution_cwd", detail=str(exc)) from exc
        return {"workspace_id": "", "operation_root": root, "path": clean_path}

    def _operation_scope(self, *, context: ToolExecutionContext, location: Mapping[str, Any]):
        """Resolve identity only when a registered project is actually used.

        A cwd already authorized by the execution provider is a filesystem
        operation, not project-catalog state.  Requiring a QQ group actor for
        that path made basic inspect/write/patch fail during host-initiated or
        attention continuations even though Shell could use the same cwd.
        """

        if location.get("operation_root") is not None:
            return None
        return self._scope(context)


class ManageProjectWorkspaceToolHandler(_ProjectWorkspaceHandler):
    tool_type = "manage_project_workspace"

    def tool_spec(self):
        return MANAGE_PROJECT_WORKSPACE_TOOL_SPEC

    def build_prompt_instruction(self) -> str:
        return (
            "- manage_project_workspace：设置当前会话的代码目录。create 新建并切换，open 登记已有绝对目录并切换，"
            "select 切到已登记项目，current/list 查看，close 退出当前目录，archive 归档但不删除文件。"
            "create/open/select 后代码工具省略 cwd 时使用该目录；显式 cwd 只覆盖当前一次调用。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict) or str(value.get("type") or "") != self.tool_type:
            return None
        action = str(value.get("action") or "").strip()
        if action not in {"list", "create", "open", "select", "archive", "current", "close"}:
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
        if action == "close":
            return self._execute(
                lambda: {
                    "status": "succeeded",
                    **self.service.close(scope=scope()),
                }
            )
        return self._execute(lambda: self.service.list(scope=scope(), include_archived=bool(call.get("include_archived"))))


class ProjectInspectToolHandler(_ProjectWorkspaceHandler):
    tool_type = "project_inspect"

    def tool_spec(self):
        return PROJECT_INSPECT_TOOL_SPEC

    def build_prompt_instruction(self) -> str:
        return (
            "- project_inspect：只读检查源码。path 可相对 cwd、使用真实绝对路径；省略 cwd 时使用当前项目，没有当前项目时使用执行根；"
            "list 发现相对路径，search 按文本或正则定位到行号，"
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
        if value.get("cwd"):
            normalized["cwd"] = str(value.get("cwd")).strip()
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
                location = self._operation_location(
                    context=context,
                    workspace_id=str(call.get("workspace_id") or ""),
                    cwd=str(call.get("cwd") or ""),
                    path=str(call.get("path") or "."),
                    directory_path=Path(str(call.get("path") or ".")).expanduser().is_absolute(),
                )
                data = self.service.inspect_list(
                    scope=self._operation_scope(context=context, location=location),
                    workspace_id=str(location["workspace_id"]),
                    operation_root=location["operation_root"],
                    path=str(location["path"] or "."),
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
                location = self._operation_location(
                    context=context,
                    workspace_id=str(call.get("workspace_id") or ""),
                    cwd=str(call.get("cwd") or ""),
                    path=str(call.get("path") or "."),
                    directory_path=Path(str(call.get("path") or ".")).expanduser().is_absolute(),
                )
                data = self.service.inspect_search(
                    scope=self._operation_scope(context=context, location=location),
                    workspace_id=str(location["workspace_id"]),
                    operation_root=location["operation_root"],
                    path=str(location["path"] or "."),
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
            location = self._operation_location(
                context=context,
                workspace_id=str(call.get("workspace_id") or ""),
                cwd=str(call.get("cwd") or ""),
                path=str(call.get("path") or ""),
            )
            data = self.service.inspect_read(
                scope=self._operation_scope(context=context, location=location),
                workspace_id=str(location["workspace_id"]),
                operation_root=location["operation_root"],
                path=str(location["path"] or ""),
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
        workspace_id = str(data.get("workspace_id") or "")
        effective_cwd = str(data.get("effective_cwd") or "")
        location_label = f"workspace_id：{workspace_id or 'none'}  effective_cwd：{effective_cwd}"
        lines = [f"【{heading}】", f"{location_label}  路径：{data['path']}"]
        if action == "list":
            lines.append(
                f"遍历：{data.get('visited_entries', 0)} 个条目，最大深度 {data.get('max_depth', 0)}；"
                f"未递归标准缓存/构建目录 {data.get('pruned_directories', 0)} 个"
            )
        if action == "search":
            lines.append(
                f"查询：{data['query']}  文件过滤：{data['include']}  扫描：{data['scanned_files']} 个 UTF-8 文本文件 / {data['scanned_bytes']} 字节；"
                f"跳过二进制 {data.get('skipped_binary', 0)} 个，超大文件 {data.get('skipped_too_large', 0)} 个"
            )
        lines.extend(rows or ["(没有匹配结果)"])
        if not bool(data.get("scan_complete", True)):
            lines.append("扫描触及明确技术边界；以上结果真实有效，但不是整个项目的完整结果。请缩小 path 或 pattern/include 后重试。")
        continuation = None
        if not complete:
            continuation_call = self._bind_direct_cwd(call, data)
            cursor = self._make_cursor(
                call=continuation_call,
                context=context,
                payload={"i": end, "f": fingerprint},
            )
            continuation = self._continuation_call(continuation_call, cursor)
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
                "effective_cwd": effective_cwd,
                "shown": len(rows),
                "shown_through": end,
                "total": len(items),
                "scan_complete": bool(data.get("scan_complete", True)),
                "skipped_binary": int(data.get("skipped_binary", 0)),
                "skipped_too_large": int(data.get("skipped_too_large", 0)),
                "pruned_directories": int(data.get("pruned_directories", 0)),
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
        workspace_id = str(data.get("workspace_id") or "")
        effective_cwd = str(data.get("effective_cwd") or "")
        location_label = f"workspace_id：{workspace_id or 'none'}  effective_cwd：{effective_cwd}"
        lines = [
            "【项目源码读取】",
            f"{location_label}  文件：{data['path']}",
            f"SHA-256：{fingerprint}  文件大小：{data['bytes']} 字节  总行数：{len(lines_source)}",
            *rendered,
        ]
        if not lines_source:
            lines.append("(文件为空)")
        continuation = None
        if not complete:
            continuation_call = self._bind_direct_cwd(call, data)
            cursor = self._make_cursor(
                call=continuation_call,
                context=context,
                payload={"l": line_index, "c": column, "f": fingerprint},
            )
            continuation = self._continuation_call(continuation_call, cursor)
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
                "effective_cwd": effective_cwd,
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
    def _bind_direct_cwd(call: dict[str, Any], data: Mapping[str, Any]) -> dict[str, Any]:
        """Keep a default-root page pinned if project selection changes later."""

        if (
            call.get("cwd")
            or call.get("workspace_id")
            or data.get("workspace_id")
            or not data.get("effective_cwd")
        ):
            return call
        path = Path(str(call.get("path") or "")).expanduser()
        if path.is_absolute():
            return call
        return {**call, "cwd": str(data.get("effective_cwd") or "")}

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
            "- workspace_write：相对 cwd、真实绝对 path 或当前持久项目原子创建/替换一个 UTF-8 文件。"
            "省略 cwd/workspace_id 时使用当前项目，没有当前项目时使用执行根；已有文件优先带 expected_sha256，"
            "冲突时重新读取再修改。长源码优先用本工具；显式 cwd/workspace_id 只覆盖当前调用。"
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
        if value.get("cwd"):
            normalized["cwd"] = str(value.get("cwd")).strip()
        return normalized

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        def operation():
            location = self._operation_location(
                context=context,
                workspace_id=str(call.get("workspace_id") or ""),
                cwd=str(call.get("cwd") or ""),
                path=str(call.get("path") or ""),
            )
            return self.service.write(
                scope=self._operation_scope(context=context, location=location),
                workspace_id=str(location["workspace_id"]),
                operation_root=location["operation_root"],
                path=str(location["path"] or ""),
                content=str(call.get("content") or ""),
                expected_sha256=str(call.get("expected_sha256") or ""),
                mode=str(call.get("mode") or "create_or_replace"),
            )
        return self._execute(operation)


class WorkspacePatchToolHandler(_ProjectWorkspaceHandler):
    tool_type = "workspace_patch"

    def tool_spec(self):
        return WORKSPACE_PATCH_TOOL_SPEC

    def build_prompt_instruction(self) -> str:
        return (
            "- workspace_patch：对 cwd 或当前持久项目中的 UTF-8 文件应用无行数上下文补丁；不必先注册项目。"
            "格式为 `*** Begin Patch` → `*** Update File: 路径`（或 Add/Delete File）→ `@@` → "
            "空格前缀的原文、`-` 删除行、`+` 新增行 → `*** End Patch`；不要填写 unified diff 的行号和行数。"
            "Add File 的每一行以 `+` 开头；Delete File 不附带正文。"
            "支持修改、新建、删除和重命名；所有 hunk 先校验再提交，失败时不会留下半应用结果。"
            "省略 cwd/workspace_id 时使用当前项目，没有当前项目时使用执行根；显式 cwd/workspace_id 只覆盖当前调用。"
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
        if value.get("cwd"):
            normalized["cwd"] = str(value.get("cwd")).strip()
        return normalized

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        def operation():
            location = self._operation_location(
                context=context,
                workspace_id=str(call.get("workspace_id") or ""),
                cwd=str(call.get("cwd") or ""),
            )
            return self.service.patch(
                scope=self._operation_scope(context=context, location=location),
                workspace_id=str(location["workspace_id"]),
                operation_root=location["operation_root"],
                patch_text=str(call.get("patch") or ""),
                expected_files=dict(call.get("expected_files") or {}),
            )
        return self._execute(operation)


__all__ = [
    "ManageProjectWorkspaceToolHandler",
    "ProjectInspectToolHandler",
    "WorkspacePatchToolHandler",
    "WorkspaceWriteToolHandler",
]
