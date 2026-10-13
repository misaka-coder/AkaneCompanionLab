"""Managed and personal browser argument validation."""

from __future__ import annotations

import re
from typing import Any, Mapping

from ...browser_page_contract import CONTROL_ACTIONS, OPTION_KEYS, option_error
from .common import normalize_http_url


class BrowserInputsMixin:
    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        if str(value.get("type") or "").strip() != self.tool_type:
            return None
        if value.get("action") == "capabilities" and value.get("session_source", "managed") in {"managed", "personal_chrome"}:
            return {"type": self.tool_type, "action": "capabilities", "session_source": value.get("session_source", "managed")}
        if value.get("session_source") == "personal_chrome":
            from ...personal_browser_contract import normalize_personal_call
            normalized = normalize_personal_call(value)
            if normalized and normalized.get("url") and not self._normalize_public_url(normalized["url"]):
                return None
            return normalized
        if value.get("session_source") not in (None, "", "managed"):
            return None
        cursor = str(value.get("cursor") or "").strip()
        if cursor:
            return {"type": self.tool_type, "cursor": cursor}
        action = self._normalize_action(value)
        if not action:
            return None
        if option_error({**value, "action": action}):
            return None
        actions = []
        if action == "run_actions":
            raw_actions = value.get("actions")
            if not isinstance(raw_actions, list) or not 1 <= len(raw_actions) <= 6:
                return None
            from ...browser_page_contract import STEP_SCHEMA
            for step in raw_actions:
                if (not isinstance(step, dict) or step.get("action") not in CONTROL_ACTIONS
                        or set(step) - set(STEP_SCHEMA["properties"]) or not step.get("selector")):
                    return None
                if step.get("frame_selector", "") != value.get("frame_selector", ""):
                    return None
                normalized_step = self.normalize_call({"type": self.tool_type, **step})
                if normalized_step is None:
                    return None
                actions.append({k: normalized_step[k] for k in STEP_SCHEMA["properties"] if k in step})
        url = ""
        raw_url = value.get("url") or value.get("link") or value.get("href")
        if action in {"navigate", "read_text"} and raw_url:
            url = self._normalize_public_url(raw_url)
            if not url:
                return None
        if action == "navigate" and not url:
            return None
        raw_target = value.get("target")
        selector = self._normalize_selector(value.get("selector") or raw_target)
        ref = self._normalize_ref(value.get("ref") or value.get("element_ref") or value.get("target_ref") or raw_target)
        candidate_index = self._normalize_candidate_index(
            value.get("candidate_index")
            or value.get("candidateIndex")
            or value.get("candidate")
            or value.get("index")
        )
        if action != "click":
            candidate_index = 0

        coordinate: tuple[int, int] | None = None
        raw_coord = value.get("coordinate")
        if isinstance(raw_coord, (list, tuple)) and len(raw_coord) == 2:
            try:
                coordinate = (int(raw_coord[0]), int(raw_coord[1]))
            except (ValueError, TypeError):
                pass

        if action in CONTROL_ACTIONS - {"press"} and not selector and not ref and candidate_index <= 0 and coordinate is None:
            return None
        if action != "click" and coordinate is not None:
            return None
        raw_text = next((value[k] for k in ("text", "value", "query") if k in value), None)
        text = self._normalize_fill_text(raw_text)
        if action == "fill" and text is None:
            return None
        key = self._normalize_press_key(value.get("key") or value.get("press"))
        if action == "press" and not key:
            return None
        observation_id = str(value.get("observation_id") or "").strip()[:80]
        raw_obs_mode = str(value.get("observation_mode") or "").strip().lower()
        observation_mode = raw_obs_mode if raw_obs_mode in {"hybrid", "text", "visual"} else ""
        screenshot_id = str(value.get("screenshot_id") or "").strip()[:80]
        download_id = str(value.get("download_id") or "").strip()[:80]
        return {
            "type": self.tool_type,
            "action": action,
            "url": url,
            "open_for_user": self._coerce_bool(value.get("open_for_user") or value.get("openForUser")),
            "scroll_delta": self._coerce_int(
                value.get("scroll_delta") or value.get("delta"), minimum=-2400, maximum=2400, default=800
            ),
            "element_limit": self._coerce_int(
                value.get("element_limit") or value.get("limit"), minimum=1, maximum=self.MAX_ELEMENT_LIMIT, default=20
            ),
            "selector": selector,
            "ref": ref,
            "text": text if text is not None else "",
            "key": key,
            "candidate_index": candidate_index,
            "observation_id": observation_id,
            "observation_mode": observation_mode,
            "coordinate": coordinate,
            "screenshot_id": screenshot_id,
            "download_id": download_id,
            **{k: value[k] for k in OPTION_KEYS if k in value},
            **({"actions": actions} if action == "run_actions" else {}),
        }

    def _normalize_action(self, value: Mapping[str, Any]) -> str:
        raw = str(value.get("action") or "").strip().lower().replace("-", "_").replace(" ", "_")
        if not raw:
            return "navigate" if (value.get("url") or value.get("link") or value.get("href")) else "current"
        aliases = {
            "open": "navigate",
            "go": "navigate",
            "goto": "navigate",
            "visit": "navigate",
            "read": "read_text",
            "read_page": "read_text",
            "extract": "read_text",
            "extract_text": "read_text",
            "list_elements": "elements",
            "inspect_elements": "elements",
            "interactive_elements": "elements",
            "visible_elements": "elements",
            "tap": "click",
            "type": "fill",
            "input": "fill",
            "press_key": "press",
            "status": "current",
            "current_page": "current",
            "info": "current",
            "state": "snapshot",
            "observe": "snapshot",
            "snapshot_page": "snapshot",
            "page_snapshot": "snapshot",
            "accessibility_snapshot": "snapshot",
        }
        action = aliases.get(raw, raw)
        return action if action in self.ALLOWED_ACTIONS else ""

    def _normalize_public_url(self, value: Any) -> str:
        return normalize_http_url(
            value, is_private_host=self._is_private_or_local_host,
            allow_private=self.allow_private_network_urls, reject_secret_query=True,
        )

    def _coerce_bool(self, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        text = str(value or "").strip().lower()
        return text in {"1", "true", "yes", "y", "on", "打开", "是", "需要"}

    def _normalize_selector(self, value: Any) -> str:
        selector = str(value or "").strip()
        if not selector or len(selector) > self.MAX_SELECTOR_CHARS:
            return ""
        if any(ord(ch) < 32 for ch in selector):
            return ""
        return selector

    def _normalize_ref(self, value: Any) -> str:
        ref = str(value or "").strip()
        if not ref:
            return ""
        if ref.startswith("[ref=") and ref.endswith("]"):
            ref = ref[5:-1].strip()
        if ref.startswith("ref="):
            ref = ref[4:].strip()
        return ref if re.fullmatch(r"e\d{1,6}", ref) else ""

    def _normalize_candidate_index(self, value: Any) -> int:
        if value in (None, ""):
            return 0
        try:
            number = int(value)
        except Exception:
            return 0
        return max(0, min(30, number))

    def _normalize_fill_text(self, value: Any) -> str | None:
        # Empty text means clear. Content is data, not an authorization signal.
        if not isinstance(value, str) or len(value) > self.MAX_FILL_TEXT_CHARS or "\x00" in value:
            return None
        return value

    def argument_error(self, value: Mapping[str, Any]) -> str:
        """Explain rejected arguments without echoing submitted content."""
        if value.get("session_source") == "personal_chrome":
            from ...personal_browser_contract import ACTIONS
            if value.get("action") not in ACTIONS:
                return "个人 Chrome 后端尚未支持此动作；用 capabilities 查看当前后端可调用动作。"
            if any(k in value for k in OPTION_KEYS):
                return "个人 Chrome 当前不支持 frame/scope、上传、表单扩展或 dialog 策略参数；用 capabilities 查看差异。"
        issue = option_error(value)
        if issue:
            return issue
        if value.get("action") == "run_actions":
            return "run_actions 需要 1–6 个带 selector 的控件动作，必须使用同一 frame_selector；不接受嵌套批次、坐标或旧 ref。"
        if self._normalize_action(value) == "fill":
            raw = next((value[k] for k in ("text", "value", "query") if k in value), None)
            if not isinstance(raw, str):
                return "fill.text 必须是字符串；空字符串表示清空，不能省略。"
            if len(raw) > self.MAX_FILL_TEXT_CHARS:
                return f"fill.text 超过当前后端上限 {self.MAX_FILL_TEXT_CHARS} 字符；未执行，也未截断输入。"
            if "\x00" in raw:
                return "fill.text 不支持 NUL 字符；未执行。"
            if value.get("session_source") == "personal_chrome" and any(ch in raw for ch in "\r\n\t"):
                return "个人 Chrome 当前不支持 fill 输入换行或 Tab；未执行。"
        return ""

    def _normalize_press_key(self, value: Any) -> str:
        raw = str(value or "Enter").strip()
        aliases = {
            "return": "Enter",
            "esc": "Escape",
            "escape": "Escape",
            "enter": "Enter",
            "tab": "Tab",
            "down": "ArrowDown",
            "up": "ArrowUp",
            "left": "ArrowLeft",
            "right": "ArrowRight",
            "pagedown": "PageDown",
            "pageup": "PageUp",
            "home": "Home",
            "end": "End",
        }
        key = aliases.get(raw.lower().replace(" ", ""), raw)
        return key if key in self.ALLOWED_PRESS_KEYS else ""
