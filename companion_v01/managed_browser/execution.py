"""Action orchestration, safe recovery, and bounded batches."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from ..browser_page_contract import CONTROL_ACTIONS, managed_capabilities
from .models import BrowserPageResult


class BrowserExecutionMixin:
    def _run_in_browser(
        self,
        *,
        action: str,
        url: str,
        max_chars: int,
        scroll_delta: int,
        element_limit: int,
        selector: str,
        ref: str,
        text: str,
        key: str,
        candidate_index: int,
        screenshot_path: str,
        observation_id: str = "",
        observation_mode: str = "",
        coordinate: tuple[int, int] | list[int] | None = None,
        screenshot_id: str = "",
        download_id: str = "",
        options: dict[str, Any] | None = None,
    ) -> BrowserPageResult:
        if action == "run_actions":
            return self._run_batch(options or {}, observation_id=observation_id, observation_mode=observation_mode)
        safe_to_retry = action in {"navigate", "read_text", "current", "snapshot", "screenshot", "elements", "download_status"}
        for attempt in range(2):
            self._action_state = "not_started"
            try:
                return self._run_in_browser_once(
                    action=action,
                    url=url,
                    max_chars=max_chars,
                    scroll_delta=scroll_delta,
                    element_limit=element_limit,
                    selector=selector,
                    ref=ref,
                    text=text,
                    key=key,
                    candidate_index=candidate_index,
                    screenshot_path=screenshot_path,
                    observation_id=observation_id,
                    observation_mode=observation_mode,
                    coordinate=coordinate,
                    screenshot_id=screenshot_id,
                    download_id=download_id,
                    options=options,
                )
            except Exception as exc:
                status, reason, retryable, next_action = self._classify_browser_error(exc, action=action)
                if attempt == 0 and safe_to_retry and status == "browser_closed" and self._action_state == "not_started":
                    self._discard_browser_objects()
                    continue
                return BrowserPageResult(
                    ok=False,
                    status=status,
                    action=action,
                    reason=reason,
                    retryable=retryable,
                    next_action=next_action,
                    action_state=self._action_state,
                    observation_state="failed",
                )
        return BrowserPageResult(
            ok=False,
            status="unavailable",
            action=action,
            reason="browser_page_runner_failed",
            retryable=True,
            next_action="navigate",
        )

    def _run_batch(self, options: dict[str, Any], *, observation_id: str, observation_mode: str) -> BrowserPageResult:
        steps = options.get("actions", [])
        if not isinstance(steps, list) or not 1 <= len(steps) <= 6:
            return BrowserPageResult(ok=False, status="invalid_request", action="run_actions", reason="invalid_batch")
        receipts = []
        result = None
        for index, step in enumerate(steps):
            stop_event = options.get("stop_event")
            if self._shutdown_started.is_set() or (stop_event is not None and stop_event.is_set()):
                result = BrowserPageResult(ok=False, status="partial", action="run_actions", reason="batch_wait_expired", next_action="snapshot")
                break
            generation = self._document_generation
            page_id = self._page_id
            before = self._observation_store.get(observation_id)
            frame_document = (before.live_state.get("frame", {}).get("document") if before else None)
            result = self._run_in_browser(action=step["action"], url="", max_chars=50000, scroll_delta=800,
                element_limit=20, selector=step.get("selector", ""), ref="", text=step.get("text", ""),
                key=step.get("key", ""), candidate_index=0, screenshot_path="",
                observation_id=observation_id, observation_mode=observation_mode or "text",
                options={k: v for k, v in step.items() if k not in {"action", "selector", "text", "key"}})
            receipts.append({"index": index, "action": step["action"], "ok": result.ok,
                "action_state": result.action_state, "observation_state": result.observation_state,
                "reason": result.reason, "observation_id": result.observation_id})
            observation_id = result.observation_id
            after = self._observation_store.get(observation_id)
            new_frame_document = (after.live_state.get("frame", {}).get("document") if after else None)
            if not result.ok:
                break
            if index < len(steps) - 1 and (result.dialog_events or generation != self._document_generation
                    or page_id != self._page_id or frame_document != new_frame_document
                    or result.observation_state != "complete"):
                result = replace(result, ok=False, status="partial", reason="batch_stopped_for_new_observation", next_action="snapshot")
                break
        state = result.action_state
        if state == "not_started" and any(r["action_state"] == "executed" for r in receipts):
            state = "executed"
        return replace(result, action="run_actions", action_state=state, step_results=tuple(receipts))

    def _run_in_browser_once(
        self,
        *,
        action: str,
        url: str,
        max_chars: int,
        scroll_delta: int,
        element_limit: int,
        selector: str,
        ref: str,
        text: str,
        key: str,
        candidate_index: int,
        screenshot_path: str,
        observation_id: str = "",
        observation_mode: str = "",
        coordinate: tuple[int, int] | list[int] | None = None,
        screenshot_id: str = "",
        download_id: str = "",
        options: dict[str, Any] | None = None,
    ) -> BrowserPageResult:
        options = options or {}
        if action == "capabilities":
            import json
            return BrowserPageResult(ok=True, status="available", action=action, text=json.dumps(managed_capabilities()))
        if action == "download_status":
            return self._download_status(download_id)

        self._dialog_policy = options.get("dialog")
        try:
            page = self._ensure_page()
            self._bring_to_front(page)

            validation_result = self._validate_control_observation(
                page, action=action, observation_id=observation_id,
                coordinate=coordinate, screenshot_id=screenshot_id, options=options,
            )
            if validation_result is not None:
                return validation_result

            action_state = "not_started"
            page_changed = "no"
            dl_before = self._download_tracker.latest()
            self._dialog_events[:] = self._dialog_events[-20:]
            dialog_start = len(self._dialog_events)

            if action in {"navigate", "read_text"} and url:
                self._perform_action(lambda: page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms))
                self._bring_to_front(page)
                self._document_generation += 1
                self._advance_page_revision()
                action_state = "executed"
                page_changed = "yes"
            elif action in {"navigate"} and not url:
                return BrowserPageResult(
                    ok=False,
                    status="invalid_request",
                    action=action,
                    reason="url_required",
                    action_state="not_started",
                )

            current_url = str(getattr(page, "url", "") or "")
            if not current_url or current_url == "about:blank":
                return BrowserPageResult(
                    ok=False,
                    status="no_page",
                    action=action,
                    reason="browser_page_empty",
                    retryable=True,
                    next_action="navigate",
                    action_state=action_state,
                )

            if action == "scroll":
                self._perform_action(lambda: page.mouse.wheel(0, self._safe_scroll_delta(scroll_delta)))
                self._brief_visual_pause(page)
                self._advance_page_revision()
                action_state = "executed"
                page_changed = "yes"
            elif action in CONTROL_ACTIONS:
                url_before = current_url
                page_before = page
                document_generation_before_action = self._document_generation
                control_result = self._run_control_action(
                    page,
                    action=action,
                    selector=selector,
                    ref=ref,
                    text=text,
                    key=key,
                    candidate_index=candidate_index,
                    coordinate=coordinate,
                    screenshot_id=screenshot_id,
                    observation_id=observation_id,
                    options=options,
                )
                if control_result is not None:
                    return control_result
                if self._page is not None:
                    page = self._page
                current_url = str(getattr(page, "url", "") or current_url)
                page_switched = page is not page_before
                if current_url != url_before or page_switched:
                    if self._document_generation == document_generation_before_action:
                        self._document_generation += 1
                    page_changed = "yes"
                else:
                    # A control may have changed focus, an input value, or an
                    # in-page widget without a URL change. Do not report a
                    # page change merely because the interaction revision moved.
                    page_changed = "unknown"
                self._advance_page_revision()
                action_state = "executed"
            elif action == "screenshot":
                target = Path(str(screenshot_path or "")).resolve()
                if not str(screenshot_path or "").strip():
                    return BrowserPageResult(
                        ok=False,
                        status="invalid_request",
                        action=action,
                        reason="screenshot_path_required",
                        action_state="not_started",
                    )
                target.parent.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(target), full_page=False)
                action_state = "executed"
                page_changed = "no"

            # Check if any download was triggered by the action
            dl_after = self._download_tracker.latest()
            download_record = None
            if dl_after is not None and (dl_before is None or dl_after.download_id != dl_before.download_id):
                download_record = dl_after

            return self._observe_page_result(
                page, action=action, current_url=current_url, element_limit=element_limit,
                observation_mode=observation_mode, action_state=action_state,
                page_changed=page_changed, download_record=download_record,
                dialog_start=dialog_start, options=options,
            )
        finally:
            self._dialog_policy = None
