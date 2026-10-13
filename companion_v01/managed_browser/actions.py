"""Bound controls, accurate action receipts, and popup adoption."""
from __future__ import annotations

import uuid
from typing import Any, Callable

from ..browser_page_contract import CONTROL_ACTIONS
from ..browser_page_observation import coordinate_pixels_match, read_live_page_state
from .models import BrowserPageResult


class BrowserActionsMixin:
    def _perform_action(self, action: Callable[[], Any]) -> Any:
        # A Playwright timeout can happen after the input was delivered. Only
        # a successful return proves execution; exceptions remain unknown.
        self._action_state = "unknown"
        value = action()
        self._action_state = "executed"
        return value

    def _control_failure(self, exc: Exception, *, action: str, fallback: str) -> BrowserPageResult:
        status, reason, retryable, next_action = self._classify_browser_error(exc, action=action)
        if self._action_state != "not_started":
            self._advance_page_revision()
            next_action = "current"
        return BrowserPageResult(
            ok=False, status=status, action=action, reason=reason or fallback,
            retryable=retryable, next_action=next_action,
            action_state=self._action_state, observation_state="failed",
        )

    def _run_control_action(
        self,
        page: Any,
        *,
        action: str,
        selector: str,
        ref: str,
        text: str,
        key: str,
        candidate_index: int,
        coordinate: tuple[int, int] | list[int] | None = None,
        screenshot_id: str = "",
        observation_id: str = "",
        options: dict[str, Any] | None = None,
    ) -> BrowserPageResult | None:
        safe_selector = str(selector or "").strip()
        safe_ref = str(ref or "").strip()
        safe_candidate_index = self._safe_int(candidate_index, default=0, minimum=0, maximum=30)
        target_selector = f"aria-ref={safe_ref}" if safe_ref else safe_selector

        # 1. Candidate index click strictly bound to frozen observation candidates
        if action == "click" and safe_candidate_index > 0:
            return self._click_observed_candidate(
                page, safe_candidate_index=safe_candidate_index, observation_id=observation_id,
            )

        # 2. Visual coordinate click fallback
        if action == "click" and not target_selector and coordinate is not None:
            return self._click_observed_coordinate(
                page, coordinate=coordinate, screenshot_id=screenshot_id, observation_id=observation_id,
            )

        # 3. Standard selector / ref target validation
        if safe_ref and not observation_id:
            return BrowserPageResult(
                ok=False,
                status="stale_target",
                action=action,
                reason="observation_id_required_for_ref",
                retryable=True,
                next_action="snapshot",
            )
        if action in CONTROL_ACTIONS - {"press"} and not target_selector:
            return BrowserPageResult(ok=False, status="invalid_request", action=action, reason="selector_required")
        try:
            options = options or {}
            scope = self._scope_page(page, options)
            if action == "click":
                self._click_locator_and_switch_to_popup_if_any(page, scope.locator(target_selector))
            elif action == "fill":
                self._perform_action(lambda: scope.locator(target_selector).fill(text, timeout=self.timeout_ms))
            elif action == "hover":
                self._perform_action(lambda: scope.locator(target_selector).hover(timeout=self.timeout_ms))
            elif action == "select_option":
                self._perform_action(lambda: scope.locator(target_selector).select_option(value=options["values"], timeout=self.timeout_ms))
            elif action == "set_checked":
                self._perform_action(lambda: scope.locator(target_selector).set_checked(options["checked"], timeout=self.timeout_ms))
            elif action == "upload":
                self._perform_action(lambda: scope.locator(target_selector).set_input_files(options["upload_paths"], timeout=self.timeout_ms))
            elif action == "press":
                if target_selector:
                    self._perform_action(lambda: scope.locator(target_selector).press(str(key or "Enter"), timeout=self.timeout_ms))
                else:
                    self._perform_action(lambda: page.keyboard.press(str(key or "Enter")))
            else:
                return BrowserPageResult(ok=False, status="invalid_request", action=action, reason="unsupported_control_action")
            try:
                page.wait_for_load_state("domcontentloaded", timeout=1500)
            except Exception:
                pass
            return None
        except Exception as exc:
            return self._control_failure(exc, action=action, fallback="browser_control_action_failed")

    def _click_locator_and_switch_to_popup_if_any(self, page: Any, locator: Any) -> None:
        context = getattr(page, "context", None)
        if context is None:
            self._perform_action(lambda: locator.click(timeout=self.timeout_ms))
            return
        new_page = self._run_action_with_optional_popup(
            page,
            lambda: self._perform_action(lambda: locator.click(timeout=self.timeout_ms)),
        )
        if new_page is None:
            self._page = page
            return
        self._adopt_popup_page(page, new_page)

    def _run_action_with_optional_popup(self, page: Any, action: Callable[[], Any]) -> Any | None:
        """Run one action while associating an event-created popup with it."""
        created_pages: list[Any] = []

        def remember_page(candidate: Any) -> None:
            if candidate not in created_pages:
                created_pages.append(candidate)

        page.on("popup", remember_page)
        try:
            action()
            # Pump immediate popup events; this is not a claim of page stability.
            page.wait_for_timeout(150)
        finally:
            page.remove_listener("popup", remember_page)
        if len(created_pages) > 1:
            raise RuntimeError("multiple_popups_created_by_action")
        return created_pages[0] if created_pages else None

    def _adopt_popup_page(self, source_page: Any, new_page: Any) -> None:
        try:
            new_page.wait_for_load_state("domcontentloaded", timeout=self.timeout_ms)
        except Exception:
            pass
        popup_url = str(getattr(new_page, "url", "") or "").strip()
        if not self._safe_public_candidate_url(popup_url, base_url=""):
            try:
                new_page.close()
            except Exception:
                pass
            self._page = source_page
            raise RuntimeError("popup_url_rejected")
        self._page = new_page
        self._setup_page_listeners(new_page)
        self._document_generation += 1
        self._page_id = "page_" + uuid.uuid4().hex[:8]
        self._bring_to_front(new_page)

    def _validate_control_observation(
        self,
        page: Any,
        *,
        action: str,
        observation_id: str,
        coordinate: tuple[int, int] | list[int] | None,
        screenshot_id: str,
        options: dict[str, Any],
    ) -> BrowserPageResult | None:
        if action in CONTROL_ACTIONS and not observation_id and coordinate is None:
            return BrowserPageResult(
                ok=False, status="stale_target", action=action,
                reason="observation_id_required_for_control", retryable=True, next_action="snapshot",
            )

        bound_observation = self._observation_store.get(observation_id) if observation_id else None
        if coordinate is not None and screenshot_id:
            bound_observation = self._observation_store.get_by_screenshot_id(screenshot_id)
        if (bound_observation is not None and action in CONTROL_ACTIONS
                and bound_observation.browser_generation == self._browser_generation
                and bound_observation.document_generation == self._document_generation
                and bound_observation.page_revision == self._page_revision):
            try:
                live_state = self._scoped_live_state(page, options)
            except Exception:
                live_state = None
            if not bound_observation.live_state or live_state != bound_observation.live_state:
                return BrowserPageResult(
                    ok=False, status="stale_target", action=action,
                    reason="page_changed_since_observation", retryable=True,
                    next_action="snapshot", observation_state="failed",
                )

        # Validate freshness of observation target if caller bound this control action
        if observation_id and action in CONTROL_ACTIONS:
            valid, stale_reason = self._observation_store.validate_action_target(
                observation_id=observation_id,
                current_browser_gen=self._browser_generation,
                current_doc_gen=self._document_generation,
                current_page_id=self._page_id,
                current_page_revision=self._page_revision,
            )
            if not valid:
                return BrowserPageResult(
                    ok=False,
                    status="stale_target",
                    action=action,
                    reason=stale_reason,
                    retryable=True,
                    next_action="snapshot",
                    observation_id=observation_id,
                    action_state="not_started",
                    observation_state="failed",
                    page_changed="no",
                )

        return None

    def _click_observed_candidate(self, page: Any, *, safe_candidate_index: int, observation_id: str) -> BrowserPageResult | None:
        action = "click"
        if not observation_id:
            return BrowserPageResult(
                ok=False,
                status="stale_target",
                action=action,
                reason="observation_id_required_for_candidate_click",
                retryable=True,
                next_action="elements",
            )
        target_obs = self._observation_store.get(observation_id)
        if target_obs is None or not target_obs.element_candidates:
            return BrowserPageResult(
                ok=False,
                status="stale_target",
                action=action,
                reason="elements_observation_required_before_candidate_click",
                retryable=True,
                next_action="elements",
            )
        if (
            target_obs.browser_generation != self._browser_generation
            or target_obs.document_generation != self._document_generation
            or target_obs.page_id != self._page_id
            or target_obs.page_revision != self._page_revision
        ):
            return BrowserPageResult(
                ok=False,
                status="stale_target",
                action=action,
                reason="observation_stale_for_candidate_click",
                retryable=True,
                next_action="elements",
            )
        if safe_candidate_index > len(target_obs.element_candidates):
            return BrowserPageResult(
                ok=False,
                status="stale_target",
                action=action,
                reason=f"candidate_index_{safe_candidate_index}_out_of_range",
                retryable=True,
                next_action="elements",
            )
        candidate = target_obs.element_candidates[safe_candidate_index - 1]
        try:
            source_index = self._safe_int(candidate.get("source_index"), default=-1, minimum=-1, maximum=100_000)
            if source_index < 0:
                raise RuntimeError("candidate_source_missing")
            locator = page.locator("a[href]").nth(source_index)
            if not locator.is_visible():
                raise RuntimeError("candidate_not_visible")
            current_href = str(locator.get_attribute("href") or "").strip()
            current_url = self._safe_public_candidate_url(
                current_href,
                base_url=str(getattr(page, "url", "") or ""),
            )
            expected_url = str(candidate.get("url") or "").strip()
            current_title = " ".join(
                str(
                    locator.get_attribute("title")
                    or locator.get_attribute("aria-label")
                    or locator.inner_text()
                    or ""
                ).split()
            )[:160]
            expected_title = " ".join(str(candidate.get("title") or "").split())[:160]
            if current_url != expected_url or current_title != expected_title:
                raise RuntimeError("candidate_identity_changed")
            self._click_locator_and_switch_to_popup_if_any(page, locator)
            return None
        except Exception as exc:
            return self._control_failure(exc, action=action, fallback="candidate_click_failed")

    def _click_observed_coordinate(self, page: Any, *, coordinate: tuple[int, int] | list[int], screenshot_id: str, observation_id: str) -> BrowserPageResult | None:
        action = "click"
        if not screenshot_id:
            return BrowserPageResult(
                ok=False,
                status="invalid_request",
                action=action,
                reason="screenshot_id_required_for_coordinate_click",
                retryable=True,
                next_action="snapshot",
            )
        valid, reason = self._observation_store.validate_coordinate_target(
            screenshot_id=screenshot_id,
            coordinate=coordinate,
            current_browser_gen=self._browser_generation,
            current_doc_gen=self._document_generation,
            observation_id=observation_id,
            current_page_id=self._page_id,
            current_page_revision=self._page_revision,
            current_viewport=self._safe_viewport_box(page),
        )
        if not valid:
            return BrowserPageResult(
                ok=False,
                status="stale_target",
                action=action,
                reason=reason,
                retryable=True,
                next_action="snapshot",
            )
        try:
            x, y = int(coordinate[0]), int(coordinate[1])
            target_state = page.evaluate("""([x, y]) => {
                    const el = document.elementFromPoint(x, y);
                    return {masked: !!el?.closest('input[type=password], textarea[type=password]')};
                }""", [x, y])
            visual_record = self._observation_store.get_by_screenshot_id(screenshot_id)
            if (target_state.get("masked") or visual_record is None
                    or not coordinate_pixels_match(visual_record, self._safe_observation_screenshot(page), x, y)
                    or read_live_page_state(page) != visual_record.live_state):
                return BrowserPageResult(
                    ok=False, status="stale_target", action=action,
                    reason="coordinate_target_changed_or_masked", next_action="snapshot",
                )
            context = getattr(page, "context", None)
            if context is not None:
                new_page = self._run_action_with_optional_popup(
                    page,
                    lambda: self._perform_action(lambda: page.mouse.click(x, y)),
                )
                if new_page is not None:
                    self._adopt_popup_page(page, new_page)
            else:
                self._perform_action(lambda: page.mouse.click(x, y))
            return None
        except Exception as exc:
            return self._control_failure(exc, action=action, fallback="coordinate_click_failed")
