"""Tool environment, capability catalog and per-round prompt sections."""
from __future__ import annotations

import json
from typing import Any
from ..capability_registry import CapabilitySelection, resolve_capability_disclosures
from ..client_protocol import ClientMode, ClientProtocolContext
from .. import tool_orchestration_engine


MEDIA_PRESET_ROUTING = [
    "【媒体任务预设路由】",
    "- 只要原文件不处理 → send_file，不要转写/转码/净化",
    "",
    "生成与交付分别确认：以工具实际返回的交付状态为准，已发送的结果不要重复发送；仅登记的成果可按用户要求调用 send_file 精确交付，多个结果可一次批量发送。",
    "涉及大小、码率、分辨率、时长、格式兼容等具体约束时，先读取当前规格，再根据当前实际可用工具决定处理方式。",
]


class ToolPromptContextMixin:
    def _build_tool_round_extra_context(
        self,
        *,
        turn_extra_user_context: str,
        tool_followups: list[str],
        allow_more: bool,
        stop_reason: str = "",
        round_control_feedback: str = "",
    ) -> str:
        context = str(turn_extra_user_context or "").strip()
        if any(str(item or "").strip() for item in tool_followups) or stop_reason:
            context = self._merge_extra_user_context(
                context,
                self._build_multi_tool_followup_context(
                    tool_followups,
                    allow_more=allow_more,
                    stop_reason=stop_reason,
                ),
            )
        return self._merge_extra_user_context(
            context,
            str(round_control_feedback or "").strip(),
        )

    def _build_multi_tool_followup_context(
        self,
        tool_followups: list[str],
        *,
        allow_more: bool,
        stop_reason: str = "",
    ) -> str:
        return tool_orchestration_engine.build_multi_tool_followup_context(
            tool_followups,
            allow_more=allow_more,
            stop_reason=stop_reason,
        )

    @staticmethod
    def _build_execution_host_context_sections(
        handler: Any,
        *,
        profile_user_id: str = "",
        session_id: str = "",
        client_mode: str = "",
        actor_stable_id: str = "",
        actor_profile_user_id: str = "",
        execution_scope: Any = None,
    ) -> dict[str, str]:
        """Separate low-frequency host facts from current execution coordinates."""

        empty = {"execution_stable_context": "", "execution_context": ""}

        provider = getattr(handler, "execution_provider", None) if handler is not None else None
        describe = getattr(provider, "prompt_environment", None)
        if not callable(describe):
            describe = getattr(provider, "model_environment", None)
        if not callable(describe):
            return empty
        try:
            environment = dict(describe() or {})
        except Exception:
            return empty
        platform = str(environment.get("platform") or "host")
        command_shell = str(environment.get("command_shell") or "host-default")
        script_shell = str(environment.get("preferred_script_shell") or command_shell)
        host_access = environment.get("host_access") if isinstance(environment.get("host_access"), dict) else {}
        filesystem = str(host_access.get("filesystem") or "host_user_permissions")
        absolute_cwd = str(host_access.get("absolute_cwd") or "supported")
        dependency_storage = (
            environment.get("dependency_storage")
            if isinstance(environment.get("dependency_storage"), dict)
            else {}
        )
        runtime = str(dependency_storage.get("runtime") or "host_path")
        raw_credential_refs = (
            host_access.get("credential_env_refs")
            if isinstance(host_access.get("credential_env_refs"), dict)
            else {}
        )
        credential_refs = ", ".join(
            f"{name}={str(raw_credential_refs[name] or 'missing')}"
            for name in sorted(str(item) for item in raw_credential_refs)
        )
        cwd_state: dict[str, Any] = {}
        describe_cwd = getattr(handler, "working_directory_context", None)
        if execution_scope is not None:
            cwd_state = {"working_directory": execution_scope.working_directory, "project": "task"}
        elif callable(describe_cwd) and profile_user_id and session_id:
            try:
                cwd_state = dict(
                    describe_cwd(
                        profile_user_id=profile_user_id,
                        session_id=session_id,
                        client_mode=client_mode,
                        actor_stable_id=actor_stable_id,
                        actor_profile_user_id=actor_profile_user_id,
                    )
                    or {}
                )
            except Exception:
                cwd_state = {}
        working_directory = str(cwd_state.get("working_directory") or "execution_root").strip()
        project = str(cwd_state.get("project") or "none").strip()
        workspace_id = str(cwd_state.get("workspace_id") or "").strip()
        stable_context = (
            "Execution environment:\n"
            f"platform: {platform}\n"
            f"shell: {command_shell}\n"
            f"access: filesystem={filesystem}; absolute_cwd={absolute_cwd}; runtime={runtime}; script_shell={script_shell}\n"
            "exec_run/status/cancel 操作执行宿主，不依赖桌宠窗口在线；桌面工具操作绑定设备，两者可能不同。"
            "停止自己启动的命令用 exec_cancel(run_id)；其他进程先查再操作，不要把桌面设备的 PID 直接用于宿主命令。\n"
            "专用工具不可用时可在授权范围内用宿主命令处理。工作目录以当前执行坐标为准；普通环境与 PATH 继承宿主（凭据和内部变量除外），依赖遵循项目清单与锁文件。"
            "credential_env_refs 仅列引用状态，configured 可按当前 Shell 语法使用；真实值由执行器注入并从输出中遮蔽。"
        )
        current_context = (
            f"working_directory: {json.dumps(working_directory, ensure_ascii=False)}\n"
            f"project: {json.dumps(project, ensure_ascii=False)}\n"
            f"workspace_id: {workspace_id or 'none'}"
        )
        if credential_refs:
            current_context += f"\ncredential_env_refs: {credential_refs}"
        return {"execution_stable_context": stable_context, "execution_context": current_context}

    @staticmethod
    def _build_execution_host_context(handler: Any, **kwargs: Any) -> str:
        """Composed host context for diagnostics; production uses both sections."""

        sections = ToolPromptContextMixin._build_execution_host_context_sections(handler, **kwargs)
        return "\n\n".join(part for part in sections.values() if part)

    def _build_tool_prompt_context_sections(
        self,
        *,
        allow_tool_call: bool,
        client_context: ClientProtocolContext | None = None,
        profile_user_id: str = "",
        session_id: str = "",
        exclude_tool_types: set[str] | None = None,
        domain_profile_id: str = "",
        capability_selection: CapabilitySelection | None = None,
        include_capability_status: bool = True,
        actor_stable_id: str = "",
        actor_profile_user_id: str = "",
    ) -> dict[str, str]:
        if not allow_tool_call:
            return {
                "execution_stable_context": "",
                "execution_context": "",
                "catalog_context": "",
                "catalog_status": "skipped",
                "round_context": "本轮不要调用任何工具；按当前模式的最终回复协议作答。",
            }

        selection = capability_selection or self._resolve_capability_selection(
            client_context=client_context,
            profile_user_id=profile_user_id,
            session_id=session_id,
            domain_profile_id=domain_profile_id,
        )
        handlers = self._resolve_tool_handlers(
            client_context=client_context,
            profile_user_id=profile_user_id,
            session_id=session_id,
            domain_profile_id=domain_profile_id,
            capability_selection=selection,
        )
        ready_tool_names = tuple(handlers)
        execution_handler = handlers.get("exec_run")
        raw_disclosures = tuple(getattr(selection, "disclosures", ()) or ())
        disclosures = (
            resolve_capability_disclosures(selection, available_tool_names=ready_tool_names)
            if include_capability_status and raw_disclosures
            else ()
        )
        capability_hints = tuple(selection.light_hints) if include_capability_status else ()
        excluded = {str(item).strip() for item in (exclude_tool_types or set()) if str(item).strip()}
        if excluded:
            handlers = {tool_type: handler for tool_type, handler in handlers.items() if str(tool_type) not in excluded}
        if getattr(selection, "capability_catalog", None) is not None:
            # Deferred contracts are disclosed by load results only, never
            # duplicated into the legacy prompt as a second exposure path.
            handlers = {name: handler for name, handler in handlers.items() if name in selection.schema_tool_names}
        execution_host_sections = ToolPromptContextMixin._build_execution_host_context_sections(
            execution_handler,
            profile_user_id=profile_user_id,
            session_id=session_id,
            client_mode=(client_context.effective_mode.value if client_context else ""),
            actor_stable_id=actor_stable_id,
            actor_profile_user_id=actor_profile_user_id,
        )
        catalog_projection = getattr(self, "_build_loadable_capability_catalog_projection", None)
        skill_catalog_entries = None
        if callable(catalog_projection):
            catalog_context, catalog_errors, skill_catalog_entries = catalog_projection(
                ready_tool_names=ready_tool_names, profile_user_id=profile_user_id)
        else:
            catalog_context, catalog_errors = self._build_loadable_capability_catalog(
                ready_tool_names=ready_tool_names, profile_user_id=profile_user_id)
        if getattr(selection, "capability_catalog", None) is not None:
            from ..capability_exposure import directory_text
            catalog_context = "\n\n".join(part for part in (directory_text(selection), catalog_context) if part)
        catalog_status = "failed" if catalog_errors else "ready"
        media_routing: list[str] = []
        if "media_workbench" in selection.module_names:
            media_routing = [*MEDIA_PRESET_ROUTING, ""]

        def append_capability_context(parts: list[str]) -> None:
            ready = [item for item in disclosures if item.state == "ready"]
            latent = [item for item in disclosures if item.state == "latent"]
            unavailable = [item for item in disclosures if item.state == "unavailable"]
            disclosed_summaries = {item.summary for item in disclosures}
            extra_ready_hints = [hint for hint in capability_hints if hint and hint not in disclosed_summaries]
            if ready or extra_ready_hints:
                parts.append("【当前可用能力概览】")
                parts.extend(f"- {item.summary}" for item in ready)
                parts.extend(f"- {hint}" for hint in extra_ready_hints)
                parts.append("")
            elif not disclosures and capability_hints:
                parts.append("【能力概览】")
                parts.extend(f"- {hint}" for hint in capability_hints if hint)
                parts.append("")
            if latent:
                parts.append("【可按需激活的能力】")
                for item in latent:
                    detail = item.summary
                    if item.reason:
                        detail += f" 当前没有展开具体工具，因为：{item.reason}"
                    if item.activation:
                        detail += f" 激活方式：{item.activation}"
                    parts.append(f"- {detail}")
                parts.append("")
            if unavailable:
                parts.append("【暂不可用的能力】")
                for item in unavailable:
                    detail = item.summary
                    if item.reason:
                        detail += f" 当前没有暴露具体工具，因为：{item.reason}"
                    if item.activation:
                        detail += f" 恢复条件：{item.activation}"
                    parts.append(f"- {detail}")
                parts.append("")

        if not handlers:
            if not include_capability_status and excluded:
                return {
                    **execution_host_sections,
                    "catalog_context": catalog_context,
                    "catalog_status": catalog_status,
                    "skill_catalog_entries": skill_catalog_entries,
                    "round_context": "",
                }
            if not disclosures and not capability_hints and not media_routing:
                round_text = "当前没有可用工具；按当前模式的最终回复协议作答。"
                if catalog_errors:
                    round_text = "\n".join([*(f"- {item}" for item in catalog_errors), round_text])
                return {
                    **execution_host_sections,
                    "catalog_context": catalog_context,
                    "catalog_status": catalog_status,
                    "skill_catalog_entries": skill_catalog_entries,
                    "round_context": round_text,
                }
            parts: list[str] = []
            append_capability_context(parts)
            if catalog_errors:
                parts.extend(["【能力目录状态】", *(f"- {item}" for item in catalog_errors), ""])
            parts.extend(media_routing)
            parts.append("当前没有需要展开的具体工具；按当前模式的最终回复协议作答。")
            return {
                **execution_host_sections,
                "catalog_context": catalog_context,
                "catalog_status": catalog_status,
                "skill_catalog_entries": skill_catalog_entries,
                "round_context": "\n".join(parts),
            }

        lines: list[str] = []
        append_capability_context(lines)
        if catalog_errors:
            lines.extend(["【能力目录状态】", *(f"- {item}" for item in catalog_errors), ""])
        if (
            client_context
            and client_context.effective_mode == ClientMode.DESKTOP_PET
            and "send_file" in selection.tool_names
        ):
            lines.extend(
                [
                    "【桌宠文件交付】",
                    "- send_file 在桌宠里表示把已有文件交给手边工作台；如果用户明确说“打开”“显示位置”“放桌面”“复制路径”，"
                    "先完成必要的生成/转换，再对目标 handle 调用 send_file，并加 delivery_action："
                    "open、reveal、save_desktop 或 copy_path。只是让用户拿到文件时，可以不填 delivery_action。",
                    "",
                ]
            )
        lines.extend(media_routing)
        lines.extend(
            [
                "【当前可调用工具（兼容 JSON 通道）】",
                "以下工具没有出现在本轮直接工具入口中；需要时按各自格式写入最终 JSON 的 "
                "tool_call，一次一个。工具结果返回后再判断是否继续。",
            ]
        )
        for handler in handlers.values():
            instruction = str(handler.build_prompt_instruction() or "").strip()
            if instruction:
                lines.append(instruction)
        return {
            **execution_host_sections,
            "catalog_context": catalog_context,
            "catalog_status": catalog_status,
            "skill_catalog_entries": skill_catalog_entries,
            "round_context": "\n".join(lines),
        }

    def _build_tool_prompt_context(
        self,
        **kwargs: Any,
    ) -> str:
        """Compatibility composition for diagnostics and focused unit tests.

        Production prompt assembly consumes the structured sections so runtime
        coordinates, the durable capability catalog and per-round guidance do
        not accidentally acquire one shared lifecycle again.
        """

        section_builder = getattr(self, "_build_tool_prompt_context_sections", None)
        if callable(section_builder):
            sections = section_builder(**kwargs)
        else:
            sections = ToolPromptContextMixin._build_tool_prompt_context_sections(self, **kwargs)
        return "\n\n".join(
            part
            for part in (
                str(sections.get("execution_stable_context") or "").strip(),
                str(sections.get("execution_context") or "").strip(),
                str(sections.get("catalog_context") or "").strip(),
                str(sections.get("round_context") or "").strip(),
            )
            if part
        )

    def _build_native_tool_round_instruction(self, native_tools: list[dict[str, Any]] | None) -> str:
        from .tool_rounds import build_native_tool_round_instruction as _fn

        return _fn(native_tools)
