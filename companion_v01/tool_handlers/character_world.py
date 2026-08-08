"""Character-world tool handlers: character context, NPC, reminders, inventory, gift, artifact, persona."""

from __future__ import annotations

import re
import time
from typing import Any

from ..capability_registry import (
    CALL_NPC_TOOL_SPEC,
    CANCEL_REMINDER_TOOL_SPEC,
    CHECK_INVENTORY_TOOL_SPEC,
    LIST_REMINDERS_TOOL_SPEC,
    LOAD_CHARACTER_CONTEXT_TOOL_SPEC,
    SET_REMINDER_TOOL_SPEC,
)
from ..npc_runtime import GenericNPCRuntime
from ..store import MemoryStore
from ..text_utils import (
    normalize_text,
    resolve_reminder_due_timestamp,
    timestamp_to_datetime_label,
)
from .core import (
    BaseToolHandler,
    ToolExecutionContext,
    ToolExecutionResult,
)

class LoadCharacterContextToolHandler(BaseToolHandler):
    tool_type = "load_character_context"

    def __init__(self, *, context_library_service: Any) -> None:
        self.context_library_service = context_library_service

    def tool_spec(self):  # M66-C
        return LOAD_CHARACTER_CONTEXT_TOOL_SPEC

    def build_prompt_instruction(self) -> str:
        # The active character pack renders its exact libraries and targets.
        return ""

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        if str(value.get("type") or "").strip() != self.tool_type:
            return None

        raw_targets = value.get("targets")
        if raw_targets is None:
            raw_targets = value.get("files")
        if isinstance(raw_targets, str):
            candidates = [part.strip() for part in re.split(r"[,，;；、\n]+", raw_targets) if part.strip()]
        elif isinstance(raw_targets, (list, tuple, set)):
            candidates = [str(item or "").strip() for item in raw_targets]
        else:
            candidates = []

        targets: list[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            target = normalize_text(candidate).strip()
            if not target or target in seen:
                continue
            seen.add(target)
            targets.append(target)
        if not targets:
            return None
        return {"type": self.tool_type, "targets": targets}

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        service = self.context_library_service
        if service is None:
            return ToolExecutionResult(
                tool_type=self.tool_type,
                followup_context=(
                    "【角色资料读取结果】\n"
                    "status=unavailable\nreason=context_library_service_unavailable\n"
                    "请不要猜测缺失的角色设定，基于当前已经可见的信息自然回应。"
                ),
                state_updates={
                    "character_context": {
                        "status": "unavailable",
                        "loaded": [],
                        "failed": list(call.get("targets") or []),
                    }
                },
            )

        result = service.load_context(
            str(context.character_pack_id or ""),
            list(call.get("targets") or []),
        )
        loaded_targets = [
            str(item.get("target") or "")
            for item in result.get("loaded") or []
            if isinstance(item, dict) and str(item.get("target") or "")
        ]
        failed_targets = [
            {
                "target": str(item.get("target") or ""),
                "status": str(item.get("status") or "unavailable"),
                "reason": str(item.get("reason") or ""),
            }
            for item in result.get("failed") or []
            if isinstance(item, dict)
        ]
        return ToolExecutionResult(
            tool_type=self.tool_type,
            followup_context=str(result.get("followup_context") or ""),
            state_updates={
                "character_context": {
                    "status": str(result.get("status") or "unavailable"),
                    "loaded": loaded_targets,
                    "failed": failed_targets,
                }
            },
        )


class CallNPCToolHandler(BaseToolHandler):
    tool_type = "call_npc"

    def __init__(self, *, npc_runtime: GenericNPCRuntime, describe_scene, build_followup_context) -> None:
        self.npc_runtime = npc_runtime
        self.describe_scene = describe_scene
        self.build_followup_context = build_followup_context

    def tool_spec(self):  # M66-C
        return CALL_NPC_TOOL_SPEC

    def build_prompt_instruction(self) -> str:
        return (
            "- call_npc：当场景里需要路人、店员、摊主之类的临时 NPC 先回答一句时使用。"
            '格式为 {"type":"call_npc","npc_name":"名字","npc_role":"身份","query":"要问的话"}。'
            "如果你输出 call_npc，speech 应该是你在 NPC 回答前先说出的那句台词。"
            "这句可以先回应用户、再顺势问 NPC，也可以直接转头问 NPC，不必固定写成“我帮你问问”。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None

        call_type = str(value.get("type") or "").strip()
        if call_type != self.tool_type:
            return None

        query = str(value.get("query") or value.get("question") or value.get("prompt") or "").strip()
        if not query:
            return None

        npc_name = str(value.get("npc_name") or value.get("name") or "路人").strip() or "路人"
        npc_role = str(value.get("npc_role") or value.get("role") or "通用NPC").strip() or "通用NPC"
        return {
            "type": self.tool_type,
            "npc_name": npc_name[:24],
            "npc_role": npc_role[:40],
            "query": query[:120],
        }

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        scene_context = self.describe_scene(context.visual_payload)
        npc_turn = self.npc_runtime.reply(
            profile_user_id=context.profile_user_id,
            npc_name=str(call["npc_name"]),
            npc_role=str(call["npc_role"]),
            query=str(call["query"]),
            scene_context=scene_context,
            now_ts=int(context.now_ts),
        )
        return ToolExecutionResult(
            tool_type=self.tool_type,
            raw_turns=[npc_turn],
            stream_events=[
                {
                    "type": "npc_turn",
                    "speaker": str(npc_turn.get("speaker") or "NPC"),
                    "speech": str(npc_turn.get("speech") or ""),
                }
            ],
            followup_context=self.build_followup_context(npc_turn),
        )


class SetReminderToolHandler(BaseToolHandler):
    tool_type = "set_reminder"

    def __init__(self, *, store: MemoryStore) -> None:
        self.store = store

    def tool_spec(self):  # M66-C
        return SET_REMINDER_TOOL_SPEC

    def build_prompt_instruction(self) -> str:
        return (
            "- set_reminder：当用户明确要求你稍后提醒、明天提醒、今晚提醒某件事时使用。"
            '格式为 {"type":"set_reminder","content":"提醒内容","time_text":"原始时间说法","offset_minutes":5,"date_label":"YYYY-MM-DD","time_of_day":"morning|afternoon|night|midnight","hour":20,"minute":0}。'
            "其中 content 要写真正要提醒的事，time_text 保留用户原本的时间说法。"
            "如果是“5分钟后”“半小时后”“2小时后”这种相对时间，优先填写 offset_minutes。"
            "如果时间还太模糊，先直接追问，不要调用这个工具。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None

        call_type = str(value.get("type") or "").strip()
        if call_type != self.tool_type:
            return None

        content = str(
            value.get("content") or value.get("task") or value.get("reminder") or value.get("text") or ""
        ).strip()
        if not content:
            return None

        normalized: dict[str, Any] = {
            "type": self.tool_type,
            "content": content[:120],
            "time_text": str(value.get("time_text") or value.get("time") or value.get("when") or "").strip()[:60],
            "date_label": str(value.get("date_label") or "").strip()[:10],
            "time_of_day": str(value.get("time_of_day") or "").strip()[:16],
            "hour": self._coerce_int(value.get("hour")),
            "minute": self._coerce_int(value.get("minute")),
            "offset_minutes": self._coerce_int(
                value.get("offset_minutes") or value.get("delay_minutes") or value.get("minutes_later")
            ),
        }
        return normalized

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        due_ts = resolve_reminder_due_timestamp(
            now_ts=context.now_ts,
            time_text=str(call.get("time_text") or ""),
            date_label=str(call.get("date_label") or "") or None,
            time_of_day=str(call.get("time_of_day") or "") or None,
            hour=call.get("hour"),
            minute=call.get("minute"),
            offset_minutes=call.get("offset_minutes"),
        )
        content = str(call.get("content") or "").strip()
        raw_time_text = str(call.get("time_text") or "").strip()

        if due_ts is None:
            return ToolExecutionResult(
                tool_type=self.tool_type,
                followup_context=(
                    "你刚刚尝试帮用户设置提醒，但时间还不够明确，所以提醒尚未创建。"
                    f"提醒内容是：{content or '（未提供）'}。"
                    f"原始时间说法是：{raw_time_text or '（未提供）'}。"
                    "请你直接向用户确认更具体的提醒时间，不要再次调用工具。"
                ),
            )

        reminder = self.store.add_reminder(
            profile_user_id=context.profile_user_id,
            session_id=context.session_id,
            content=content,
            due_ts=due_ts,
            raw_time_text=raw_time_text,
        )
        due_label = timestamp_to_datetime_label(due_ts)
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=[
                {
                    "type": "reminder_set",
                    "reminder_id": reminder["reminder_id"],
                    "content": reminder["content"],
                    "due_ts": reminder["due_ts"],
                    "due_label": due_label,
                }
            ],
            followup_context=(
                f"你刚刚已经成功设置了一条提醒：在 {due_label} 提醒用户“{reminder['content']}”。"
                "请你用当前前台角色的语气自然确认这件事，不要再次调用工具。"
            ),
        )

    def _coerce_int(self, value: Any) -> int | None:
        if value is None or value == "":
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None


class ListRemindersToolHandler(BaseToolHandler):
    tool_type = "list_reminders"

    def __init__(self, *, store: MemoryStore) -> None:
        self.store = store

    def tool_spec(self):  # M66-C
        return LIST_REMINDERS_TOOL_SPEC

    def build_prompt_instruction(self) -> str:
        return (
            "- list_reminders：当用户想查看自己现在有哪些提醒时使用。"
            '格式为 {"type":"list_reminders","status":"pending","limit":5}。'
            "通常查看待提醒事项时，status 固定填 pending。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        if str(value.get("type") or "").strip() != self.tool_type:
            return None
        limit = self._coerce_int(value.get("limit")) or 5
        return {
            "type": self.tool_type,
            "status": str(value.get("status") or "pending").strip().lower() or "pending",
            "limit": max(1, min(10, limit)),
        }

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        reminders = self.store.list_reminders(
            profile_user_id=context.profile_user_id,
            session_id=context.session_id,
            status=str(call.get("status") or "pending"),
            limit=int(call.get("limit") or 5),
        )
        items = [
            {
                "index": index,
                "reminder_id": reminder["reminder_id"],
                "content": reminder["content"],
                "due_ts": reminder["due_ts"],
                "due_label": timestamp_to_datetime_label(reminder["due_ts"]),
                "raw_time_text": reminder.get("raw_time_text", ""),
            }
            for index, reminder in enumerate(reminders, start=1)
        ]
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=[
                {
                    "type": "reminder_list",
                    "items": items,
                }
            ],
            followup_context=self._build_followup_context(items),
        )

    def _build_followup_context(self, items: list[dict[str, Any]]) -> str:
        if not items:
            return "当前没有待处理的提醒。请你直接自然告诉用户现在没有提醒。"

        lines = ["当前待处理提醒如下："]
        for item in items:
            lines.append(f"{item['index']}. [{item['due_label']}] {item['content']}")
        lines.append("如果用户接着说要取消第几个提醒，请按这个顺序理解。")
        lines.append("请你自然地把这些提醒告诉用户，不要再调用工具。")
        return "\n".join(lines)

    def _coerce_int(self, value: Any) -> int | None:
        if value is None or value == "":
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None


class CancelReminderToolHandler(BaseToolHandler):
    tool_type = "cancel_reminder"

    def __init__(self, *, store: MemoryStore) -> None:
        self.store = store

    def tool_spec(self):  # M66-C
        return CANCEL_REMINDER_TOOL_SPEC

    def build_prompt_instruction(self) -> str:
        return (
            "- cancel_reminder：当用户明确要取消某条已经存在的提醒时使用。"
            '格式为 {"type":"cancel_reminder","reminder_id":"...","target_text":"提醒线索","target_index":2}。'
            "如果你知道具体是哪一条，优先填 reminder_id；否则可以填 target_text 或 target_index。"
            "如果用户说得还不够明确，不要盲目取消，先追问或先调用 list_reminders。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        if str(value.get("type") or "").strip() != self.tool_type:
            return None

        reminder_id = str(value.get("reminder_id") or "").strip()
        target_text = str(
            value.get("target_text") or value.get("content") or value.get("query") or value.get("reminder") or ""
        ).strip()
        target_index = self._coerce_int(value.get("target_index") or value.get("index"))
        if not reminder_id and not target_text and target_index is None:
            return None
        return {
            "type": self.tool_type,
            "reminder_id": reminder_id,
            "target_text": target_text[:80],
            "target_index": target_index,
        }

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        reminders = self.store.list_reminders(
            profile_user_id=context.profile_user_id,
            session_id=context.session_id,
            status="pending",
            limit=20,
        )
        selection = self._select_reminder(
            reminders=reminders,
            reminder_id=str(call.get("reminder_id") or "").strip(),
            target_text=str(call.get("target_text") or "").strip(),
            target_index=call.get("target_index"),
        )
        if selection["status"] == "none":
            return ToolExecutionResult(
                tool_type=self.tool_type,
                followup_context="当前没有可以取消的待处理提醒。请你直接自然告诉用户现在没有待取消的提醒。",
            )
        if selection["status"] == "not_found":
            return ToolExecutionResult(
                tool_type=self.tool_type,
                followup_context=(
                    "你刚刚尝试取消一条提醒，但没有找到明确匹配的待处理提醒。"
                    "请你先告诉用户没有准确对上，并请他再描述一下是哪一条，必要时可以先列出当前提醒。"
                ),
            )
        if selection["status"] == "ambiguous":
            lines = ["有多条待处理提醒都可能是用户想取消的目标："]
            for item in selection["matches"]:
                lines.append(f"- [{timestamp_to_datetime_label(item['due_ts'])}] {item['content']}")
            lines.append("请你向用户确认具体要取消哪一条，不要再次调用工具。")
            return ToolExecutionResult(
                tool_type=self.tool_type,
                followup_context="\n".join(lines),
            )

        reminder = selection["reminder"]
        cancelled = self.store.cancel_reminder(
            profile_user_id=context.profile_user_id,
            session_id=context.session_id,
            reminder_id=reminder["reminder_id"],
            cancelled_at=context.now_ts,
        )
        if cancelled is None:
            return ToolExecutionResult(
                tool_type=self.tool_type,
                followup_context="那条提醒刚刚已经不在待处理列表里了。请你自然告诉用户这条提醒已经不需要再取消。",
            )

        due_label = timestamp_to_datetime_label(cancelled["due_ts"])
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=[
                {
                    "type": "reminder_cancelled",
                    "reminder_id": cancelled["reminder_id"],
                    "content": cancelled["content"],
                    "due_ts": cancelled["due_ts"],
                    "due_label": due_label,
                }
            ],
            followup_context=(
                f"你刚刚已经成功取消了一条提醒：[{due_label}] {cancelled['content']}。"
                "请你用当前前台角色的语气自然确认取消成功，不要再次调用工具。"
            ),
        )

    def _select_reminder(
        self,
        *,
        reminders: list[dict[str, Any]],
        reminder_id: str,
        target_text: str,
        target_index: int | None,
    ) -> dict[str, Any]:
        if not reminders:
            return {"status": "none"}

        if reminder_id:
            for item in reminders:
                if str(item.get("reminder_id") or "") == reminder_id:
                    return {"status": "ok", "reminder": item}
            return {"status": "not_found"}

        if target_index is not None:
            index = int(target_index)
            if 1 <= index <= len(reminders):
                return {"status": "ok", "reminder": reminders[index - 1]}
            return {"status": "not_found"}

        normalized_query = normalize_text(target_text).lower()
        if not normalized_query:
            return {"status": "not_found"}
        exact_matches: list[dict[str, Any]] = []
        fuzzy_matches: list[dict[str, Any]] = []
        for item in reminders:
            haystack = " ".join(
                [
                    normalize_text(str(item.get("content") or "")).lower(),
                    normalize_text(str(item.get("raw_time_text") or "")).lower(),
                    timestamp_to_datetime_label(int(item.get("due_ts") or 0)).lower(),
                ]
            ).strip()
            if not haystack:
                continue
            if normalized_query == normalize_text(str(item.get("content") or "")).lower():
                exact_matches.append(item)
            elif normalized_query in haystack:
                fuzzy_matches.append(item)
        if len(exact_matches) == 1:
            return {"status": "ok", "reminder": exact_matches[0]}
        if len(exact_matches) > 1:
            return {"status": "ambiguous", "matches": exact_matches}
        if len(fuzzy_matches) == 1:
            return {"status": "ok", "reminder": fuzzy_matches[0]}
        if len(fuzzy_matches) > 1:
            return {"status": "ambiguous", "matches": fuzzy_matches}
        return {"status": "not_found"}

    def _coerce_int(self, value: Any) -> int | None:
        if value is None or value == "":
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None


class CheckInventoryToolHandler(BaseToolHandler):
    tool_type = "check_inventory"

    def __init__(self, *, gift_service) -> None:
        self.gift_service = gift_service

    def tool_spec(self):  # M66-C
        return CHECK_INVENTORY_TOOL_SPEC

    def build_prompt_instruction(self) -> str:
        return (
            "- check_inventory：当你需要查看手边礼物或自己的礼物库存时使用。"
            '格式为 {"type":"check_inventory","scope":"pending_recent|pending_all|kept|internalized","limit":5}。'
            "正常只看手边时优先用 pending_recent；只有确实需要翻完整礼物箱时才用 pending_all。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        if str(value.get("type") or "").strip() != self.tool_type:
            return None
        scope = str(value.get("scope") or "pending_recent").strip().lower() or "pending_recent"
        if scope not in {"pending_recent", "pending_all", "kept", "internalized"}:
            scope = "pending_recent"
        limit = self._coerce_int(value.get("limit")) or (3 if scope == "pending_recent" else 5)
        return {
            "type": self.tool_type,
            "scope": scope,
            "limit": max(1, min(20, limit)),
        }

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        payload = self.gift_service.list_inventory(
            profile_user_id=context.profile_user_id,
            session_id=context.session_id,
            scope=str(call.get("scope") or "pending_recent"),
            limit=int(call.get("limit") or 5),
        )
        items = list(payload.get("items") or [])
        total_count = int(payload.get("total_count") or 0)
        overflow_count = int(payload.get("overflow_count") or 0)
        scope = str(payload.get("scope") or "pending_recent")

        if items:
            lines = [
                f"{index}. {str(item.get('summary') or item.get('display_name') or '未命名礼物')}"
                for index, item in enumerate(items, start=1)
            ]
            items_text = "\n".join(lines)
        else:
            items_text = "(空)"

        followup_context = (
            f"你刚刚查看了礼物库存，scope={scope}。\n"
            f"当前看到的礼物如下：\n{items_text}\n"
            f"当前这一范围总数约为 {total_count} 件。"
        )
        if overflow_count > 0:
            followup_context += f"\n除此之外还有 {overflow_count} 件未在本次结果中展开。"
        followup_context += "\n请基于这份结果自然回应，不要重复调用同一个工具。"

        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=[
                {
                    "type": "inventory_snapshot",
                    "scope": scope,
                    "items": items,
                    "total_count": total_count,
                    "overflow_count": overflow_count,
                }
            ],
            followup_context=followup_context,
        )

    def _coerce_int(self, value: Any) -> int | None:
        if value is None or value == "":
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

class ManageGiftToolHandler(BaseToolHandler):
    tool_type = "manage_gift"

    def __init__(self, *, gift_service, observe_image_fn=None) -> None:
        self.gift_service = gift_service
        self.observe_image_fn = observe_image_fn

    def build_prompt_instruction(self) -> str:
        return (
            "- manage_gift：当用户已经明确表示要怎么处理某份礼物时使用。"
            '格式为 {"type":"manage_gift","action":"observe|keep|internalize|defer|reject|remove|purge","asset_id":"可选"}。'
            "如果当前讨论对象已经很明确，可以省略 asset_id；如果礼物对象不明确，就不要调用这个工具，直接追问。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        if str(value.get("type") or "").strip() != self.tool_type:
            return None
        action = str(value.get("action") or "").strip().lower()
        action = {
            "observe": "observe",
            "look": "observe",
            "save": "keep",
            "keep": "keep",
            "internalize": "internalize",
            "defer": "defer",
            "reject": "reject",
            "remove": "remove",
            "purge": "purge",
            "delete": "purge",
        }.get(action, "")
        if not action:
            return None
        return {
            "type": self.tool_type,
            "action": action,
            "asset_id": str(value.get("asset_id") or "").strip(),
        }

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        action = str(call.get("action") or "").strip().lower()
        target = self.gift_service.resolve_focus_asset(
            profile_user_id=context.profile_user_id,
            session_id=context.session_id,
            asset_id=str(call.get("asset_id") or "").strip(),
        )
        if target is None:
            return ToolExecutionResult(
                tool_type=self.tool_type,
                followup_context=(
                    "你刚刚想处理一份礼物，但当前没有足够明确的礼物对象。"
                    "请你直接向用户确认到底是在说哪一份礼物，不要继续调用 manage_gift。"
                ),
            )

        if action == "observe":
            if self.observe_image_fn is None:
                return ToolExecutionResult(
                    tool_type=self.tool_type,
                    followup_context="你刚刚想先看看这张图，但当前系统里没有可用的查看能力，请自然告诉用户暂时看不了。",
                )
            observed = self.observe_image_fn(
                profile_user_id=context.profile_user_id,
                session_id=context.session_id,
                asset_id=str(target.get("asset_id") or ""),
                timestamp=context.now_ts,
            )
            if observed is None:
                return ToolExecutionResult(
                    tool_type=self.tool_type,
                    followup_context=(
                        "你刚刚想先看看这张图，但目标已经不存在或状态不再可用。"
                        "请自然告诉用户这张图暂时看不了，不要继续调用 manage_gift。"
                    ),
                )
            assistant_line = str(observed.get("assistant_line") or "").strip()
            followup_context = (
                "你刚刚已经看过这张图片了，而且没有把它留下来。"
                "这只是一次被主人分享给你看的日常，不要再把它当成礼物处理。"
            )
            if assistant_line:
                followup_context += f" 你刚刚自然说过的话是：{assistant_line}"
            return ToolExecutionResult(
                tool_type=self.tool_type,
                stream_events=[
                    {
                        "type": "gift_updated",
                        "asset": observed.get("asset") or target,
                        "action": action,
                    }
                ],
                followup_context=followup_context,
            )

        updated = self.gift_service.apply_action(
            profile_user_id=context.profile_user_id,
            session_id=context.session_id,
            asset_id=str(target.get("asset_id") or ""),
            action=action,
            timestamp=context.now_ts,
            source_id=context.current_user_source_id,
        )
        if updated is None:
            return ToolExecutionResult(
                tool_type=self.tool_type,
                followup_context=(
                    "你刚刚尝试处理礼物，但目标已经不存在或状态不再可用。"
                    "请自然告诉用户这份礼物暂时处理不了，不要继续调用 manage_gift。"
                ),
            )

        display_name = (
            str(updated.get("display_name") or updated.get("origin_name") or "这份礼物").strip() or "这份礼物"
        )
        action_label = {
            "keep": "留下",
            "internalize": "吃掉",
            "defer": "暂时放在手边",
            "reject": "放下",
            "remove": "从自己的收藏里放下",
            "purge": "彻底删掉",
        }.get(action, action)
        followup_context = (
            f"你刚刚已经把礼物“{display_name}”处理为：{action_label}。"
            f"当前状态是 {str(updated.get('status') or '') or '已移除'}。"
        )
        if action == "internalize":
            followup_context += "这份礼物已经进入你的可用资源层。"
        elif action == "defer":
            followup_context += "它还留在手边，之后仍然可以继续讨论。"
        elif action == "remove":
            followup_context += "它已经不再属于你的收藏或可用资源层了。"
        elif action == "purge":
            followup_context += "它已经被彻底删除，不会继续留在你的世界里。"
        else:
            followup_context += "请基于这个既成事实，自然接一句，不要重复调用 manage_gift。"

        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=[
                {
                    "type": "gift_updated",
                    "asset": updated,
                    "action": action,
                }
            ],
            followup_context=followup_context,
        )


class ManageArtifactToolHandler(BaseToolHandler):
    tool_type = "manage_artifact"

    def __init__(self, *, artifact_service) -> None:
        self.artifact_service = artifact_service

    def build_prompt_instruction(self) -> str:
        return (
            "- manage_artifact：当你和用户已经商量好某个图片/资产在你世界里的正式名字、集合或用途时使用。"
            '格式为 {"type":"manage_artifact","action":"claim|rename|move|delete","asset_id":"可选",'
            '"display_name":"正式名字","collection_key":"稳定英文id","collection_name":"中文集合名",'
            '"asset_role":"scene|outfit|expression|portrait|album_photo","placement_hint":"可选位置提示"}。'
            "如果当前讨论对象已经很明确，可以省略 asset_id。"
            "scene/album_photo 的集合表示相册或场景分组；outfit/expression/portrait 的集合表示服装或形象分组，"
            "不要把服装或表情放进看起来像纯场景的集合。"
            "expression 的 display_name 优先用 normal/shy/quiet 这类表情 id，collection_name 则填它所属的服装/形象集合。"
            "认领为 outfit/expression/portrait 后，它会变成可切换形象资源；切换时 outfit 对应集合名/id，emotion 对应表情名/id。"
            "只看看、普通收下、吃掉仍使用 manage_gift；不要用 manage_artifact 处理没达成共识的资产。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        if str(value.get("type") or "").strip() != self.tool_type:
            return None
        action = str(value.get("action") or "").strip().lower()
        action = {
            "claim": "claim",
            "rename": "rename",
            "move": "move",
            "delete": "delete",
            "remove": "delete",
        }.get(action, "")
        if not action:
            return None
        return {
            "type": self.tool_type,
            "action": action,
            "asset_id": str(value.get("asset_id") or "").strip(),
            "display_name": str(value.get("display_name") or value.get("name") or value.get("title") or "").strip()[
                :80
            ],
            "collection_key": str(value.get("collection_key") or value.get("container_key") or "").strip()[:64],
            "collection_name": str(value.get("collection_name") or value.get("container_name") or "").strip()[:32],
            "asset_role": str(value.get("asset_role") or value.get("role") or "").strip().lower()[:32],
            "placement_hint": str(value.get("placement_hint") or value.get("placement") or "").strip()[:80],
        }

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        action = str(call.get("action") or "").strip().lower()
        try:
            updated = self.artifact_service.manage_artifact(
                profile_user_id=context.profile_user_id,
                session_id=context.session_id,
                asset_id=str(call.get("asset_id") or "").strip(),
                action=action,
                display_name=str(call.get("display_name") or "").strip(),
                collection_key=str(call.get("collection_key") or "").strip(),
                collection_name=str(call.get("collection_name") or "").strip(),
                asset_role=str(call.get("asset_role") or "").strip(),
                placement_hint=str(call.get("placement_hint") or "").strip(),
                timestamp=context.now_ts,
                source_id=context.current_user_source_id,
            )
        except ValueError as exc:
            return ToolExecutionResult(
                tool_type=self.tool_type,
                followup_context=(
                    f"你刚刚想整理世界资产，但信息还不够完整：{exc}。"
                    "请直接向用户确认名字、集合或用途，不要继续调用 manage_artifact。"
                ),
            )

        if updated is None:
            return ToolExecutionResult(
                tool_type=self.tool_type,
                followup_context=(
                    "你刚刚想整理一份世界资产，但当前没有足够明确的对象。"
                    "请直接向用户确认到底是在说哪一张图或哪份资产，不要继续调用 manage_artifact。"
                ),
            )

        payload = updated.get("payload") if isinstance(updated.get("payload"), dict) else {}
        flags = updated.get("artifact_flags") if isinstance(updated.get("artifact_flags"), dict) else {}
        display_name = (
            str(updated.get("display_name") or updated.get("origin_name") or "这份资产").strip() or "这份资产"
        )
        collection_name = str(payload.get("collection_name") or updated.get("container_name") or "").strip()
        asset_role = str(payload.get("asset_role") or flags.get("asset_role") or "").strip()
        action_label = {
            "claim": "正式认领",
            "rename": "重命名",
            "move": "移动集合",
            "delete": "从世界里移除",
        }.get(action, action)
        followup_context = f"你刚刚已经把“{display_name}”完成了：{action_label}。"
        if collection_name:
            followup_context += f" 当前集合是「{collection_name}」。"
        if asset_role:
            followup_context += f" 它现在的世界资产类型是 {asset_role}。"
        if action == "delete":
            followup_context += (
                " 它已经不再显示在你的收藏或资源里；如果用户要彻底删除文件，应该再明确确认后使用 manage_gift 的 purge。"
            )
        else:
            followup_context += " 请基于这个既成事实自然回应，不要重复调用 manage_artifact。"

        projection_changed = str(updated.get("asset_type") or "").strip().lower() == "image" and (
            str(payload.get("projection_role") or "").strip().lower() == "scene"
            or action in {"claim", "rename", "move", "delete"}
            or str(updated.get("status") or "").strip().lower() == "internalized"
        )

        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=[
                {
                    "type": "artifact_updated",
                    "asset": updated,
                    "action": action,
                    "projection_changed": projection_changed,
                }
            ],
            followup_context=followup_context,
        )


class ManagePersonaToolHandler(BaseToolHandler):
    tool_type = "manage_persona"

    def __init__(self, *, persona_service) -> None:
        self.persona_service = persona_service

    def build_prompt_instruction(self) -> str:
        return (
            "- manage_persona：用于保存或调整当前前台角色的表达侧面卡片。"
            '格式为 {"type":"manage_persona","action":"create|update|inspect|archive|delete",'
            '"card_id":"可选","name":"名字","summary":"核心摘要",'
            '"speech_style":"说话方式","interaction_bias":"互动倾向",'
            '"resource_preference":"场景/BGM/服装偏好","switch_hint":"适合进入的氛围",'
            '"unsuitable_contexts":"不擅长应对的情景","reason":"为什么这样做"}。'
            "已有卡的本轮选择由 persona.active 表达；manage_persona 只处理卡片本身。"
            "当新的表达侧面变得清晰、值得留下时可以 create；当前卡大方向正确但不够自然时可以 update 当前卡。"
            "archive/delete 用于收起不再需要的卡片。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        if str(value.get("type") or "").strip() != self.tool_type:
            return None
        action = str(value.get("action") or "").strip().lower()
        action = {
            "create": "create",
            "new": "create",
            "update": "update",
            "edit": "update",
            "tune": "update",
            "inspect": "inspect",
            "read": "inspect",
            "view": "inspect",
            "archive": "archive",
            "hide": "archive",
            "delete": "delete",
            "remove": "delete",
        }.get(action, "")
        if not action:
            return None
        return {
            "type": self.tool_type,
            "action": action,
            "card_id": str(value.get("card_id") or value.get("persona_id") or "").strip()[:64],
            "name": str(value.get("name") or value.get("title") or "").strip()[:32],
            "summary": str(value.get("summary") or value.get("description") or "").strip()[:220],
            "speech_style": str(value.get("speech_style") or value.get("style") or "").strip()[:180],
            "interaction_bias": str(value.get("interaction_bias") or value.get("bias") or "").strip()[:180],
            "resource_preference": str(value.get("resource_preference") or value.get("preference") or "").strip()[:180],
            "switch_hint": str(value.get("switch_hint") or value.get("when_to_use") or "").strip()[:160],
            "unsuitable_contexts": str(
                value.get("unsuitable_contexts") or value.get("weakness") or value.get("not_good_at") or ""
            ).strip()[:180],
            "reason": str(value.get("reason") or "").strip()[:240],
        }

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        action = str(call.get("action") or "").strip().lower()
        try:
            if action == "create":
                card = self.persona_service.create_card(
                    profile_user_id=context.profile_user_id,
                    session_id=context.session_id,
                    name=str(call.get("name") or ""),
                    summary=str(call.get("summary") or ""),
                    speech_style=str(call.get("speech_style") or ""),
                    interaction_bias=str(call.get("interaction_bias") or ""),
                    resource_preference=str(call.get("resource_preference") or ""),
                    switch_hint=str(call.get("switch_hint") or ""),
                    unsuitable_contexts=str(call.get("unsuitable_contexts") or ""),
                    reason=str(call.get("reason") or ""),
                    timestamp=context.now_ts,
                    source_id=context.current_user_source_id,
                )
                return self._result_for_card(
                    action=action,
                    card=card,
                    followup=(
                        f"新的表达侧面「{card['name']}」(id:{card['card_id']}) 已形成。接下来让回应自然贴合这张卡。"
                    ),
                    state_changed=True,
                )

            if action == "update":
                card = self.persona_service.update_active_card(
                    profile_user_id=context.profile_user_id,
                    session_id=context.session_id,
                    fields={
                        "name": str(call.get("name") or ""),
                        "summary": str(call.get("summary") or ""),
                        "speech_style": str(call.get("speech_style") or ""),
                        "interaction_bias": str(call.get("interaction_bias") or ""),
                        "resource_preference": str(call.get("resource_preference") or ""),
                        "switch_hint": str(call.get("switch_hint") or ""),
                        "unsuitable_contexts": str(call.get("unsuitable_contexts") or ""),
                    },
                    reason=str(call.get("reason") or ""),
                    timestamp=context.now_ts,
                    source_id=context.current_user_source_id,
                )
                return self._result_for_card(
                    action=action,
                    card=card,
                    followup=(
                        f"当前表达侧面「{card['name']}」(id:{card['card_id']}) 已微调。"
                        "接下来让回应自然贴合调整后的倾向。"
                    ),
                    state_changed=True,
                )

            if action == "inspect":
                card = self.persona_service.inspect_card(
                    profile_user_id=context.profile_user_id,
                    session_id=context.session_id,
                    card_id=str(call.get("card_id") or ""),
                    name=str(call.get("name") or ""),
                )
                if card is None:
                    return ToolExecutionResult(
                        tool_type=self.tool_type,
                        followup_context="你刚刚想查看一张人设卡，但没有找到明确目标。请自然告诉用户暂时没找到这张卡。",
                    )
                return self._result_for_card(
                    action=action,
                    card=card,
                    followup=self._build_inspect_followup(card),
                    state_changed=False,
                )

            if action == "archive":
                card = self.persona_service.archive_card(
                    profile_user_id=context.profile_user_id,
                    session_id=context.session_id,
                    card_id=str(call.get("card_id") or ""),
                    name=str(call.get("name") or ""),
                    reason=str(call.get("reason") or ""),
                    timestamp=context.now_ts,
                    source_id=context.current_user_source_id,
                )
                if card is None:
                    return ToolExecutionResult(
                        tool_type=self.tool_type,
                        followup_context="你刚刚想归档一张人设卡，但没有找到明确目标。请自然告诉用户暂时没找到这张卡。",
                    )
                return self._result_for_card(
                    action=action,
                    card=card,
                    followup=f"你刚刚已经把人设卡「{card['name']}」归档。可以不主动汇报，除非用户正在询问这件事。",
                    state_changed=True,
                )

            if action == "delete":
                card = self.persona_service.delete_card(
                    profile_user_id=context.profile_user_id,
                    session_id=context.session_id,
                    card_id=str(call.get("card_id") or ""),
                    name=str(call.get("name") or ""),
                    reason=str(call.get("reason") or ""),
                    timestamp=context.now_ts,
                    source_id=context.current_user_source_id,
                )
                if card is None:
                    return ToolExecutionResult(
                        tool_type=self.tool_type,
                        followup_context="你刚刚想删除一张人设卡，但没有找到明确目标。请自然告诉用户暂时没找到这张卡。",
                    )
                return self._result_for_card(
                    action=action,
                    card=card,
                    followup=f"你刚刚已经删除人设卡「{card['name']}」。删除属于明确管理操作，可以自然向用户确认。",
                    state_changed=True,
                )
        except ValueError as exc:
            return ToolExecutionResult(
                tool_type=self.tool_type,
                followup_context=(
                    f"你刚刚想管理人设卡，但信息还不够完整：{exc}。请自然继续对话，不要重复调用 manage_persona。"
                ),
            )

        return ToolExecutionResult(
            tool_type=self.tool_type,
            followup_context="你刚刚想管理人设卡，但动作不受支持。请自然继续对话，不要重复调用 manage_persona。",
        )

    def _result_for_card(
        self,
        *,
        action: str,
        card: dict[str, Any],
        followup: str,
        state_changed: bool,
    ) -> ToolExecutionResult:
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=[
                {
                    "type": "persona_state",
                    "action": action,
                    "card": self._compact_card(card),
                    "silent": action in {"create", "update", "archive"},
                }
            ],
            followup_context=followup,
            state_updates={
                "persona_state_changed": bool(state_changed),
                "persona_action": action,
                "active_persona_id": str(card.get("card_id") or "")
                if str(card.get("status") or "") == "active"
                else "",
            },
        )

    def _build_inspect_followup(self, card: dict[str, Any]) -> str:
        lines = [
            f"你刚刚查看了人设卡「{card.get('name') or card.get('card_id')}」(id:{card.get('card_id')})。",
            f"状态：{card.get('status')}",
            f"摘要：{card.get('summary') or '(无)'}",
            f"说话方式：{card.get('speech_style') or '(无)'}",
            f"互动倾向：{card.get('interaction_bias') or '(无)'}",
            f"资源偏好：{card.get('resource_preference') or '(无)'}",
            f"适合进入：{card.get('switch_hint') or '(无)'}",
            f"不擅长应对：{card.get('unsuitable_contexts') or '(无)'}",
            "请根据用户是否真的在询问这张卡，决定是否自然说明；不要重复调用 manage_persona。",
        ]
        return "\n".join(lines)

    def _compact_card(self, card: dict[str, Any]) -> dict[str, Any]:
        return {
            "card_id": str(card.get("card_id") or ""),
            "name": str(card.get("name") or ""),
            "status": str(card.get("status") or ""),
            "summary": str(card.get("summary") or ""),
        }
