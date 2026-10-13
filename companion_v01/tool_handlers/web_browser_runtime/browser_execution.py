"""Prepare and execute one authorized browser action."""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any, Mapping

from ...browser_page_runtime import BrowserPageResult
from ...browser_page_contract import OPTION_KEYS, managed_capabilities
from ..core import ToolExecutionContext, ToolExecutionResult


class BrowserExecutionMixin:
    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        if call.get("action") == "capabilities":
            from ...personal_browser_contract import ACTIONS
            facts = managed_capabilities() if call.get("session_source") != "personal_chrome" else {
                "backend": "personal_chrome", "actions": [*ACTIONS, "capabilities"],
                "fill_max_chars": 500, "fill_empty": True, "fill_multiline": False,
                "targets": "current observation refs", "coordinates": False, "upload": False,
                "download_transfer": False, "frames": False, "batch": False,
            }
            return ToolExecutionResult(tool_type=self.tool_type, followup_context=json.dumps(facts, ensure_ascii=False))
        if call.get("session_source") == "personal_chrome":
            return self.personal_browser.execute(call=call, context=context)
        cursor = str(call.get("cursor") or "").strip()
        if cursor:
            return self._execute_snapshot_continuation(cursor=cursor, context=context)
        action = str(call.get("action") or "current").strip() or "current"
        if action in self.CONTROL_ACTIONS or call.get("dialog", {}).get("action") == "accept":
            authorization = self._authorize_control_action(action=action, call=call, context=context)
            if not authorization.get("ok"):
                return self._approval_required(action=action, call=call, context=context, authorization=authorization)
        prepared = self._prepare_browser_run(call=call, context=context)
        if isinstance(prepared, ToolExecutionResult):
            return prepared
        run_kwargs, screenshot_output_path = prepared
        normalized = self._run_browser_action(run_kwargs=run_kwargs, context=context)
        open_event_url = str(call.get("url") or "").strip() or str(normalized.url or "").strip()
        open_event = (
            self._build_open_event(open_event_url, normalized.title, client_mode=context.client_mode)
            if call.get("open_for_user") else None
        )
        if not normalized.ok:
            self._discard_browser_screenshot(screenshot_output_path)
            return self._failure(normalized, open_event=open_event)
        return self._finish_browser_result(
            normalized=normalized, context=context, open_event=open_event,
            screenshot_output_path=screenshot_output_path,
            scroll_delta=int(run_kwargs.get("scroll_delta") or 0),
        )

    def _normalize_result(self, value: Any, *, fallback_action: str) -> BrowserPageResult:
        if isinstance(value, BrowserPageResult):
            return value
        if isinstance(value, Mapping):
            action = str(value.get("action") or fallback_action)
            return BrowserPageResult(
                ok=bool(value.get("ok")),
                status=str(value.get("status") or ("available" if value.get("ok") else "unavailable")),
                action=action,
                url=str(value.get("url") or ""),
                title=str(value.get("title") or ""),
                text=str(value.get("text") or ""),
                reason=str(value.get("reason") or ""),
                snapshot_id=str(value.get("snapshot_id") or ""),
                complete=bool(value.get("complete", True)),
                next_cursor=str(value.get("next_cursor") or ""),
                shown_chars=int(value.get("shown_chars") or 0),
                total_chars=int(value.get("total_chars") or 0),
                page_revision=str(value.get("page_revision") or ""),
                retryable=bool(value.get("retryable")),
                next_action=str(value.get("next_action") or ""),
                observation_id=str(value.get("observation_id") or ""),
                screenshot_id=str(value.get("screenshot_id") or ""),
                action_state=str(value.get("action_state") or ("executed" if value.get("ok") and action in self.CONTROL_ACTIONS else "not_started")),
                observation_state=str(value.get("observation_state") or "complete"),
                page_changed=str(value.get("page_changed") or "unknown"),
                visual_status=str(value.get("visual_status") or "not_requested"),
                model_image=value.get("model_image") if isinstance(value.get("model_image"), dict) else None,
                viewport=self._browser_dimensions(value.get("viewport")),
                screenshot_dimensions=self._browser_dimensions(value.get("screenshot_dimensions")),
                download_id=str(value.get("download_id") or ""),
                download_status=str(value.get("download_status") or ""),
                download_filename=str(value.get("download_filename") or ""),
                attachment_handle=str(value.get("attachment_handle") or ""),
                attachment_id=str(value.get("attachment_id") or ""),
                dialog_events=tuple(item for item in (value.get("dialog_events") or ()) if isinstance(item, Mapping)),
                step_results=tuple(dict(item) for item in (value.get("step_results") or ()) if isinstance(item, Mapping)),
            )
        return self._browser_run_failure(fallback_action, "invalid_runner_result")

    @staticmethod
    def _browser_dimensions(value: Any) -> tuple[int, int]:
        if value is None:
            return (0, 0)
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            raise ValueError("invalid_browser_dimensions")
        width, height = (int(item) for item in value)
        if width < 0 or height < 0:
            raise ValueError("invalid_browser_dimensions")
        return width, height

    def _resolve_upload_files(self, handles: list[str], context: ToolExecutionContext) -> tuple[list[str], str]:
        resolver = getattr(self.generated_file_service, "resolve_input_resource", None)
        if not callable(resolver):
            return [], "upload_resource_service_unavailable"
        paths = []
        for handle in handles:
            try:
                item = resolver(profile_user_id=context.profile_user_id, session_id=context.session_id,
                                target=handle, timestamp=context.now_ts)
                if not isinstance(item, dict) or str(item.get("handle", "")).casefold() != handle.casefold():
                    return [], "upload_exact_resource_handle_required"
                path = Path(item.get("absolute_path", ""))
                if not path.is_absolute() or not path.is_file():
                    return [], "upload_resource_file_unavailable"
                paths.append(str(path))
            except Exception:
                return [], "upload_resource_resolution_failed"
        return paths, ""

    def _prepare_browser_run(
        self, *, call: dict[str, Any], context: ToolExecutionContext,
    ) -> tuple[dict[str, Any], Path | None] | ToolExecutionResult:
        action = str(call.get("action") or "current").strip() or "current"
        url = str(call.get("url") or "").strip()
        scroll_delta = self._coerce_int(call.get("scroll_delta"), minimum=-2400, maximum=2400, default=800)
        element_limit = self._coerce_int(
            call.get("element_limit"), minimum=1, maximum=self.MAX_ELEMENT_LIMIT, default=20
        )
        selector = str(call.get("selector") or "").strip()
        ref = str(call.get("ref") or "").strip()
        text = str(call.get("text") or "")
        key = str(call.get("key") or "").strip()
        candidate_index = self._coerce_int(call.get("candidate_index"), minimum=0, maximum=30, default=0)
        observation_id = str(call.get("observation_id") or "").strip()
        raw_obs_mode = str(call.get("observation_mode") or "").strip().lower()
        observation_mode = raw_obs_mode if raw_obs_mode in {"hybrid", "text", "visual"} else ""
        coordinate = call.get("coordinate")
        screenshot_id = str(call.get("screenshot_id") or "").strip()
        download_id = str(call.get("download_id") or "").strip()
        run_kwargs: dict[str, Any] = {
            "action": action,
            "url": url,
            "max_chars": 50_000,
            "observation_id": observation_id,
            "observation_mode": observation_mode,
        }
        options = {k: call[k] for k in OPTION_KEYS if k in call}
        if action == "upload":
            paths, reason = self._resolve_upload_files(call["files"], context)
            if reason:
                return self._failure(BrowserPageResult(ok=False, status="invalid_request", action=action, reason=reason))
            options["upload_paths"] = paths
        if action == "run_actions":
            steps = []
            for step in call["actions"]:
                prepared = dict(step)
                if step["action"] == "upload":
                    paths, reason = self._resolve_upload_files(step["files"], context)
                    if reason:
                        return self._failure(BrowserPageResult(ok=False, status="invalid_request", action=action, reason=reason))
                    prepared["upload_paths"] = paths
                steps.append(prepared)
            options["actions"] = steps
        if options:
            run_kwargs["options"] = options
        if coordinate is not None:
            run_kwargs["coordinate"] = coordinate
        if screenshot_id:
            run_kwargs["screenshot_id"] = screenshot_id
        if download_id:
            run_kwargs["download_id"] = download_id
        screenshot_output_path = None
        if action == "screenshot":
            allocated = self._allocate_browser_screenshot(context)
            if isinstance(allocated, ToolExecutionResult):
                return allocated
            screenshot_output_path = allocated
            run_kwargs["screenshot_path"] = str(allocated)
        if action == "scroll":
            run_kwargs["scroll_delta"] = scroll_delta
        if action == "elements":
            run_kwargs["element_limit"] = element_limit
        if action in self.CONTROL_ACTIONS:
            run_kwargs["selector"] = selector
            run_kwargs["ref"] = ref
            run_kwargs["text"] = text
            run_kwargs["key"] = key
            if action == "click":
                run_kwargs["candidate_index"] = candidate_index
        return run_kwargs, screenshot_output_path

    def _run_browser_action(
        self, *, run_kwargs: dict[str, Any], context: ToolExecutionContext,
    ) -> BrowserPageResult:
        action = str(run_kwargs["action"])
        try:
            runner = self._runner_for_context(context)
        except Exception:
            return self._browser_run_failure(action, "browser_runner_failed")
        runner_fn = getattr(runner, "run", None)
        effective_run_kwargs = dict(run_kwargs)
        if callable(runner_fn):
            try:
                sig = inspect.signature(runner_fn)
                has_var_keyword = any(
                    p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
                )
                if not has_var_keyword:
                    allowed_params = set(sig.parameters.keys())
                    if "options" in run_kwargs and "options" not in allowed_params:
                        return BrowserPageResult(ok=False, status="unavailable", action=action,
                            reason="browser_runner_extensions_unavailable")
                    effective_run_kwargs = {k: v for k, v in run_kwargs.items() if k in allowed_params}
            except (ValueError, TypeError):
                pass
        try:
            result = runner.run(**effective_run_kwargs)
        except Exception:
            return self._browser_run_failure(action, "browser_runner_failed")
        try:
            return self._normalize_result(result, fallback_action=action)
        except (TypeError, ValueError, OverflowError):
            return self._browser_run_failure(action, "invalid_runner_result")

    def _browser_run_failure(self, action: str, reason: str) -> BrowserPageResult:
        return BrowserPageResult(
            ok=False, status="unavailable", action=action, reason=reason,
            action_state="unknown" if action in self.CONTROL_ACTIONS or action in {"navigate", "scroll"} else "not_started",
            observation_state="failed", next_action="current",
        )
