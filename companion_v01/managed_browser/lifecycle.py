"""Browser readiness, owning thread, page lifecycle, and context events."""
from __future__ import annotations

import importlib.util
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from .models import BrowserPageResult


class BrowserLifecycleMixin:
    def capability_status(self) -> dict[str, Any]:
        cached = self._capability_status_cache
        if cached is not None:
            return dict(cached)
        if not self.is_available():
            status = {
                "enabled": False,
                "status": "missing_executor",
                "reason": "playwright_not_installed",
            }
            self._capability_status_cache = status
            return dict(status)
        # Importability alone is not a usable browser. A Playwright package can
        # exist while its browser binary or Linux shared libraries are absent.
        # Probe one invisible launch once per host process so the capability
        # catalog never advertises a tool that will fail on its first action.
        probe = ThreadPoolExecutor(max_workers=1, thread_name_prefix="akane-browser-probe")
        try:
            # Capability catalogs are built from async HTTP handlers. Running
            # Playwright's sync API on that event-loop thread is rejected even
            # though real browser actions (already worker-threaded) work. Probe
            # in the same kind of dedicated thread used by normal actions.
            status = probe.submit(self._probe_browser_runtime).result(timeout=20)
        except Exception:
            status = {
                "enabled": False,
                "status": "missing_executor",
                "reason": "playwright_browser_unavailable",
            }
        finally:
            probe.shutdown(wait=False, cancel_futures=True)
        self._capability_status_cache = status
        return dict(status)

    def _probe_browser_runtime(self) -> dict[str, Any]:
        browser = None
        playwright = None
        try:
            from playwright.sync_api import sync_playwright

            playwright = sync_playwright().start()
            launch_kwargs: dict[str, Any] = {"headless": True}
            if self.browser_channel:
                try:
                    browser = playwright.chromium.launch(channel=self.browser_channel, **launch_kwargs)
                except Exception:
                    browser = playwright.chromium.launch(**launch_kwargs)
            else:
                browser = playwright.chromium.launch(**launch_kwargs)
            return {"enabled": True, "status": "ready", "reason": ""}
        finally:
            if browser is not None:
                try:
                    browser.close()
                except Exception:
                    pass
            if playwright is not None:
                try:
                    playwright.stop()
                except Exception:
                    pass

    def is_available(self) -> bool:
        return importlib.util.find_spec("playwright") is not None

    def shutdown(self, *, timeout: float = 5.0) -> None:
        with self._lock:
            if self._shutdown_started.is_set():
                return
            self._shutdown_started.set()
            executor = self._executor
        if executor is None:
            self._close_objects()
            return
        try:
            executor.submit(self._close_objects).result(timeout=timeout)
        except Exception:
            # Browser shutdown is best-effort. A slow Edge/Chromium teardown
            # must not turn an already successful browser task into a caller-
            # visible failure or block host shutdown indefinitely.
            pass
        finally:
            # Keep the queued close on the Playwright owner thread even after
            # the bounded wait expires. Repeated shutdown must never move it
            # to the caller thread or cancel the only pending cleanup.
            executor.shutdown(wait=False, cancel_futures=False)

    def _submit(self, task: Callable[[], BrowserPageResult], *, timeout_seconds: float | None = None) -> BrowserPageResult:
        with self._lock:
            if self._shutdown_started.is_set():
                raise RuntimeError("browser_runner_shutdown")
            if self._executor is None:
                self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="akane-browser-page")
            executor = self._executor
        return executor.submit(task).result(timeout=timeout_seconds or max(8, self.timeout_ms / 1000 + 5))

    def _ensure_page(self) -> Any:
        if self._page is not None:
            try:
                if not self._page.is_closed():
                    return self._page
            except Exception:
                pass
        browser = self._browser
        if browser is not None:
            try:
                if not browser.is_connected():
                    self._discard_browser_objects()
            except Exception:
                self._discard_browser_objects()
        from playwright.sync_api import sync_playwright

        self._playwright = self._playwright or sync_playwright().start()
        if self._browser is None:
            self._browser = self._launch_browser()
        try:
            if self._context is None:
                self._context = self._browser.new_context(accept_downloads=True)
                self._context.on("download", self._handle_download)
            self._page = self._context.new_page()
            self._setup_page_listeners(self._page)
        except Exception:
            # A visible browser window can be closed by the user while the host
            # still owns Python proxy objects. Drop the complete browser tree;
            # reusing only the old context poisons every later request.
            self._discard_browser_objects()
            self._playwright = self._playwright or sync_playwright().start()
            self._browser = self._launch_browser()
            self._context = self._browser.new_context(accept_downloads=True)
            self._context.on("download", self._handle_download)
            self._page = self._context.new_page()
            self._setup_page_listeners(self._page)
        self._bring_to_front(self._page)
        return self._page

    def _setup_page_listeners(self, page: Any) -> None:
        try:
            page.on("dialog", self._handle_dialog)
        except Exception:
            pass

    def _handle_download(self, download: Any) -> None:
        try:
            self._download_tracker.handle_playwright_download(
                download,
                profile_user_id=self._profile_user_id,
                session_id=self._session_id,
            )
        except Exception:
            pass

    def _handle_dialog(self, dialog: Any) -> None:
        event = {
            "type": str(getattr(dialog, "type", "") or ""),
            "message": str(getattr(dialog, "message", "") or "")[:200],
            "response": "dismiss", "state": "unknown",
        }
        try:
            policy = self._dialog_policy
            self._dialog_policy = None  # Explicit policy applies to one dialog only.
            if policy and policy.get("action") == "accept":
                event["response"] = "accept"
                if event["type"] == "prompt":
                    dialog.accept(prompt_text=policy.get("prompt_text", ""))
                else:
                    dialog.accept()
            else:
                dialog.dismiss()
            event["state"] = "handled"
        except Exception:
            event["reason"] = "dialog_response_failed"
        finally:
            self._dialog_events.append(event)

    def _launch_browser(self) -> Any:
        launch_kwargs: dict[str, Any] = {"headless": self.headless}
        if not self.browser_channel:
            return self._playwright.chromium.launch(**launch_kwargs)
        try:
            return self._playwright.chromium.launch(channel=self.browser_channel, **launch_kwargs)
        except Exception as channel_exc:
            try:
                return self._playwright.chromium.launch(**launch_kwargs)
            except Exception as fallback_exc:
                channel_reason = str(channel_exc).splitlines()[0][:120]
                fallback_reason = str(fallback_exc).splitlines()[0][:120]
                raise RuntimeError(
                    f"browser_launch_failed channel={self.browser_channel}: {channel_reason}; fallback: {fallback_reason}"
                ) from fallback_exc

    def _close_objects(self) -> None:
        for item in (self._page, self._context, self._browser):
            if item is None:
                continue
            try:
                item.close()
            except Exception:
                pass
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._browser_generation += 1
        self._page_id = "page_" + uuid.uuid4().hex[:8]
        if hasattr(self, "_observation_store"):
            self._observation_store.clear()
        if hasattr(self, "_download_tracker"):
            self._download_tracker.cancel_pending_downloads()
        with self._snapshot_lock:
            self._snapshot_cache.clear()

    def _discard_browser_objects(self) -> None:
        """Forget a closed page/context/browser without stopping Playwright."""
        for item in (self._page, self._context, self._browser):
            if item is None:
                continue
            try:
                item.close()
            except Exception:
                pass
        self._page = None
        self._context = None
        self._browser = None
        self._browser_generation += 1
        self._page_id = "page_" + uuid.uuid4().hex[:8]
        if hasattr(self, "_observation_store"):
            self._observation_store.clear()
        if hasattr(self, "_download_tracker"):
            self._download_tracker.cancel_pending_downloads()

    @staticmethod
    def _classify_browser_error(exc: BaseException, *, action: str) -> tuple[str, str, bool, str]:
        raw = str(exc or "").strip()
        lowered = raw.lower()
        reason = (raw.splitlines()[0] if raw else "browser_page_runner_failed")[:200]
        if any(marker in lowered for marker in ("target page, context or browser has been closed", "browser has been closed", "target closed")):
            return "browser_closed", reason, True, "navigate"
        if "timeout" in lowered:
            return "navigation_timeout" if action in {"navigate", "read_text"} else "action_timeout", reason, True, "snapshot"
        if any(marker in lowered for marker in ("no node found", "resolved to 0 elements", "not attached", "detached")):
            return "stale_target", reason, True, "snapshot"
        if any(marker in lowered for marker in ("name_not_resolved", "connection refused", "connection reset", "net::err")):
            return "site_unreachable", reason, True, "navigate"
        return "unavailable", reason, True, "snapshot"

    def _bring_to_front(self, page: Any) -> None:
        try:
            page.bring_to_front()
        except Exception:
            pass

    def _brief_visual_pause(self, page: Any) -> None:
        try:
            page.wait_for_timeout(200)
        except Exception:
            pass

    def _default_browser_channel(self) -> str:
        return "msedge" if sys.platform == "win32" else ""

    def _download_status(self, download_id: str) -> BrowserPageResult:
        action = "download_status"
        # Playwright dispatches context events while its owning thread is
        # making a browser call. Give a pending download callback one
        # short event turn before reading the tracker.
        if self._page is not None:
            try:
                self._page.wait_for_timeout(50)
            except Exception:
                pass
        target_dl_id = str(download_id or "").strip()
        record = self._download_tracker.get(target_dl_id) if target_dl_id else self._download_tracker.latest()
        if record is None:
            return BrowserPageResult(
                ok=False,
                status="not_found",
                action=action,
                reason="download_not_found",
                download_id=target_dl_id,
            )
        status_text = (
            f"下载任务标识: {record.download_id}\n"
            f"建议文件名: {record.suggested_filename}\n"
            f"下载状态: {record.status}\n"
            f"文件大小: {record.file_size} 字节\n"
        )
        if record.status == "completed":
            if record.attachment_handle:
                status_text += f"工作台材料句柄: {record.attachment_handle}\n"
            if record.storage_relpath:
                status_text += f"存储相对路径: {record.storage_relpath}\n"
        elif record.status in {"failed", "cancelled"}:
            status_text += f"失败/取消原因: {record.error_reason}\n"
        return BrowserPageResult(
            ok=record.status in {"completed", "pending"},
            status=record.status,
            action=action,
            text=status_text,
            download_id=record.download_id,
            download_status=record.status,
            download_filename=record.suggested_filename,
            attachment_handle=record.attachment_handle,
            attachment_id=record.attachment_id,
            action_state="executed",
            observation_state="complete",
        )
