from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from companion_v01.local_capability_config import save_approval_policy_config
from companion_v01.tool_runtime import BrowserPageToolHandler, ToolExecutionContext


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Probe Akane's optional managed browser page runner.",
    )
    parser.add_argument(
        "--url",
        default="https://example.com",
        help="Public http/https URL to navigate and read. Defaults to https://example.com.",
    )
    parser.add_argument(
        "--max-chars",
        type=int,
        default=1600,
        help="Maximum page text chars to request from the tool. Defaults to 1600.",
    )
    parser.add_argument(
        "--action",
        choices=("navigate", "read_text", "current", "snapshot", "scroll", "elements", "click", "fill", "press"),
        default="navigate",
        help="Browser page action to probe.",
    )
    parser.add_argument(
        "--scroll-delta",
        type=int,
        default=800,
        help="Scroll delta for --action scroll. Defaults to 800.",
    )
    parser.add_argument(
        "--element-limit",
        type=int,
        default=20,
        help="Maximum visible element summaries for --action elements. Defaults to 20.",
    )
    parser.add_argument(
        "--selector",
        default="",
        help="CSS selector for --action click/fill/press.",
    )
    parser.add_argument(
        "--ref",
        default="",
        help="Accessibility snapshot ref for --action click/fill/press, for example e12.",
    )
    parser.add_argument(
        "--candidate-index",
        type=int,
        default=0,
        help="Visible link/video candidate index for --action click, from the snapshot candidate list.",
    )
    parser.add_argument(
        "--text",
        default="Akane",
        help="Text for --action fill. Defaults to Akane.",
    )
    parser.add_argument(
        "--key",
        default="Enter",
        help="Key for --action press. Defaults to Enter.",
    )
    parser.add_argument(
        "--trusted-auto-allow",
        action="store_true",
        help="Temporarily enable trusted_auto_allow for control action probing.",
    )
    parser.add_argument(
        "--require-ready",
        action="store_true",
        help="Return a non-zero exit code when Playwright/browser execution is unavailable.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    temp_policy_dir: tempfile.TemporaryDirectory[str] | None = None
    handler_kwargs: dict[str, Any] = {}
    if args.trusted_auto_allow:
        temp_policy_dir = tempfile.TemporaryDirectory()
        save_approval_policy_config(
            base_dir=temp_policy_dir.name,
            profile_user_id="probe",
            payload={"defaultMode": "trusted_auto_allow"},
        )
        handler_kwargs["config_base_dir"] = temp_policy_dir.name
    handler = BrowserPageToolHandler(**handler_kwargs)
    call_payload: dict[str, Any] = {
        "type": "browser_page",
        "action": args.action,
        "max_chars": args.max_chars,
    }
    if args.action == "scroll":
        call_payload["scroll_delta"] = args.scroll_delta
    if args.action == "elements":
        call_payload["element_limit"] = args.element_limit
    if args.action in {"click", "fill", "press"}:
        call_payload["selector"] = args.selector
        call_payload["ref"] = args.ref
    if args.action == "click" and args.candidate_index:
        call_payload["candidate_index"] = args.candidate_index
    if args.action == "fill":
        call_payload["text"] = args.text
    if args.action == "press":
        call_payload["key"] = args.key
    if args.action in {"navigate", "read_text"} and args.url:
        call_payload["url"] = args.url

    if args.action in {"current", "snapshot", "scroll", "elements", "click", "fill", "press"} and args.url:
        warmup_call = handler.normalize_call(
            {
                "type": "browser_page",
                "action": "navigate",
                "url": args.url,
                "max_chars": 500,
            }
        )
        if warmup_call is not None:
            handler.execute(
                call=warmup_call,
                context=ToolExecutionContext(
                    profile_user_id="probe",
                    session_id="browser-page-probe",
                    now_ts=0,
                    visual_payload={},
                    client_mode="desktop_pet",
                ),
            )

    status = handler.capability_status()
    call = handler.normalize_call(call_payload)
    if call is None:
        print(
            json.dumps(
                {
                    "ok": False,
                    "status": "invalid_request",
                    "capabilityStatus": status,
                    "reason": "browser_page_call_rejected",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        if temp_policy_dir is not None:
            temp_policy_dir.cleanup()
        return 2

    result = handler.execute(
        call=call,
        context=ToolExecutionContext(
            profile_user_id="probe",
            session_id="browser-page-probe",
            now_ts=0,
            visual_payload={},
            client_mode="desktop_pet",
        ),
    )
    event = next((item for item in result.stream_events if isinstance(item, dict)), {})
    event_status = str(event.get("status") or "")
    payload = {
        "ok": event_status not in {"unavailable", "approval_required"} and event.get("type") != "capability_approval_required",
        "status": event_status or result.state_updates.get("browser_page_status") or result.state_updates.get("browser_control_status") or "unknown",
        "capabilityStatus": status,
        "action": event.get("action") or call.get("action"),
        "url": event.get("url") or result.state_updates.get("browser_page_url") or "",
        "title": event.get("title") or result.state_updates.get("browser_page_title") or "",
        "reason": event.get("reason") or result.state_updates.get("browser_page_reason") or "",
        "followupChars": len(result.followup_context or ""),
        "textObserved": any(marker in (result.followup_context or "") for marker in ("页面状态快照：", "正文摘录：", "元素摘要：")),
        "elementCount": event.get("element_count") or result.state_updates.get("browser_page_element_count") or 0,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if temp_policy_dir is not None:
        temp_policy_dir.cleanup()
    if args.require_ready and not payload["ok"]:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
