"""Scoped text/visual evidence and receipts after page actions."""
from __future__ import annotations

from typing import Any

from ..browser_page_contract import CONTROL_ACTIONS
from ..browser_page_download import BrowserDownloadRecord
from ..browser_page_observation import (
    ObservationRecord, generate_observation_id, generate_screenshot_id,
    png_dimensions, read_live_page_state,
)
from .models import BrowserPageResult


class BrowserObservingMixin:
    def _observe_page_result(
        self,
        page: Any,
        *,
        action: str,
        current_url: str,
        element_limit: int,
        observation_mode: str,
        action_state: str,
        page_changed: str,
        download_record: BrowserDownloadRecord | None,
        dialog_start: int,
        options: dict[str, Any],
    ) -> BrowserPageResult:
        clean_obs_mode = str(observation_mode or "").strip().lower()
        if clean_obs_mode in {"hybrid", "text", "visual"}:
            effective_mode = clean_obs_mode
        elif action in CONTROL_ACTIONS | {"navigate", "scroll"}:
            effective_mode = "hybrid"
        else:
            effective_mode = "text"

        try:
            observed_state = self._scoped_live_state(page, options)
            title = self._safe_title(page)
            snapshot_kind = "elements" if action == "elements" else "page"
            element_candidates: list[dict[str, Any]] = []
            if action == "elements":
                captured = self._safe_element_summary(page, element_limit=element_limit)
                element_candidates = self._safe_link_candidate_rows(page, max_items=element_limit)
            elif options.get("frame_selector") or options.get("scope_selector"):
                scope = self._scope_page(page, options)
                locator = scope.locator(options.get("scope_selector") or "body")
                if locator.count() != 1:
                    raise ValueError("scope_selector_must_match_one_element")
                if action == "read_text":
                    captured = locator.inner_text(timeout=self.timeout_ms)
                else:
                    captured = locator.aria_snapshot(timeout=self.timeout_ms)
                captured = ("Observation scope: " + str({k: options[k] for k in ("frame_selector", "scope_selector") if k in options})
                            + "\n" + captured)[:self._snapshot_capture_max_chars]
            else:
                captured = self._safe_page_snapshot(page, max_chars=self._snapshot_capture_max_chars)
            record = self.store_snapshot(kind=snapshot_kind, text=captured, url=current_url, title=title)
            from ..paged_reading import FIRST_PAGE_BUDGET_CHARS, FIRST_PAGE_BUDGET_LINES, slice_page

            page_text, next_offset, total_chars = slice_page(
                captured,
                start=0,
                budget_chars=FIRST_PAGE_BUDGET_CHARS,
                budget_lines=FIRST_PAGE_BUDGET_LINES,
            )
            complete = next_offset >= total_chars

            screenshot_bytes: bytes | None = None
            screenshot_id = ""
            viewport_box = self._safe_viewport_box(page)
            if effective_mode in {"hybrid", "visual"}:
                try:
                    screenshot_bytes = self._safe_observation_screenshot(page)
                    screenshot_id = generate_screenshot_id()
                    visual_status = "available"
                    observation_state = "complete"
                except Exception:
                    visual_status = "visual_unavailable"
                    observation_state = "partial"
            else:
                visual_status = "not_requested"
                observation_state = "complete"

            if self._scoped_live_state(page, options) != observed_state:
                # Preserve text for inspection but do not publish actionable
                # evidence assembled from two different page states.
                observation_state = "unstable"
                screenshot_bytes = None
                screenshot_id = ""
                visual_status = "visual_unavailable" if effective_mode != "text" else "not_requested"
                observed_state = {}

            obs_id = generate_observation_id()
            obs_record = ObservationRecord(
                observation_id=obs_id,
                page_id=self._page_id,
                browser_generation=self._browser_generation,
                document_generation=self._document_generation,
                url=current_url,
                title=title,
                aria_snapshot=captured if snapshot_kind != "elements" else "",
                visible_text=captured,
                screenshot_id=screenshot_id,
                screenshot_bytes=screenshot_bytes,
                viewport=viewport_box,
                screenshot_dimensions=png_dimensions(screenshot_bytes),
                page_revision=self._page_revision,
                action_state=action_state,
                observation_state=observation_state,
                page_changed=page_changed,
                visual_status=visual_status,
                element_candidates=element_candidates,
                live_state=observed_state,
                captured_at=float(self._now()),
            )
            obs_record = self._observation_store.put(obs_record)
            model_image = obs_record.build_model_image_dict() if obs_record.visual_status == "available" else None

            return BrowserPageResult(
                ok=True,
                status="executed" if action in CONTROL_ACTIONS | {"screenshot"} else "available",
                action=action,
                url=current_url,
                title=title,
                text=page_text,
                snapshot_id=str(record.get("snapshot_id") or ""),
                complete=complete,
                next_cursor=(
                    f"{record.get('snapshot_id')}:{next_offset}" if not complete else ""
                ),
                shown_chars=len(page_text),
                total_chars=total_chars,
                page_revision=str(record.get("revision") or ""),
                observation_id=obs_id,
                screenshot_id=obs_record.screenshot_id,
                action_state=action_state,
                observation_state=obs_record.observation_state,
                page_changed=page_changed,
                visual_status=obs_record.visual_status,
                model_image=model_image,
                viewport=viewport_box,
                screenshot_dimensions=obs_record.screenshot_dimensions,
                download_id=download_record.download_id if download_record else "",
                download_status=download_record.status if download_record else "",
                download_filename=download_record.suggested_filename if download_record else "",
                attachment_handle=download_record.attachment_handle if download_record else "",
                attachment_id=download_record.attachment_id if download_record else "",
                dialog_events=tuple(self._dialog_events[dialog_start:]),
            )
        except Exception as obs_exc:
            if action_state == "executed":
                return BrowserPageResult(
                    ok=True,
                    status="executed",
                    action=action,
                    url=current_url,
                    action_state="executed",
                    observation_state="failed",
                    page_changed=page_changed,
                    reason=f"observation_failed_after_action: {obs_exc}",
                    retryable=True,
                    next_action="snapshot",
                    download_id=download_record.download_id if download_record else "",
                    download_status=download_record.status if download_record else "",
                    download_filename=download_record.suggested_filename if download_record else "",
                    attachment_handle=download_record.attachment_handle if download_record else "",
                    attachment_id=download_record.attachment_id if download_record else "",
                    dialog_events=tuple(self._dialog_events[dialog_start:]),
                )
            raise


    def _scope_page(self, page: Any, options: dict[str, Any]) -> Any:
        selector = options.get("frame_selector")
        if not selector:
            return page
        locator = page.locator(selector)
        if locator.count() != 1:
            raise ValueError("frame_selector_must_match_one_iframe")
        element = locator.element_handle(timeout=self.timeout_ms)
        frame = element.content_frame() if element else None
        if frame is None:
            raise ValueError("frame_unavailable")
        return frame

    def _scoped_live_state(self, page: Any, options: dict[str, Any]) -> dict[str, Any]:
        root = read_live_page_state(page)
        if not options.get("frame_selector"):
            return root
        return {"main": root, "frame_selector": options["frame_selector"],
                "frame": read_live_page_state(self._scope_page(page, options))}

    def _safe_observation_screenshot(self, page: Any) -> bytes:
        """Capture a model-only viewport image, masking password controls when supported."""
        try:
            password_controls = page.locator('input[type="password"], textarea[type="password"]')
            return page.screenshot(
                type="png",
                full_page=False,
                timeout=3000,
                mask=[password_controls],
                scale="css",
                caret="initial",
            )
        except TypeError as exc:
            raise RuntimeError("screenshot_masking_unavailable") from exc
