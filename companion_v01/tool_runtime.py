from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Callable

from .npc_runtime import GenericNPCRuntime
from .store import MemoryStore
from .task_workspace import TaskWorkspaceService
from .text_utils import normalize_text, resolve_reminder_due_timestamp, timestamp_to_datetime_label


@dataclass(frozen=True)
class ToolExecutionContext:
    profile_user_id: str
    session_id: str
    now_ts: int
    visual_payload: dict[str, Any]
    current_user_source_id: str = ""


@dataclass
class ToolExecutionResult:
    tool_type: str
    raw_turns: list[dict[str, Any]] = field(default_factory=list)
    stream_events: list[dict[str, Any]] = field(default_factory=list)
    followup_context: str = ""
    state_updates: dict[str, Any] = field(default_factory=dict)


class BaseToolHandler:
    tool_type: str = ""

    def build_prompt_instruction(self) -> str:
        raise NotImplementedError

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        raise NotImplementedError

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        raise NotImplementedError


class RetrieveMemoryToolHandler(BaseToolHandler):
    tool_type = "retrieve_memory"

    def __init__(
        self,
        *,
        retrieve_fn: Callable[..., ToolExecutionResult],
    ) -> None:
        self.retrieve_fn = retrieve_fn

    def build_prompt_instruction(self) -> str:
        return (
            "- retrieve_memory：当你看完当前原始上下文、阶段摘要、长期语义记忆和可用回忆片段后，"
            "仍然觉得需要主动回想更早内容时使用。"
            "这是内部记忆检索工具，不是对用户说出口的话。"
            "格式为 {\"type\":\"retrieve_memory\",\"query\":\"简短搜索短句\",\"keywords\":[\"关键词\"],"
            "\"time_hint\":{\"date_label\":\"YYYY-MM-DD\",\"time_of_day\":\"morning|afternoon|night|midnight\"}}。"
            "query 要写具体实体、地点、人物、事件或偏好，不要写“帮我回忆一下”这类空泛句。"
            "只有当前可见记忆不足以回答时才调用；如果不需要检索，tool_call 输出 null。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None

        call_type = str(value.get("type") or "").strip()
        if call_type != self.tool_type:
            return None

        query = str(value.get("query") or value.get("rewritten_query") or value.get("prompt") or "").strip()
        query = normalize_text(query)
        if not query:
            return None

        raw_keywords = value.get("keywords")
        keyword_candidates: list[str] = []
        if isinstance(raw_keywords, list):
            keyword_candidates = [str(item or "") for item in raw_keywords]
        elif isinstance(raw_keywords, str):
            keyword_candidates = [part for part in re.split(r"[,，;；|、\s]+", raw_keywords) if part]

        keywords: list[str] = []
        seen: set[str] = set()
        for item in keyword_candidates:
            keyword = normalize_text(item).strip("[](){}\"' ")
            if not keyword or len(keyword) > 32:
                continue
            dedupe_key = keyword.lower()
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            keywords.append(keyword)
            if len(keywords) >= 8:
                break

        time_hint: dict[str, Any] = {}
        raw_time_hint = value.get("time_hint")
        if isinstance(raw_time_hint, dict):
            for key in ("date_label", "time_of_day", "relative_time", "start_ts", "end_ts"):
                if key not in raw_time_hint:
                    continue
                item = raw_time_hint.get(key)
                if item is None:
                    continue
                if key in {"start_ts", "end_ts"}:
                    try:
                        time_hint[key] = int(item)
                    except Exception:
                        continue
                else:
                    text = str(item or "").strip()
                    if text:
                        time_hint[key] = text

        return {
            "type": self.tool_type,
            "query": query[:200],
            "keywords": keywords,
            "time_hint": time_hint,
        }

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        return self.retrieve_fn(call=call, context=context)


class CallNPCToolHandler(BaseToolHandler):
    tool_type = "call_npc"

    def __init__(
        self,
        *,
        npc_runtime: GenericNPCRuntime,
        describe_scene: Callable[[dict[str, Any]], str],
        build_followup_context: Callable[[dict[str, Any]], str],
    ) -> None:
        self.npc_runtime = npc_runtime
        self.describe_scene = describe_scene
        self.build_followup_context = build_followup_context

    def build_prompt_instruction(self) -> str:
        return (
            "- call_npc：当场景里需要路人、店员、摊主之类的临时 NPC 先回答一句时使用。"
            "格式为 {\"type\":\"call_npc\",\"npc_name\":\"名字\",\"npc_role\":\"身份\",\"query\":\"要问的话\"}。"
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

    def build_prompt_instruction(self) -> str:
        return (
            "- set_reminder：当用户明确要求你稍后提醒、明天提醒、今晚提醒某件事时使用。"
            "格式为 {\"type\":\"set_reminder\",\"content\":\"提醒内容\",\"time_text\":\"原始时间说法\",\"offset_minutes\":5,\"date_label\":\"YYYY-MM-DD\",\"time_of_day\":\"morning|afternoon|night|midnight\",\"hour\":20,\"minute\":0}。"
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
            value.get("content")
            or value.get("task")
            or value.get("reminder")
            or value.get("text")
            or ""
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
                value.get("offset_minutes")
                or value.get("delay_minutes")
                or value.get("minutes_later")
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

    def build_prompt_instruction(self) -> str:
        return (
            "- list_reminders：当用户想查看自己现在有哪些提醒时使用。"
            "格式为 {\"type\":\"list_reminders\",\"status\":\"pending\",\"limit\":5}。"
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

    def build_prompt_instruction(self) -> str:
        return (
            "- cancel_reminder：当用户明确要取消某条已经存在的提醒时使用。"
            "格式为 {\"type\":\"cancel_reminder\",\"reminder_id\":\"...\",\"target_text\":\"提醒线索\",\"target_index\":2}。"
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
            value.get("target_text")
            or value.get("content")
            or value.get("query")
            or value.get("reminder")
            or ""
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

    def build_prompt_instruction(self) -> str:
        return (
            "- check_inventory：当你需要查看手边礼物或自己的礼物库存时使用。"
            "格式为 {\"type\":\"check_inventory\",\"scope\":\"pending_recent|pending_all|kept|internalized\",\"limit\":5}。"
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


class InspectAttachmentToolHandler(BaseToolHandler):
    tool_type = "inspect_attachment"

    def __init__(self, *, attachment_service) -> None:
        self.attachment_service = attachment_service

    def build_prompt_instruction(self) -> str:
        return (
            "- inspect_attachment：当你需要展开查看当前聊天里临时发来的图片或文件时使用。"
            "格式为 {\"type\":\"inspect_attachment\",\"target\":\"可选：附件id/标题/文件名/最近\",\"kind\":\"any|image|file|document|audio\"}。"
            "附件只是临时上下文，不是礼物；单独查看某个附件时使用。"
            "如果要同时对比多份附件，优先使用 sync_attachment_workspace。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        if str(value.get("type") or "").strip() != self.tool_type:
            return None
        return {
            "type": self.tool_type,
            "target": str(value.get("target") or value.get("attachment_id") or value.get("query") or "latest").strip()[:120],
            "kind": self._normalize_kind(value.get("kind") or value.get("asset_type") or "any"),
        }

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        result = self.attachment_service.inspect_attachment(
            profile_user_id=context.profile_user_id,
            session_id=context.session_id,
            target=str(call.get("target") or ""),
            kind=str(call.get("kind") or "any"),
            timestamp=context.now_ts,
        )
        item = result.get("item") if isinstance(result, dict) else None
        events = []
        if isinstance(item, dict):
            events.append(
                {
                    "type": "attachment_inspected",
                    "attachment": item,
                }
            )
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=events,
            followup_context=str(result.get("followup_context") or "") if isinstance(result, dict) else "",
        )

    def _normalize_kind(self, value: Any) -> str:
        kind = str(value or "any").strip().lower()
        if kind in {"photo", "picture", "pic", "img"}:
            return "image"
        if kind in {"doc", "text", "txt", "pdf"}:
            return "document"
        if kind in {"music", "song", "voice"}:
            return "audio"
        if kind in {"any", "image", "file", "document", "audio"}:
            return kind
        return "any"


class ReadAttachmentSectionToolHandler(BaseToolHandler):
    tool_type = "read_attachment_section"

    def __init__(self, *, attachment_service) -> None:
        self.attachment_service = attachment_service

    def build_prompt_instruction(self) -> str:
        return (
            "- read_attachment_section：当临时文件较长、你需要展开某一页/某几行/某个表/某个 sheet 的内容时使用。"
            "格式为 {\"type\":\"read_attachment_section\",\"target\":\"file_001|标题|文件名|latest\","
            "\"section\":\"第2页|第10-30行|第1个表|Sheet1\",\"kind\":\"any|file|document\"}。"
            "它只展开当前已解析出的可用文本片段；如果文件本身没有文本层或还没解析好，系统会告诉你。"
            "不要用它处理图片礼物或长期记忆。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        if str(value.get("type") or "").strip() != self.tool_type:
            return None
        return {
            "type": self.tool_type,
            "target": str(value.get("target") or value.get("attachment_id") or value.get("query") or "latest").strip()[:120],
            "section": str(value.get("section") or value.get("range") or value.get("page") or "当前可用片段").strip()[:120],
            "kind": self._normalize_kind(value.get("kind") or "document"),
        }

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        result = self.attachment_service.read_section(
            profile_user_id=context.profile_user_id,
            session_id=context.session_id,
            target=str(call.get("target") or ""),
            section=str(call.get("section") or ""),
            kind=str(call.get("kind") or "document"),
            timestamp=context.now_ts,
        )
        item = result.get("item") if isinstance(result, dict) else None
        events = []
        if isinstance(item, dict):
            events.append(
                {
                    "type": "attachment_section_read",
                    "attachment": item,
                    "section": str(call.get("section") or ""),
                }
            )
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=events,
            followup_context=str(result.get("followup_context") or "") if isinstance(result, dict) else "",
        )

    def _normalize_kind(self, value: Any) -> str:
        kind = str(value or "document").strip().lower()
        if kind in {"doc", "text", "txt", "pdf"}:
            return "document"
        if kind in {"any", "file", "document"}:
            return kind
        return "document"


class SyncAttachmentWorkspaceToolHandler(BaseToolHandler):
    tool_type = "sync_attachment_workspace"

    def __init__(self, *, attachment_service) -> None:
        self.attachment_service = attachment_service

    def build_prompt_instruction(self) -> str:
        return (
            "- sync_attachment_workspace：当你需要整理当前附件工作台时使用。新发来的图片/文件通常会自动进入工作台；"
            "这个工具主要用于收起暂时不分析的附件、重新指定重点材料，或切换要对比的对象。"
            "格式为 {\"type\":\"sync_attachment_workspace\",\"focus_targets\":[\"img_001\",\"第2张图\",\"菜单照片\"],\"kind\":\"any|image|file|document|audio\",\"reason\":\"为什么需要这些附件\"}。"
            "focus_targets 是整理后的最终工作台清单；可以一次保留多张图片或多个文件进行对比。"
            "未列入的其它附件会留在旁边文件筐，只给识别信息。"
            "系统会按上下文预算尽量展开你选中的附件；如果某些大文件放不下，会提示你用 read_attachment_section 指定页、行或 sheet。"
            "不要用一连串打开/关闭操作；一次性提交整理后的最终清单即可。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        if str(value.get("type") or "").strip() != self.tool_type:
            return None
        targets = value.get("focus_targets")
        if targets is None:
            targets = value.get("targets") or value.get("attachment_ids") or value.get("target") or []
        normalized_targets = self._normalize_targets(targets)
        return {
            "type": self.tool_type,
            "focus_targets": normalized_targets[:30],
            "kind": self._normalize_kind(value.get("kind") or "any"),
            "reason": str(value.get("reason") or "").strip()[:160],
        }

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        result = self.attachment_service.sync_workspace(
            profile_user_id=context.profile_user_id,
            session_id=context.session_id,
            focus_targets=list(call.get("focus_targets") or []),
            kind=str(call.get("kind") or "any"),
            reason=str(call.get("reason") or ""),
            timestamp=context.now_ts,
        )
        focused = list(result.get("focused") or []) if isinstance(result, dict) else []
        events = []
        if focused:
            events.append(
                {
                    "type": "attachment_workspace_synced",
                    "items": focused,
                }
            )
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=events,
            followup_context=str(result.get("followup_context") or "") if isinstance(result, dict) else "",
        )

    def _normalize_targets(self, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            raw_items = [item.strip() for item in value.replace("，", ",").replace("、", ",").split(",")]
        elif isinstance(value, (list, tuple, set)):
            raw_items = list(value)
        else:
            raw_items = [value]
        targets: list[str] = []
        for item in raw_items:
            text = str(item or "").strip()
            if text and text not in targets:
                targets.append(text[:120])
        return targets

    def _normalize_kind(self, value: Any) -> str:
        kind = str(value or "any").strip().lower()
        if kind in {"photo", "picture", "pic", "img"}:
            return "image"
        if kind in {"doc", "text", "txt", "pdf"}:
            return "document"
        if kind in {"music", "song", "voice"}:
            return "audio"
        if kind in {"any", "image", "file", "document", "audio"}:
            return kind
        return "any"


class ClearAttachmentFocusToolHandler(BaseToolHandler):
    tool_type = "clear_attachment_focus"

    def __init__(self, *, attachment_service) -> None:
        self.attachment_service = attachment_service

    def build_prompt_instruction(self) -> str:
        return (
            "- clear_attachment_focus：当临时图片/文件已经聊完、用户说发错了、或你判断不需要继续挂在上下文时使用。"
            "格式为 {\"type\":\"clear_attachment_focus\",\"target\":\"current|latest|all|附件id/标题/文件名\",\"targets\":[\"img_001\",\"第2张图\"],\"kind\":\"any|image|file|document|audio\",\"reason\":\"可选原因\"}。"
            "清理多个指定附件时用 targets 数组；清理全部图片或文件时用 target=all 并配合 kind。"
            "它只清理临时附件焦点，不删除聊天记忆，也不处理礼物系统。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        if str(value.get("type") or "").strip() != self.tool_type:
            return None
        targets = value.get("targets")
        if targets is None:
            targets = value.get("attachment_ids")
        return {
            "type": self.tool_type,
            "target": str(value.get("target") or value.get("attachment_id") or value.get("query") or "current").strip()[:120],
            "targets": self._normalize_targets(targets),
            "kind": self._normalize_kind(value.get("kind") or "any"),
            "reason": str(value.get("reason") or "").strip()[:160],
        }

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        result = self.attachment_service.clear_focus(
            profile_user_id=context.profile_user_id,
            session_id=context.session_id,
            target=str(call.get("target") or "current"),
            targets=list(call.get("targets") or []),
            kind=str(call.get("kind") or "any"),
            reason=str(call.get("reason") or ""),
            timestamp=context.now_ts,
        )
        cleared = list(result.get("cleared") or []) if isinstance(result, dict) else []
        events = []
        if cleared:
            events.append(
                {
                    "type": "attachment_focus_cleared",
                    "items": cleared,
                }
            )
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=events,
            followup_context=str(result.get("followup_context") or "") if isinstance(result, dict) else "",
        )

    def _normalize_kind(self, value: Any) -> str:
        kind = str(value or "any").strip().lower()
        if kind in {"photo", "picture", "pic", "img"}:
            return "image"
        if kind in {"doc", "text", "txt", "pdf"}:
            return "document"
        if kind in {"music", "song", "voice"}:
            return "audio"
        if kind in {"any", "image", "file", "document", "audio"}:
            return kind
        return "any"

    def _normalize_targets(self, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            raw_items = [item.strip() for item in value.replace("，", ",").replace("、", ",").split(",")]
        elif isinstance(value, (list, tuple, set)):
            raw_items = list(value)
        else:
            raw_items = [value]
        targets: list[str] = []
        for item in raw_items:
            text = str(item or "").strip()
            if text and text not in targets:
                targets.append(text[:120])
        return targets[:20]


class RetryAttachmentToolHandler(BaseToolHandler):
    tool_type = "retry_attachment"

    def __init__(self, *, attachment_ingest_service) -> None:
        self.attachment_ingest_service = attachment_ingest_service

    def build_prompt_instruction(self) -> str:
        return (
            "- retry_attachment：当临时图片/文件处理失败，且用户让你再试一次，或你需要重新读取失败附件时使用。"
            "格式为 {\"type\":\"retry_attachment\",\"target\":\"latest|附件id|img_001|标题|文件名\",\"kind\":\"any|image|file|document|audio\",\"reason\":\"可选原因\"}。"
            "这个工具只会重新处理临时附件，不会把它变成礼物或长期记忆；成功后附件会回到临时附件焦点。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        if str(value.get("type") or "").strip() != self.tool_type:
            return None
        return {
            "type": self.tool_type,
            "target": str(value.get("target") or value.get("attachment_id") or value.get("query") or "latest").strip()[:120],
            "kind": self._normalize_kind(value.get("kind") or "any"),
            "reason": str(value.get("reason") or "").strip()[:160],
        }

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        result = self.attachment_ingest_service.retry_attachment(
            profile_user_id=context.profile_user_id,
            session_id=context.session_id,
            target=str(call.get("target") or "latest"),
            kind=str(call.get("kind") or "any"),
            timestamp=context.now_ts,
        )
        item = result.get("item") if isinstance(result, dict) else None
        events = []
        if isinstance(item, dict):
            events.append(
                {
                    "type": "attachment_retry_started",
                    "status": str(result.get("status") or ""),
                    "item": item,
                }
            )
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=events,
            followup_context=str(result.get("followup_context") or "") if isinstance(result, dict) else "",
        )

    def _normalize_kind(self, value: Any) -> str:
        kind = str(value or "any").strip().lower()
        if kind in {"photo", "picture", "pic", "img"}:
            return "image"
        if kind in {"doc", "text", "txt", "pdf"}:
            return "document"
        if kind in {"music", "song", "voice"}:
            return "audio"
        if kind in {"any", "image", "file", "document", "audio"}:
            return kind
        return "any"


class FetchMediaFromUrlToolHandler(BaseToolHandler):
    tool_type = "fetch_media_from_url"

    def __init__(self, *, attachment_ingest_service) -> None:
        self.attachment_ingest_service = attachment_ingest_service

    def build_prompt_instruction(self) -> str:
        return (
            "- fetch_media_from_url：当用户直接给你公开视频/音频链接，想让你先把素材下载到当前工作台时使用。"
            "格式为 {\"type\":\"fetch_media_from_url\",\"url\":\"https://...\",\"preferred_title\":\"可选标题\"}，"
            "批量时可用 {\"type\":\"fetch_media_from_url\",\"urls\":[\"https://...\",\"https://...\"]}。"
            "在 QQ/桌宠模式里，如果用户只发来一个公开视频或音频链接，或说“下载/拉进来/转写/总结这个链接”，"
            "应优先调用这个工具实际获取素材；不要只凭猜测说链接打不开、需要登录或平台不稳定。"
            "如果用户说“再试一次/重新下载/继续试”，且最近对话里有明确链接，也应带上那个链接重新调用。"
            "它只负责把公开可访问的媒体链接下载成临时附件，不会直接总结、转写或转码；"
            "下载成功后，这些素材会像普通 audio_001/file_001 一样进入临时附件工作台，之后再继续用 inspect_attachment、inspect_media_info、transcribe_media、convert_media_file 或 send_file。"
            "如果用户只是要原视频/原音频或“把链接里的文件发我”，下载成功后直接 send_file 对应 handle，不要顺手转写、提音频或压缩。"
            "不要用它处理需要登录、付费、会员、DRM 或整条播放列表/合集的链接。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        if str(value.get("type") or "").strip() != self.tool_type:
            return None
        urls_value = (
            value.get("urls")
            if value.get("urls") is not None
            else value.get("links")
            if value.get("links") is not None
            else value.get("url")
        )
        urls = self._normalize_urls(urls_value)
        if not urls:
            return None
        return {
            "type": self.tool_type,
            "url": urls[0],
            "urls": urls,
            "preferred_title": str(value.get("preferred_title") or value.get("title") or "").strip()[:120],
        }

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        result = self.attachment_ingest_service.fetch_media_from_urls(
            profile_user_id=context.profile_user_id,
            session_id=context.session_id,
            urls=list(call.get("urls") or []),
            preferred_title=str(call.get("preferred_title") or ""),
            timestamp=context.now_ts,
        )
        events = []
        if isinstance(result, dict):
            for item in list(result.get("items") or []):
                if not isinstance(item, dict):
                    continue
                events.append(
                    {
                        "type": "attachment_remote_media_ready",
                        "item": item,
                    }
                )
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=events,
            followup_context=str(result.get("followup_context") or "") if isinstance(result, dict) else "",
        )

    def _normalize_urls(self, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            raw_items = re.split(r"[\s,，;；]+", value)
        elif isinstance(value, (list, tuple, set)):
            raw_items = list(value)
        else:
            raw_items = [value]
        urls: list[str] = []
        for item in raw_items:
            text = str(item or "").strip()
            if text.startswith(("http://", "https://")) and text not in urls:
                urls.append(text[:1000])
        return urls[:8]


class ComposeFileToolHandler(BaseToolHandler):
    tool_type = "compose_file"

    def __init__(self, *, generated_file_service) -> None:
        self.generated_file_service = generated_file_service

    def build_prompt_instruction(self) -> str:
        return (
            "- compose_file：当用户要你把临时附件、已生成文件或当前对话内容整理成一个新文件时使用。"
            "如果用户明确要求生成/导出文件，或在已有任务后说“开始/继续/直接做”，不要只口头答应，"
            "应立刻在 tool_call 调用 compose_file。"
            "格式为 {\"type\":\"compose_file\",\"source_ids\":[\"file_001\",\"gen_001\"],"
            "\"task\":\"要整理/改写/导出的目标\",\"output_format\":\"md|txt|docx|xlsx|pdf|json|csv|html\","
            "\"output_title\":\"文件标题\",\"structure\":\"summary|table|report|notes|custom\","
            "\"style\":\"clean|formal|casual\",\"content_markdown\":\"你整理好的正文或 Markdown\","
            "\"table_rows\":[[\"列1\",\"列2\"],[\"内容1\",\"内容2\"]],"
            "\"formatting\":{\"header\":{\"bold\":true},\"columns\":[{\"match_header\":\"姓名\",\"font_color\":\"red\"}],"
            "\"highlights\":[{\"text\":\"重点\",\"fill_color\":\"yellow\"}]},\"send_to_user\":true}。"
            "这个工具只负责把你已经整理好的内容渲染成文件；如果需要提取重点、改写或排版，"
            "请把最终内容写进 content_markdown 或 table_rows，不要只写一句任务就指望工具替你思考。"
            "但如果用户只是要求忠实转换/导出原始附件（例如 TXT 转 PDF/Word、原文导出），"
            "不要把提示词里的短预览复制进 content_markdown；请留空 content_markdown/table_rows，"
            "只填写 source_ids、task、output_format、output_title，后端会从原始附件读取更完整的安全材料。"
            "要生成表格优先用 table_rows；要生成 Word/PDF/Markdown 优先用 content_markdown。"
            "需要标红、加粗、黄色高亮时，把明确规则写进 formatting；后端只执行白名单样式字段。"
            "生成结果会成为 gen_001 这类可继续修改的生成文件，不会覆盖用户原始附件。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        if str(value.get("type") or "").strip() != self.tool_type:
            return None
        sources = (
            value.get("source_ids")
            if value.get("source_ids") is not None
            else value.get("sources")
            if value.get("sources") is not None
            else value.get("targets")
            if value.get("targets") is not None
            else value.get("target")
        )
        output_format = self._normalize_output_format(value.get("output_format") or value.get("format") or "md")
        table_rows = self._normalize_table_rows(value.get("table_rows") or value.get("rows") or value.get("table"))
        return {
            "type": self.tool_type,
            "source_ids": self._normalize_sources(sources),
            "task": str(value.get("task") or value.get("instruction") or value.get("goal") or "").strip()[:500],
            "output_format": output_format,
            "output_title": str(value.get("output_title") or value.get("title") or value.get("name") or "").strip()[:80],
            "structure": str(value.get("structure") or value.get("layout") or "").strip()[:80],
            "style": str(value.get("style") or "").strip()[:80],
            "fidelity": str(value.get("fidelity") or "").strip()[:80],
            "content_markdown": str(
                value.get("content_markdown")
                or value.get("markdown")
                or value.get("content")
                or value.get("body")
                or ""
            ).strip()[:80000],
            "table_rows": table_rows,
            "formatting": self._normalize_formatting(value.get("formatting") or value.get("styles") or value.get("style_rules")),
            "send_to_user": self._coerce_bool(value.get("send_to_user"), default=True),
        }

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        result = self.generated_file_service.compose_file(
            profile_user_id=context.profile_user_id,
            session_id=context.session_id,
            source_targets=list(call.get("source_ids") or []),
            task=str(call.get("task") or ""),
            output_format=str(call.get("output_format") or "md"),
            output_title=str(call.get("output_title") or ""),
            structure=str(call.get("structure") or ""),
            style=str(call.get("style") or ""),
            fidelity=str(call.get("fidelity") or ""),
            content_markdown=str(call.get("content_markdown") or ""),
            table_rows=list(call.get("table_rows") or []),
            formatting=call.get("formatting") if isinstance(call.get("formatting"), dict) else {},
            send_to_user=bool(call.get("send_to_user")),
            timestamp=context.now_ts,
        )
        generated = result.get("generated") if isinstance(result, dict) else None
        events = []
        if isinstance(generated, dict):
            events.append(
                {
                    "type": "generated_file_ready",
                    "generated_file": generated,
                    "send_to_user": bool(result.get("send_to_user")),
                }
            )
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=events,
            followup_context=str(result.get("followup_context") or "") if isinstance(result, dict) else "",
        )

    def _normalize_sources(self, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            raw_items = [item.strip() for item in value.replace("，", ",").replace("、", ",").split(",")]
        elif isinstance(value, (list, tuple, set)):
            raw_items = list(value)
        else:
            raw_items = [value]
        sources: list[str] = []
        for item in raw_items:
            text = str(item or "").strip()
            if text and text not in sources:
                sources.append(text[:120])
        return sources[:20]

    def _normalize_output_format(self, value: Any) -> str:
        text = str(value or "md").strip().lower().lstrip(".")
        aliases = {
            "markdown": "md",
            "text": "txt",
            "plain": "txt",
            "word": "docx",
            "excel": "xlsx",
        }
        return aliases.get(text, text)[:16]

    def _normalize_table_rows(self, value: Any) -> list[list[str]]:
        if not isinstance(value, list):
            return []
        rows: list[list[str]] = []
        for row in value[:1000]:
            if not isinstance(row, (list, tuple)):
                continue
            cells = [str(cell or "").strip()[:500] for cell in list(row)[:50]]
            if any(cells):
                rows.append(cells)
        return rows

    def _normalize_formatting(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        allowed_top = {"header", "columns", "rows", "cells", "highlights", "paragraphs", "row_rules", "auto_width"}
        allowed_style = {
            "bold",
            "italic",
            "font_color",
            "fill_color",
            "highlight_color",
            "match_header",
            "header",
            "column",
            "letter",
            "index",
            "row",
            "row_index",
            "start",
            "end",
            "from",
            "to",
            "text",
            "contains",
            "paragraph_index",
            "where",
        }
        normalized: dict[str, Any] = {}
        for key, raw in value.items():
            if key not in allowed_top:
                continue
            if key == "auto_width":
                normalized[key] = self._coerce_bool(raw, default=True)
                continue
            if key == "header" and isinstance(raw, dict):
                normalized[key] = {str(k): v for k, v in raw.items() if str(k) in allowed_style}
                continue
            if not isinstance(raw, list):
                continue
            items = []
            for item in raw[:120]:
                if not isinstance(item, dict):
                    continue
                items.append({str(k): v for k, v in item.items() if str(k) in allowed_style})
            if items:
                normalized[key] = items
        return normalized

    def _coerce_bool(self, value: Any, *, default: bool) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "y", "on", "发送", "发给用户"}:
            return True
        if text in {"0", "false", "no", "n", "off", "不发送", "仅生成"}:
            return False
        return default


class ConvertMediaFileToolHandler(BaseToolHandler):
    tool_type = "convert_media_file"

    def __init__(self, *, generated_file_service) -> None:
        self.generated_file_service = generated_file_service

    def build_prompt_instruction(self) -> str:
        return (
            "- convert_media_file：当用户要把普通音频转成常见格式，或从普通视频文件里提取音频时使用。"
            "格式为 {\"type\":\"convert_media_file\",\"source_id\":\"file_001|audio_001|gen_001\","
            "\"output_format\":\"mp3|wav|flac|m4a|aac|ogg|opus\",\"output_title\":\"输出文件名\","
            "\"start_time\":\"00:00:35\",\"end_time\":\"00:01:20\",\"normalize_volume\":true,"
            "\"volume_gain_db\":6,\"trim_silence\":true,\"fade_in_seconds\":2,\"fade_out_seconds\":3,\"speed_ratio\":1.25,"
            "\"bitrate\":\"192k\",\"sample_rate\":44100,\"channels\":2,\"send_to_user\":true}。"
            "它适合普通非加密音频转码、压缩体积、截取片段、音量标准化、整体音量增减、自动去掉头尾静音、淡入淡出、调速、从 mp4/mov/mkv/webm 等视频提取音轨；不要用于 kgm/ncm/qmc 等平台加密或专有缓存格式的解密。"
            "start_time、end_time、normalize_volume、volume_gain_db、trim_silence、fade_in_seconds、fade_out_seconds、speed_ratio、bitrate、sample_rate、channels 都是可选项：用户没指定时不要硬填。"
            "如果只是转 mp3，通常只填 source_id、output_format、output_title 即可；如果是语音识别/统一语音规格，可考虑 wav、sample_rate=16000、channels=1；音乐文件通常保留原采样率和声道更自然。"
            "视频任务里只有用户要音频轨、后续人声处理、训练素材或统一媒体规格时才提音频；如果用户只要原视频，改用 send_file 发送原文件。"
            "如果用户说“声音忽大忽小/调正常/更舒服”，优先用 normalize_volume；如果用户说“太小声/放大一点”，用正数 volume_gain_db（如 3 或 6）；如果用户说“太吵/压低一点”，用负数 volume_gain_db（如 -3 或 -6）。"
            "如果用户说“把前后空白切掉/去掉开头结尾静音”，可填 trim_silence=true；如果用户说“截一段/加淡入淡出/放慢或加速”，再填写对应字段。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        if str(value.get("type") or "").strip() != self.tool_type:
            return None
        source_id = str(
            value.get("source_id")
            or value.get("source")
            or value.get("target")
            or value.get("attachment_id")
            or ""
        ).strip()
        if not source_id:
            return None
        return {
            "type": self.tool_type,
            "source_id": source_id[:120],
            "output_format": self._normalize_output_format(value.get("output_format") or value.get("format") or "mp3"),
            "output_title": str(value.get("output_title") or value.get("title") or "").strip()[:80],
            "bitrate": str(value.get("bitrate") or value.get("audio_bitrate") or "").strip()[:20],
            "sample_rate": self._coerce_int(value.get("sample_rate") or value.get("ar") or 0),
            "channels": self._coerce_int(value.get("channels") or value.get("channel") or value.get("ac") or 0),
            "start_time": str(value.get("start_time") or value.get("start") or value.get("ss") or "").strip()[:40],
            "end_time": str(value.get("end_time") or value.get("end") or value.get("to") or "").strip()[:40],
            "normalize_volume": self._coerce_bool(
                value.get("normalize_volume") or value.get("loudnorm") or value.get("normalize_audio"),
                default=False,
            ),
            "volume_gain_db": self._coerce_float(value.get("volume_gain_db") or value.get("gain_db") or value.get("volume_db") or 0),
            "trim_silence": self._coerce_bool(
                value.get("trim_silence")
                or value.get("remove_silence")
                or value.get("trim_silence_edges")
                or value.get("strip_silence"),
                default=False,
            ),
            "fade_in_seconds": value.get("fade_in_seconds") or value.get("fade_in") or 0,
            "fade_out_seconds": value.get("fade_out_seconds") or value.get("fade_out") or 0,
            "speed_ratio": self._coerce_float(value.get("speed_ratio") or value.get("speed") or value.get("atempo") or 0),
            "send_to_user": self._coerce_bool(value.get("send_to_user"), default=True),
        }

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        result = self.generated_file_service.convert_media_file(
            profile_user_id=context.profile_user_id,
            session_id=context.session_id,
            source_target=str(call.get("source_id") or ""),
            output_format=str(call.get("output_format") or "mp3"),
            output_title=str(call.get("output_title") or ""),
            bitrate=str(call.get("bitrate") or ""),
            sample_rate=int(call.get("sample_rate") or 0),
            channels=int(call.get("channels") or 0),
            start_time=str(call.get("start_time") or ""),
            end_time=str(call.get("end_time") or ""),
            normalize_volume=bool(call.get("normalize_volume")),
            volume_gain_db=call.get("volume_gain_db") or 0,
            trim_silence=bool(call.get("trim_silence")),
            fade_in_seconds=call.get("fade_in_seconds") or 0,
            fade_out_seconds=call.get("fade_out_seconds") or 0,
            speed_ratio=call.get("speed_ratio") or 0,
            send_to_user=bool(call.get("send_to_user")),
            timestamp=context.now_ts,
        )
        generated = result.get("generated") if isinstance(result, dict) else None
        events = []
        if isinstance(generated, dict):
            events.append(
                {
                    "type": "generated_file_ready",
                    "generated_file": generated,
                    "send_to_user": bool(result.get("send_to_user")),
                }
            )
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=events,
            followup_context=str(result.get("followup_context") or "") if isinstance(result, dict) else "",
        )

    def _normalize_output_format(self, value: Any) -> str:
        text = str(value or "mp3").strip().lower().lstrip(".")
        aliases = {
            "wave": "wav",
            "waveform": "wav",
            "mpeg3": "mp3",
            "mp4a": "m4a",
            "oga": "ogg",
        }
        return aliases.get(text, text)

    def _coerce_int(self, value: Any) -> int:
        try:
            return int(value or 0)
        except Exception:
            return 0

    def _coerce_float(self, value: Any) -> float:
        try:
            text = str(value or "").strip().lower()
            if not text:
                return 0.0
            text = (
                text.replace("倍速", "")
                .replace("倍", "")
                .replace("分贝", "db")
                .replace("x", "")
                .strip()
            )
            if text.endswith("db"):
                text = text[:-2].strip()
            if text.endswith("%"):
                return float(text[:-1]) / 100.0
            return float(text)
        except Exception:
            return 0.0

    def _coerce_bool(self, value: Any, *, default: bool) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "y", "on", "发送", "发给用户"}:
            return True
        if text in {"0", "false", "no", "n", "off", "不发送", "仅生成"}:
            return False
        return default


class SeparateAudioStemsToolHandler(BaseToolHandler):
    tool_type = "separate_audio_stems"

    def __init__(self, *, generated_file_service) -> None:
        self.generated_file_service = generated_file_service

    def build_prompt_instruction(self) -> str:
        return (
            "- separate_audio_stems：当用户想把一首歌、录音或带音轨视频拆成人声和伴奏两轨时使用。"
            "格式为 {\"type\":\"separate_audio_stems\",\"source_id\":\"file_001|audio_001|gen_001\","
            "\"mode\":\"vocals_instrumental\",\"output_format\":\"wav|flac|mp3\","
            "\"output_title\":\"输出标题\",\"send_to_user\":true}。"
            "当前只支持 vocals_instrumental，也就是分离出人声（vocals）和伴奏（instrumental）两份结果。"
            "这个工具负责拆轨，不负责后续精修；如果还要转码、裁剪、统一采样率、去头尾静音或调音量，请对分离后的结果再调用 convert_media_file。"
            "如果来源是普通视频文件，系统会先尝试抽取音轨再分离。不要用于 kgm/ncm/qmc 等平台加密或专有缓存格式的解密。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        if str(value.get("type") or "").strip() != self.tool_type:
            return None
        source_id = str(
            value.get("source_id")
            or value.get("source")
            or value.get("target")
            or value.get("attachment_id")
            or ""
        ).strip()
        if not source_id:
            return None
        return {
            "type": self.tool_type,
            "source_id": source_id[:120],
            "mode": self._normalize_mode(value.get("mode") or value.get("separation_mode") or "vocals_instrumental"),
            "output_format": self._normalize_output_format(value.get("output_format") or value.get("format") or "wav"),
            "output_title": str(value.get("output_title") or value.get("title") or "").strip()[:80],
            "send_to_user": self._coerce_bool(value.get("send_to_user"), default=True),
        }

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        result = self.generated_file_service.separate_audio_stems(
            profile_user_id=context.profile_user_id,
            session_id=context.session_id,
            source_target=str(call.get("source_id") or ""),
            mode=str(call.get("mode") or "vocals_instrumental"),
            output_format=str(call.get("output_format") or "wav"),
            output_title=str(call.get("output_title") or ""),
            send_to_user=bool(call.get("send_to_user")),
            timestamp=context.now_ts,
        )
        generated_files = result.get("generated_files") if isinstance(result, dict) else None
        events: list[dict[str, Any]] = []
        if isinstance(generated_files, list):
            for generated in generated_files:
                if not isinstance(generated, dict):
                    continue
                events.append(
                    {
                        "type": "generated_file_ready",
                        "generated_file": generated,
                        "send_to_user": bool(result.get("send_to_user")),
                    }
                )
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=events,
            followup_context=str(result.get("followup_context") or "") if isinstance(result, dict) else "",
        )

    def _normalize_mode(self, value: Any) -> str:
        text = str(value or "vocals_instrumental").strip().lower()
        aliases = {
            "vocals": "vocals_instrumental",
            "vocals+instrumental": "vocals_instrumental",
            "vocals_instrumental": "vocals_instrumental",
            "voice_music": "vocals_instrumental",
            "voice_and_music": "vocals_instrumental",
            "人声伴奏": "vocals_instrumental",
            "人声_伴奏": "vocals_instrumental",
        }
        return aliases.get(text, "vocals_instrumental")

    def _normalize_output_format(self, value: Any) -> str:
        text = str(value or "wav").strip().lower().lstrip(".")
        aliases = {
            "wave": "wav",
            "waveform": "wav",
            "mpeg3": "mp3",
        }
        return aliases.get(text, text)

    def _coerce_bool(self, value: Any, *, default: bool) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "y", "on", "发送", "发给用户"}:
            return True
        if text in {"0", "false", "no", "n", "off", "不发送", "仅生成"}:
            return False
        return default


class CleanVoiceTrackToolHandler(BaseToolHandler):
    tool_type = "clean_voice_track"

    def __init__(self, *, generated_file_service) -> None:
        self.generated_file_service = generated_file_service

    def build_prompt_instruction(self) -> str:
        return (
            "- clean_voice_track：当用户想把语音/人声再净化一下时使用，比如降噪、去混响、去回声、让说话更干净。"
            "格式为 {\"type\":\"clean_voice_track\",\"source_id\":\"file_001|audio_001|gen_001\","
            "\"mode\":\"denoise|dereverb|deecho|voice_focus\",\"quality\":\"auto|ai|basic\","
            "\"output_format\":\"wav|flac|mp3\",\"output_title\":\"输出标题\",\"post_filter\":false,\"send_to_user\":true}。"
            "它适合说话录音、直播片段、播客人声、分离后的人声轨；如果只是普通转码、裁剪、统一采样率、去头尾静音或调音量，请继续用 convert_media_file。"
            "quality=auto 会优先尝试本地 AI 语音净化模型（当前设计对接 DeepFilterNet），没装环境时再退回基础净化；quality=basic 表示直接走 ffmpeg 轻净化；quality=ai 表示只接受 AI 净化。"
            "mode 主要是意图提示：denoise 更偏降噪，dereverb/deecho 更偏混响与回声整理，voice_focus 更偏让人声主体更靠前。"
            "post_filter 只在 AI 净化时有意义，适合杂音更重的情况；用户没提时不要硬填。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        if str(value.get("type") or "").strip() != self.tool_type:
            return None
        source_id = str(
            value.get("source_id")
            or value.get("source")
            or value.get("target")
            or value.get("attachment_id")
            or ""
        ).strip()
        if not source_id:
            return None
        return {
            "type": self.tool_type,
            "source_id": source_id[:120],
            "mode": self._normalize_mode(value.get("mode") or value.get("clean_mode") or "denoise"),
            "quality": self._normalize_quality(value.get("quality") or value.get("backend") or "auto"),
            "output_format": self._normalize_output_format(value.get("output_format") or value.get("format") or "wav"),
            "output_title": str(value.get("output_title") or value.get("title") or "").strip()[:80],
            "post_filter": self._coerce_bool(value.get("post_filter") or value.get("pf"), default=False),
            "send_to_user": self._coerce_bool(value.get("send_to_user"), default=True),
        }

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        result = self.generated_file_service.clean_voice_track(
            profile_user_id=context.profile_user_id,
            session_id=context.session_id,
            source_target=str(call.get("source_id") or ""),
            mode=str(call.get("mode") or "denoise"),
            quality=str(call.get("quality") or "auto"),
            output_format=str(call.get("output_format") or "wav"),
            output_title=str(call.get("output_title") or ""),
            post_filter=bool(call.get("post_filter")),
            send_to_user=bool(call.get("send_to_user")),
            timestamp=context.now_ts,
        )
        generated = result.get("generated") if isinstance(result, dict) else None
        events = []
        if isinstance(generated, dict):
            events.append(
                {
                    "type": "generated_file_ready",
                    "generated_file": generated,
                    "send_to_user": bool(result.get("send_to_user")),
                }
            )
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=events,
            followup_context=str(result.get("followup_context") or "") if isinstance(result, dict) else "",
        )

    def _normalize_mode(self, value: Any) -> str:
        text = str(value or "denoise").strip().lower()
        aliases = {
            "denoise": "denoise",
            "noise": "denoise",
            "remove_noise": "denoise",
            "降噪": "denoise",
            "去噪": "denoise",
            "dereverb": "dereverb",
            "reverb": "dereverb",
            "去混响": "dereverb",
            "deecho": "deecho",
            "echo": "deecho",
            "去回声": "deecho",
            "voice_focus": "voice_focus",
            "speech": "voice_focus",
            "focus": "voice_focus",
            "人声聚焦": "voice_focus",
            "净化人声": "voice_focus",
        }
        return aliases.get(text, "denoise")

    def _normalize_quality(self, value: Any) -> str:
        text = str(value or "auto").strip().lower()
        aliases = {
            "auto": "auto",
            "默认": "auto",
            "ai": "ai",
            "model": "ai",
            "deepfilternet": "ai",
            "basic": "basic",
            "ffmpeg": "basic",
            "基础": "basic",
        }
        return aliases.get(text, "auto")

    def _normalize_output_format(self, value: Any) -> str:
        text = str(value or "wav").strip().lower().lstrip(".")
        aliases = {
            "wave": "wav",
            "waveform": "wav",
            "mpeg3": "mp3",
        }
        return aliases.get(text, text)

    def _coerce_bool(self, value: Any, *, default: bool) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "y", "on", "发送", "发给用户"}:
            return True
        if text in {"0", "false", "no", "n", "off", "不发送", "仅生成"}:
            return False
        return default


class TranscribeMediaToolHandler(BaseToolHandler):
    tool_type = "transcribe_media"

    def __init__(self, *, generated_file_service) -> None:
        self.generated_file_service = generated_file_service

    def build_prompt_instruction(self) -> str:
        return (
            "- transcribe_media：当用户要给音频/视频配文字稿、生成字幕、把录音转文字，或想总结视频/音频内容前先拿到转写稿时使用。"
            "格式为 {\"type\":\"transcribe_media\",\"source_ids\":[\"audio_001\",\"file_002\",\"gen_003\"],"
            "\"output_format\":\"md|txt|srt|vtt|json\",\"output_title\":\"转写稿标题\",\"language\":\"zh|en|auto\","
            "\"with_timestamps\":true,\"merge_outputs\":true,\"model_size\":\"small|medium|large-v3\","
            "\"vad_filter\":true,\"send_to_user\":true}。"
            "V1 支持批量来源：merge_outputs=true 会生成一份合并转写稿；merge_outputs=false 会每个来源各生成一份。"
            "如果用户要字幕文件，优先用 srt 或 vtt；如果要后续总结、会议纪要、内容梳理，优先用 md 并保留时间戳。"
            "音频较吵、歌曲伴奏很重或人声不清时，可先调用 separate_audio_stems / clean_voice_track，再对生成的人声结果调用 transcribe_media。"
            "这个工具负责转写，不负责总结；转写完成后如果用户要总结内容，再基于生成的转写稿继续用 compose_file。"
            "如果用户只要原视频/原音频，不要为了回复而转写；直接发送原文件即可。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        if str(value.get("type") or "").strip() != self.tool_type:
            return None
        source_ids = self._normalize_source_ids(
            value.get("source_ids")
            or value.get("sources")
            or value.get("targets")
            or value.get("source_id")
            or value.get("source")
            or value.get("target")
            or value.get("attachment_id")
        )
        if not source_ids:
            return None
        return {
            "type": self.tool_type,
            "source_ids": source_ids,
            "output_format": self._normalize_output_format(value.get("output_format") or value.get("format") or "md"),
            "output_title": str(value.get("output_title") or value.get("title") or "").strip()[:80],
            "language": self._normalize_language(value.get("language") or value.get("lang") or "zh"),
            "with_timestamps": self._coerce_bool(
                value.get("with_timestamps") if "with_timestamps" in value else value.get("timestamps"),
                default=True,
            ),
            "merge_outputs": self._coerce_bool(
                value.get("merge_outputs") if "merge_outputs" in value else value.get("merge"),
                default=True,
            ),
            "model_size": self._normalize_model_size(value.get("model_size") or value.get("model") or "small"),
            "device": self._normalize_device(value.get("device") or "auto"),
            "compute_type": self._normalize_compute_type(value.get("compute_type") or "auto"),
            "vad_filter": self._coerce_bool(
                value.get("vad_filter") if "vad_filter" in value else value.get("vad"),
                default=True,
            ),
            "send_to_user": self._coerce_bool(value.get("send_to_user"), default=True),
        }

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        result = self.generated_file_service.transcribe_media(
            profile_user_id=context.profile_user_id,
            session_id=context.session_id,
            source_targets=list(call.get("source_ids") or []),
            output_format=str(call.get("output_format") or "md"),
            output_title=str(call.get("output_title") or ""),
            language=str(call.get("language") or "zh"),
            with_timestamps=bool(call.get("with_timestamps", True)),
            merge_outputs=bool(call.get("merge_outputs", True)),
            model_size=str(call.get("model_size") or "small"),
            device=str(call.get("device") or "auto"),
            compute_type=str(call.get("compute_type") or "auto"),
            vad_filter=bool(call.get("vad_filter", True)),
            send_to_user=bool(call.get("send_to_user")),
            timestamp=context.now_ts,
        )
        generated_files = result.get("generated_files") if isinstance(result, dict) else None
        events: list[dict[str, Any]] = []
        if isinstance(generated_files, list):
            for generated in generated_files:
                if isinstance(generated, dict):
                    events.append(
                        {
                            "type": "generated_file_ready",
                            "generated_file": generated,
                            "send_to_user": bool(result.get("send_to_user")),
                        }
                    )
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=events,
            followup_context=str(result.get("followup_context") or "") if isinstance(result, dict) else "",
        )

    def _normalize_source_ids(self, value: Any) -> list[str]:
        raw_items: list[Any]
        if isinstance(value, str):
            raw_items = value.replace("，", ",").replace("、", ",").split(",")
        elif isinstance(value, (list, tuple, set)):
            raw_items = list(value)
        else:
            raw_items = [value]
        normalized: list[str] = []
        for item in raw_items:
            text = str(item or "").strip()
            if text and text not in normalized:
                normalized.append(text[:120])
        return normalized[:20]

    def _normalize_output_format(self, value: Any) -> str:
        text = str(value or "md").strip().lower().lstrip(".")
        aliases = {
            "markdown": "md",
            "text": "txt",
            "plain": "txt",
            "subtitle": "srt",
            "subtitles": "srt",
            "caption": "srt",
            "captions": "srt",
            "webvtt": "vtt",
        }
        normalized = aliases.get(text, text)
        return normalized if normalized in {"md", "txt", "srt", "vtt", "json"} else "md"

    def _normalize_language(self, value: Any) -> str:
        text = str(value or "zh").strip().lower()
        aliases = {
            "中文": "zh",
            "普通话": "zh",
            "国语": "zh",
            "英文": "en",
            "自动": "auto",
            "detect": "auto",
        }
        normalized = aliases.get(text, text)
        return normalized if normalized in {"zh", "en", "ja", "ko", "auto"} else "zh"

    def _normalize_model_size(self, value: Any) -> str:
        text = str(value or "small").strip().lower().replace("_", "-")
        aliases = {
            "tiny": "tiny",
            "base": "base",
            "small": "small",
            "medium": "medium",
            "large": "large-v3",
            "large-v3": "large-v3",
            "large-v2": "large-v2",
        }
        return aliases.get(text, "small")

    def _normalize_device(self, value: Any) -> str:
        text = str(value or "auto").strip().lower()
        return text if text in {"auto", "cuda", "cpu"} else "auto"

    def _normalize_compute_type(self, value: Any) -> str:
        text = str(value or "auto").strip().lower()
        return text if text in {"auto", "float16", "float32", "int8", "int8_float16"} else "auto"

    def _coerce_bool(self, value: Any, *, default: bool) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "y", "on", "发送", "发给用户", "是", "需要", "合并"}:
            return True
        if text in {"0", "false", "no", "n", "off", "不发送", "仅生成", "否", "不需要", "分开"}:
            return False
        return default


class PrepareVoiceDatasetToolHandler(BaseToolHandler):
    tool_type = "prepare_voice_dataset"

    def __init__(self, *, generated_file_service) -> None:
        self.generated_file_service = generated_file_service

    def build_prompt_instruction(self) -> str:
        return (
            "- prepare_voice_dataset：当用户要把一段或多段人声/语音整理成 GPT-SoVITS、RVC 等训练素材时使用。"
            "格式为 {\"type\":\"prepare_voice_dataset\",\"source_ids\":[\"gen_001\",\"audio_001\"],"
            "\"profile\":\"gpt_sovits|rvc|archive\",\"output_title\":\"训练集名称\","
            "\"target_sr\":44100,\"min_clip_seconds\":3,\"max_clip_seconds\":12,"
            "\"silence_threshold_db\":-40,\"min_silence_ms\":300,\"max_silence_kept_ms\":300,"
            "\"clean_first\":false,\"normalize_volume\":false,\"send_to_user\":true}。"
            "这个工具会把多个来源统一成训练用 wav、按停顿切片、生成 manifest.json 和 zip 批次；摘要会列出过短、过长、音量偏低、可能爆音等片段文件名，方便后续和用户一起筛。"
            "它适合处理已经分离/净化后的人声轨，也可以直接处理普通语音音频或带音轨视频；如果用户还没做人声分离/净化，且需要更干净素材，可先调用 separate_audio_stems 或 clean_voice_track。"
            "训练素材任务可以分多步组合：必要时先 convert_media_file 提音频，再 separate_audio_stems 拿人声，再 clean_voice_track 降噪，最后 prepare_voice_dataset 切片打包；不要把这些步骤用于只要原文件的请求。"
            "用户没指定细节时，profile=gpt_sovits 就够了，不要硬填一堆参数。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        if str(value.get("type") or "").strip() != self.tool_type:
            return None
        source_ids = self._normalize_source_ids(
            value.get("source_ids")
            or value.get("sources")
            or value.get("targets")
            or value.get("source_id")
            or value.get("source")
            or value.get("target")
            or value.get("attachment_id")
        )
        if not source_ids:
            return None
        return {
            "type": self.tool_type,
            "source_ids": source_ids,
            "profile": self._normalize_profile(value.get("profile") or value.get("preset") or "gpt_sovits"),
            "output_title": str(value.get("output_title") or value.get("title") or "").strip()[:80],
            "target_sr": self._coerce_int(value.get("target_sr") or value.get("sample_rate") or 0),
            "mono": self._coerce_bool(value.get("mono"), default=True),
            "min_clip_seconds": self._coerce_float(value.get("min_clip_seconds") or value.get("min_seconds") or 0),
            "max_clip_seconds": self._coerce_float(value.get("max_clip_seconds") or value.get("max_seconds") or 0),
            "silence_threshold_db": self._coerce_float_or_none(value.get("silence_threshold_db") or value.get("threshold_db")),
            "min_silence_ms": self._coerce_int(value.get("min_silence_ms") or value.get("min_interval_ms") or 0),
            "max_silence_kept_ms": self._coerce_int(value.get("max_silence_kept_ms") or value.get("max_sil_kept_ms") or 0),
            "clean_first": self._coerce_bool(value.get("clean_first") or value.get("light_clean"), default=False),
            "normalize_volume": self._coerce_bool(value.get("normalize_volume") or value.get("loudnorm"), default=False),
            "send_to_user": self._coerce_bool(value.get("send_to_user"), default=True),
        }

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        result = self.generated_file_service.prepare_voice_dataset(
            profile_user_id=context.profile_user_id,
            session_id=context.session_id,
            source_targets=list(call.get("source_ids") or []),
            profile=str(call.get("profile") or "gpt_sovits"),
            output_title=str(call.get("output_title") or ""),
            target_sr=int(call.get("target_sr") or 0),
            mono=bool(call.get("mono", True)),
            min_clip_seconds=call.get("min_clip_seconds") or 0,
            max_clip_seconds=call.get("max_clip_seconds") or 0,
            silence_threshold_db=call.get("silence_threshold_db"),
            min_silence_ms=call.get("min_silence_ms") or 0,
            max_silence_kept_ms=call.get("max_silence_kept_ms") or 0,
            clean_first=bool(call.get("clean_first")),
            normalize_volume=bool(call.get("normalize_volume")),
            send_to_user=bool(call.get("send_to_user")),
            timestamp=context.now_ts,
        )
        generated = result.get("generated") if isinstance(result, dict) else None
        events = []
        if isinstance(generated, dict):
            events.append(
                {
                    "type": "generated_file_ready",
                    "generated_file": generated,
                    "send_to_user": bool(result.get("send_to_user")),
                }
            )
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=events,
            followup_context=str(result.get("followup_context") or "") if isinstance(result, dict) else "",
        )

    def _normalize_source_ids(self, value: Any) -> list[str]:
        raw_items: list[Any]
        if isinstance(value, str):
            raw_items = value.replace("，", ",").replace("、", ",").split(",")
        elif isinstance(value, (list, tuple, set)):
            raw_items = list(value)
        else:
            raw_items = [value]
        normalized: list[str] = []
        for item in raw_items:
            text = str(item or "").strip()
            if text and text not in normalized:
                normalized.append(text[:120])
        return normalized[:20]

    def _normalize_profile(self, value: Any) -> str:
        text = str(value or "gpt_sovits").strip().lower().replace("-", "_").replace(" ", "_")
        aliases = {
            "gptsovits": "gpt_sovits",
            "gpt_sovits": "gpt_sovits",
            "sovits": "gpt_sovits",
            "rvc": "rvc",
            "archive": "archive",
            "归档": "archive",
        }
        return aliases.get(text, "gpt_sovits")

    def _coerce_int(self, value: Any) -> int:
        try:
            return int(float(str(value or "").strip()))
        except Exception:
            return 0

    def _coerce_float(self, value: Any) -> float:
        try:
            return float(str(value or "").strip())
        except Exception:
            return 0.0

    def _coerce_float_or_none(self, value: Any) -> float | None:
        if value is None:
            return None
        text = str(value or "").strip()
        if not text:
            return None
        try:
            return float(text)
        except Exception:
            return None

    def _coerce_bool(self, value: Any, *, default: bool) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "y", "on", "发送", "发给用户", "是", "需要"}:
            return True
        if text in {"0", "false", "no", "n", "off", "不发送", "仅生成", "否", "不需要"}:
            return False
        return default


class InspectMediaInfoToolHandler(BaseToolHandler):
    tool_type = "inspect_media_info"

    def __init__(self, *, generated_file_service) -> None:
        self.generated_file_service = generated_file_service

    def build_prompt_instruction(self) -> str:
        return (
            "- inspect_media_info：当用户问音频/视频的时长、编码、采样率、声道、码率、分辨率、帧率、是否有音轨，"
            "或你在转换/压缩/截取前需要先看媒体规格时使用。"
            "格式为 {\"type\":\"inspect_media_info\",\"source_id\":\"file_001|audio_001|gen_001\"}。"
            "这个工具只读取媒体信息，不生成新文件；读取结果会告诉你真实规格，之后如果要处理文件再调用 convert_media_file。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        if str(value.get("type") or "").strip() != self.tool_type:
            return None
        source_id = str(
            value.get("source_id")
            or value.get("source")
            or value.get("target")
            or value.get("attachment_id")
            or ""
        ).strip()
        if not source_id:
            return None
        return {
            "type": self.tool_type,
            "source_id": source_id[:120],
        }

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        result = self.generated_file_service.inspect_media_info(
            profile_user_id=context.profile_user_id,
            session_id=context.session_id,
            source_target=str(call.get("source_id") or ""),
            timestamp=context.now_ts,
        )
        media_info = result.get("media_info") if isinstance(result, dict) else None
        events = []
        if isinstance(media_info, dict):
            events.append(
                {
                    "type": "media_info_inspected",
                    "source_id": str(call.get("source_id") or ""),
                    "media_info": media_info,
                }
            )
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=events,
            followup_context=str(result.get("followup_context") or "") if isinstance(result, dict) else "",
        )


class ReviseGeneratedFileToolHandler(BaseToolHandler):
    tool_type = "revise_generated_file"

    def __init__(self, *, generated_file_service) -> None:
        self.generated_file_service = generated_file_service

    def build_prompt_instruction(self) -> str:
        return (
            "- revise_generated_file：当用户要修改你刚生成的 gen_001/gen_002 文件时使用，默认生成新版本，不覆盖旧文件。"
            "格式为 {\"type\":\"revise_generated_file\",\"target\":\"gen_001\","
            "\"instruction\":\"用户要求怎么改\",\"output_format\":\"md|txt|docx|xlsx|pdf|json|csv|html\","
            "\"output_title\":\"修改版标题\",\"content_markdown\":\"修改后的完整正文或 Markdown\","
            "\"table_rows\":[[\"列1\",\"列2\"],[\"内容1\",\"内容2\"]],"
            "\"formatting\":{\"rows\":[{\"index\":2,\"fill_color\":\"yellow\"}]},\"send_to_user\":true}。"
            "这个工具不会替你理解“删第二段、加总结”；你需要根据生成文件工作台里的预览先整理出修改后的最终内容，"
            "再把最终内容写进 content_markdown 或 table_rows。"
            "如果只是调整颜色、加粗或高亮，把明确样式规则写进 formatting。"
            "如果用户只是要求继续改文件，优先用这个工具；如果是从原始附件重新整理一份新文件，用 compose_file。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        if str(value.get("type") or "").strip() != self.tool_type:
            return None
        table_rows = self._normalize_table_rows(value.get("table_rows") or value.get("rows") or value.get("table"))
        return {
            "type": self.tool_type,
            "target": str(value.get("target") or value.get("generated_id") or value.get("file_id") or "latest").strip()[:120],
            "instruction": str(value.get("instruction") or value.get("task") or value.get("request") or "").strip()[:500],
            "output_format": self._normalize_output_format(value.get("output_format") or value.get("format") or ""),
            "output_title": str(value.get("output_title") or value.get("title") or value.get("name") or "").strip()[:80],
            "content_markdown": str(
                value.get("content_markdown")
                or value.get("markdown")
                or value.get("content")
                or value.get("body")
                or ""
            ).strip()[:80000],
            "table_rows": table_rows,
            "formatting": ComposeFileToolHandler._normalize_formatting(self, value.get("formatting") or value.get("styles") or value.get("style_rules")),
            "send_to_user": self._coerce_bool(value.get("send_to_user"), default=True),
        }

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        result = self.generated_file_service.revise_generated_file(
            profile_user_id=context.profile_user_id,
            session_id=context.session_id,
            target=str(call.get("target") or "latest"),
            instruction=str(call.get("instruction") or ""),
            output_format=str(call.get("output_format") or ""),
            output_title=str(call.get("output_title") or ""),
            content_markdown=str(call.get("content_markdown") or ""),
            table_rows=list(call.get("table_rows") or []),
            formatting=call.get("formatting") if isinstance(call.get("formatting"), dict) else {},
            send_to_user=bool(call.get("send_to_user")),
            timestamp=context.now_ts,
        )
        generated = result.get("generated") if isinstance(result, dict) else None
        events = []
        if isinstance(generated, dict):
            events.append(
                {
                    "type": "generated_file_ready",
                    "generated_file": generated,
                    "send_to_user": bool(result.get("send_to_user")),
                }
            )
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=events,
            followup_context=str(result.get("followup_context") or "") if isinstance(result, dict) else "",
        )

    def _normalize_output_format(self, value: Any) -> str:
        text = str(value or "").strip().lower().lstrip(".")
        aliases = {
            "markdown": "md",
            "text": "txt",
            "plain": "txt",
            "word": "docx",
            "excel": "xlsx",
        }
        return aliases.get(text, text)[:16]

    def _normalize_table_rows(self, value: Any) -> list[list[str]]:
        if not isinstance(value, list):
            return []
        rows: list[list[str]] = []
        for row in value[:1000]:
            if not isinstance(row, (list, tuple)):
                continue
            cells = [str(cell or "").strip()[:500] for cell in list(row)[:50]]
            if any(cells):
                rows.append(cells)
        return rows

    def _coerce_bool(self, value: Any, *, default: bool) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "y", "on", "发送", "发给用户"}:
            return True
        if text in {"0", "false", "no", "n", "off", "不发送", "仅生成"}:
            return False
        return default


class ApplyStyleToExistingFileToolHandler(BaseToolHandler):
    tool_type = "apply_style_to_existing_file"

    def __init__(self, *, generated_file_service) -> None:
        self.generated_file_service = generated_file_service

    def build_prompt_instruction(self) -> str:
        return (
            "- apply_style_to_existing_file：当用户只要求给已有 docx/xlsx 文件套样式，而不是重写全文时使用。"
            "格式为 {\"type\":\"apply_style_to_existing_file\",\"target\":\"file_001|gen_001|最近\","
            "\"target_type\":\"attachment|generated\",\"instruction\":\"用户的样式要求\","
            "\"output_title\":\"样式版标题\","
            "\"formatting\":{\"header\":{\"bold\":true},\"columns\":[{\"match_header\":\"姓名\",\"font_color\":\"red\"}],"
            "\"rows\":[{\"index\":2,\"fill_color\":\"yellow\"}],"
            "\"row_rules\":[{\"where\":{\"column\":\"分数\",\"lt\":60},\"font_color\":\"red\"}],"
            "\"highlights\":[{\"text\":\"重点\",\"fill_color\":\"yellow\"}]},\"send_to_user\":true}。"
            "适合“把姓名列标红”“低于60分整行标红”“重点高亮”这类操作；"
            "它会复制原文件并套样式，不需要你把大表格或整篇 Word 重新输出。"
            "如果用户要增删改正文内容，用 revise_generated_file；如果要从附件整理成新文件，用 compose_file。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        if str(value.get("type") or "").strip() != self.tool_type:
            return None
        return {
            "type": self.tool_type,
            "target": str(value.get("target") or value.get("source_id") or value.get("file_id") or "latest").strip()[:120],
            "target_type": self._normalize_target_type(value.get("target_type") or value.get("source_type")),
            "instruction": str(value.get("instruction") or value.get("task") or value.get("request") or "").strip()[:500],
            "output_title": str(value.get("output_title") or value.get("title") or value.get("name") or "").strip()[:80],
            "formatting": ComposeFileToolHandler._normalize_formatting(self, value.get("formatting") or value.get("styles") or value.get("style_rules")),
            "send_to_user": self._coerce_bool(value.get("send_to_user"), default=True),
        }

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        result = self.generated_file_service.apply_style_to_existing_file(
            profile_user_id=context.profile_user_id,
            session_id=context.session_id,
            target=str(call.get("target") or "latest"),
            target_type=str(call.get("target_type") or ""),
            instruction=str(call.get("instruction") or ""),
            output_title=str(call.get("output_title") or ""),
            formatting=call.get("formatting") if isinstance(call.get("formatting"), dict) else {},
            send_to_user=bool(call.get("send_to_user")),
            timestamp=context.now_ts,
        )
        generated = result.get("generated") if isinstance(result, dict) else None
        events = []
        if isinstance(generated, dict):
            events.append(
                {
                    "type": "generated_file_ready",
                    "generated_file": generated,
                    "send_to_user": bool(result.get("send_to_user")),
                }
            )
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=events,
            followup_context=str(result.get("followup_context") or "") if isinstance(result, dict) else "",
        )

    def _normalize_target_type(self, value: Any) -> str:
        text = str(value or "").strip().lower()
        if text in {"attachment", "inbox", "file", "qq_file"}:
            return "attachment"
        if text in {"generated", "gen", "generated_file"}:
            return "generated"
        return ""

    def _coerce_bool(self, value: Any, *, default: bool) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "y", "on", "发送", "发给用户"}:
            return True
        if text in {"0", "false", "no", "n", "off", "不发送", "仅生成"}:
            return False
        return default


class SendFileToolHandler(BaseToolHandler):
    tool_type = "send_file"

    def __init__(self, *, generated_file_service) -> None:
        self.generated_file_service = generated_file_service

    def build_prompt_instruction(self) -> str:
        return (
            "- send_file：当用户要你发送已有文件时使用，可发送临时附件 file_001/img_001/audio_001，"
            "也可发送生成物 gen_001/gen_002。"
            "格式为 {\"type\":\"send_file\",\"targets\":[\"file_001\",\"gen_001\"]}，单个文件也可以用 target。"
            "适合“把刚才那个视频发我”“把原视频和转写稿都发我”“再发一次 gen_002”。"
            "它只发送已有文件，不修改、不转码、不重新生成；如果用户要求修改内容、换格式或重新整理，应使用对应生成/转换工具。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        if str(value.get("type") or "").strip() != self.tool_type:
            return None
        targets_value = (
            value.get("targets")
            if value.get("targets") is not None
            else value.get("target")
            if value.get("target") is not None
            else value.get("generated_id")
            if value.get("generated_id") is not None
            else value.get("file_id")
        )
        targets = ComposeFileToolHandler._normalize_sources(self, targets_value)
        return {
            "type": self.tool_type,
            "target": targets[0] if targets else "latest",
            "targets": targets,
        }

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        result = self.generated_file_service.send_file(
            profile_user_id=context.profile_user_id,
            session_id=context.session_id,
            target=str(call.get("target") or "latest"),
            targets=list(call.get("targets") or []),
            timestamp=context.now_ts,
        )
        events = []
        files = result.get("files") if isinstance(result, dict) else None
        if bool(result.get("ok")) and isinstance(files, list):
            for file_ref in files:
                if not isinstance(file_ref, dict):
                    continue
                events.append(
                    {
                        "type": "file_ready",
                        "file": file_ref,
                        "send_to_user": True,
                    }
                )
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=events,
            followup_context=str(result.get("followup_context") or "") if isinstance(result, dict) else "",
        )


class SendGeneratedFileToolHandler(SendFileToolHandler):
    tool_type = "send_generated_file"

    def build_prompt_instruction(self) -> str:
        return (
            "- send_generated_file：兼容旧格式；当用户要重新发送已生成的 gen_001 文件时可用。"
            "优先使用 send_file；只有需要兼容旧调用时才使用本工具。"
            "格式为 {\"type\":\"send_generated_file\",\"targets\":[\"gen_001\",\"gen_002\"]}。"
        )

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        result = self.generated_file_service.send_generated_file(
            profile_user_id=context.profile_user_id,
            session_id=context.session_id,
            target=str(call.get("target") or "latest"),
            targets=list(call.get("targets") or []),
            timestamp=context.now_ts,
        )
        events = []
        generated_files = result.get("generated_files") if isinstance(result, dict) else None
        if bool(result.get("ok")) and isinstance(generated_files, list):
            for generated in generated_files:
                if not isinstance(generated, dict):
                    continue
                events.append(
                    {
                        "type": "generated_file_ready",
                        "generated_file": generated,
                        "send_to_user": True,
                    }
                )
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=events,
            followup_context=str(result.get("followup_context") or "") if isinstance(result, dict) else "",
        )


class SendStickerToolHandler(BaseToolHandler):
    tool_type = "send_sticker"

    def __init__(self, *, sticker_service) -> None:
        self.sticker_service = sticker_service

    def build_prompt_instruction(self) -> str:
        sticker_list = self.sticker_service.build_prompt_list()
        return (
            "- send_sticker：当你想给用户发送当前可用表情包图片时使用。"
            f"可用表情：{sticker_list or '（当前没有可用表情）'}。"
            "格式为 {\"type\":\"send_sticker\",\"sticker\":\"biexiao|haoxingfu|tanshou|turan_chuxian|wainao|zaoba|zhuangsha|zhuangsi\"}。"
            "它只负责发表情包，不生成文件、不修改附件；适合开心、吐槽、装傻、装死、突然冒泡等轻量情绪回应。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        if str(value.get("type") or "").strip() != self.tool_type:
            return None
        target = (
            value.get("sticker")
            if value.get("sticker") is not None
            else value.get("sticker_id")
            if value.get("sticker_id") is not None
            else value.get("name")
            if value.get("name") is not None
            else value.get("label")
        )
        sticker = str(target or "").strip()
        if not sticker:
            return None
        return {
            "type": self.tool_type,
            "sticker": sticker[:80],
        }

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        target = str(call.get("sticker") or "").strip()
        resolution = self.sticker_service.resolve(target)
        if not resolution.ok or not isinstance(resolution.sticker, dict):
            candidates = list(resolution.candidates or [])
            if candidates:
                candidate_text = "、".join(
                    f"{item.get('id')}({item.get('display_name')})" for item in candidates[:8]
                )
                return ToolExecutionResult(
                    tool_type=self.tool_type,
                    followup_context=(
                        f"你刚刚想发送表情包“{target}”，但匹配到多个候选：{candidate_text}。"
                        "请让用户确认具体要哪一个，或改用准确 sticker id。"
                    ),
                )
            return ToolExecutionResult(
                tool_type=self.tool_type,
                followup_context=(
                    f"你刚刚想发送表情包“{target}”，但没有找到对应资源。"
                    f"当前可用：{self.sticker_service.build_prompt_list() or '无'}。"
                    "请自然告诉用户可以换一个表情名。"
                ),
            )

        sticker = dict(resolution.sticker)
        if not bool(sticker.get("exists")):
            return ToolExecutionResult(
                tool_type=self.tool_type,
                followup_context=(
                    f"你找到了表情包“{sticker.get('display_name') or target}”，"
                    "但本地 PNG 文件不存在，暂时发不出去。请自然告诉用户资源文件缺失。"
                ),
            )

        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=[
                {
                    "type": "sticker_ready",
                    "sticker": {
                        "id": sticker.get("id"),
                        "display_name": sticker.get("display_name"),
                        "absolute_path": sticker.get("absolute_path"),
                        "public_path": sticker.get("public_path"),
                    },
                    "send_to_user": True,
                }
            ],
            followup_context=(
                f"你刚刚已经选择发送表情包“{sticker.get('display_name') or target}”。"
                "请用很短的一句话自然衔接，不要描述文件路径。"
            ),
        )


class InspectGeneratedFileToolHandler(BaseToolHandler):
    tool_type = "inspect_generated_file"

    def __init__(self, *, generated_file_service) -> None:
        self.generated_file_service = generated_file_service

    def build_prompt_instruction(self) -> str:
        return (
            "- inspect_generated_file：当你需要回头查看自己生成过的 gen_001 文件正文、结尾、zip 清单或 manifest 时使用。"
            "格式为 {\"type\":\"inspect_generated_file\",\"target\":\"gen_001|最近|文件标题\","
            "\"section\":\"content|head|tail|summary|file_list|manifest|file:manifest.json\",\"max_chars\":12000}。"
            "它只读取生成物，不会发送、修改或删除文件；适合继续修改前先确认内容、查看转写稿、检查训练集 zip 的 manifest/README。"
            "如果只是要把文件再发给用户，用 send_file；如果要修改内容，用 revise_generated_file。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        if str(value.get("type") or "").strip() != self.tool_type:
            return None
        target = (
            value.get("target")
            if value.get("target") is not None
            else value.get("generated_id")
            if value.get("generated_id") is not None
            else value.get("file_id")
            if value.get("file_id") is not None
            else "latest"
        )
        section = str(value.get("section") or value.get("part") or value.get("member") or "content").strip()
        max_chars = self._normalize_max_chars(value.get("max_chars") or value.get("limit"))
        return {
            "type": self.tool_type,
            "target": str(target or "latest").strip()[:120] or "latest",
            "section": section[:260] or "content",
            "max_chars": max_chars,
        }

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        result = self.generated_file_service.inspect_generated_file(
            profile_user_id=context.profile_user_id,
            session_id=context.session_id,
            target=str(call.get("target") or "latest"),
            section=str(call.get("section") or "content"),
            max_chars=int(call.get("max_chars") or 12000),
        )
        events = []
        if bool(result.get("ok")):
            events.append(
                {
                    "type": "generated_file_inspected",
                    "generated_file": result.get("generated"),
                    "inspection": result.get("inspection"),
                }
            )
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=events,
            followup_context=str(result.get("followup_context") or "") if isinstance(result, dict) else "",
        )

    def _normalize_max_chars(self, value: Any) -> int:
        try:
            parsed = int(float(str(value).strip()))
        except Exception:
            parsed = 12000
        return max(500, min(40000, parsed))


class ManageGeneratedFileToolHandler(BaseToolHandler):
    tool_type = "manage_generated_file"

    def __init__(self, *, generated_file_service) -> None:
        self.generated_file_service = generated_file_service

    def build_prompt_instruction(self) -> str:
        return (
            "- manage_generated_file：当用户要清理、隐藏或删除你生成过的文件时使用，只管理 gen_001 这类生成物。"
            "格式为 {\"type\":\"manage_generated_file\",\"action\":\"archive|delete|purge\","
            "\"targets\":[\"gen_001\",\"gen_002\"],\"reason\":\"清理原因\"}。"
            "archive 只从生成文件工作台隐藏；delete 会同时删除本地生成文件；purge 会删除本地文件并清空生成物内容卡片。"
            "不要用它清理用户发来的 file_001/img_001，临时附件应使用 clear_attachment_focus。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        if str(value.get("type") or "").strip() != self.tool_type:
            return None
        action = self._normalize_action(value.get("action") or value.get("operation"))
        if not action:
            return None
        targets_value = (
            value.get("targets")
            if value.get("targets") is not None
            else value.get("target")
            if value.get("target") is not None
            else value.get("generated_id")
            if value.get("generated_id") is not None
            else value.get("file_id")
        )
        targets = ComposeFileToolHandler._normalize_sources(self, targets_value)
        return {
            "type": self.tool_type,
            "action": action,
            "targets": targets or ["latest"],
            "reason": str(value.get("reason") or value.get("why") or "").strip()[:200],
        }

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        result = self.generated_file_service.manage_generated_files(
            profile_user_id=context.profile_user_id,
            session_id=context.session_id,
            action=str(call.get("action") or ""),
            targets=list(call.get("targets") or []),
            reason=str(call.get("reason") or ""),
            timestamp=context.now_ts,
        )
        events = []
        if bool(result.get("ok")):
            events.append(
                {
                    "type": "generated_files_managed",
                    "action": str(result.get("action") or ""),
                    "managed": list(result.get("managed") or []),
                    "unresolved": list(result.get("unresolved") or []),
                }
            )
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=events,
            followup_context=str(result.get("followup_context") or "") if isinstance(result, dict) else "",
        )

    def _normalize_action(self, value: Any) -> str:
        action = str(value or "").strip().lower()
        aliases = {
            "hide": "archive",
            "archive": "archive",
            "remove": "archive",
            "clear": "archive",
            "收起": "archive",
            "归档": "archive",
            "隐藏": "archive",
            "delete": "delete",
            "unlink": "delete",
            "删除": "delete",
            "删掉": "delete",
            "purge": "purge",
            "destroy": "purge",
            "彻底删除": "purge",
            "彻底清理": "purge",
        }
        return aliases.get(action, "")


class ManageTaskWorkspaceToolHandler(BaseToolHandler):
    tool_type = "manage_task_workspace"

    def __init__(self, *, task_workspace_service: TaskWorkspaceService) -> None:
        self.task_workspace_service = task_workspace_service

    def build_prompt_instruction(self) -> str:
        return (
            "- manage_task_workspace：当一件事明显需要多步跟踪、产物登记、等待用户确认或事后清理工作记忆时使用。"
            "不要为一句话能完成的小事创建任务；创建/更新任务工作区不等于执行任务，"
            "如果下一步已经明确，应继续调用真正的处理工具（如 compose_file、convert_media_file、transcribe_media），不要只向用户汇报计划。"
            "当用户问“好了没/现在到哪了/还在跑吗”时，可以 inspect 最近任务并基于任务工作区简短说明进度；不要新建任务。"
            "格式为 {\"type\":\"manage_task_workspace\",\"action\":\"create|update_steps|add_artifact|ask_user|complete|cleanup|inspect\","
            "\"task_id\":\"可选；省略时默认处理最近的未完成任务\",\"goal\":\"任务目标\","
            "\"steps\":[{\"id\":\"step_1\",\"title\":\"步骤\",\"status\":\"queued|running|done|failed|waiting_user\"}],"
            "\"artifacts\":[{\"id\":\"gen_001\",\"kind\":\"md\",\"title\":\"产物名\"}],"
            "\"question\":\"需要问用户的问题\",\"reason\":\"原因\"}。"
            "create 用于建立任务白板；update_steps 更新步骤；add_artifact 记录生成物或素材；"
            "ask_user 表示任务卡住需要主人决定；complete 标记完成；cleanup 清理这次任务的工作记忆。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        if str(value.get("type") or "").strip() != self.tool_type:
            return None
        action = self._normalize_action(value.get("action") or value.get("operation"))
        if not action:
            return None

        goal = str(
            value.get("goal")
            or value.get("normalized_goal")
            or value.get("task")
            or value.get("title")
            or ""
        ).strip()
        raw_request = str(
            value.get("raw_request")
            or value.get("user_request")
            or value.get("request")
            or goal
            or ""
        ).strip()
        question = str(
            value.get("question")
            or value.get("pending_question")
            or value.get("ask")
            or ""
        ).strip()
        return {
            "type": self.tool_type,
            "action": action,
            "task_id": str(value.get("task_id") or value.get("id") or "").strip()[:96],
            "goal": goal[:400],
            "raw_request": raw_request[:500],
            "success_criteria": self._normalize_text_list(
                value.get("success_criteria") or value.get("criteria") or value.get("acceptance")
            ),
            "constraints": self._normalize_text_list(value.get("constraints") or value.get("rules")),
            "steps": self._normalize_steps(value.get("steps") or value.get("step")),
            "artifacts": self._normalize_artifacts(value.get("artifacts") or value.get("artifact")),
            "question": question[:300],
            "reason": str(value.get("reason") or value.get("why") or "").strip()[:300],
            "metadata": self._normalize_dict(value.get("metadata")),
        }

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        action = str(call.get("action") or "").strip().lower()
        if action == "create":
            return self._execute_create(call=call, context=context)

        task = self._resolve_task(call=call, context=context)
        if task is None:
            return ToolExecutionResult(
                tool_type=self.tool_type,
                followup_context=(
                    "你刚刚想管理任务工作区，但当前没有找到明确的任务。"
                    "如果这是新的多步任务，请先调用 manage_task_workspace 的 create 动作；不要重复执行当前动作。"
                ),
            )

        if action == "inspect":
            return self._result_for_task(
                action=action,
                task=task,
                followup=self._build_inspect_followup(task),
                event_type="task_workspace_inspected",
            )

        if action == "update_steps":
            steps = list(call.get("steps") or [])
            if not steps:
                return ToolExecutionResult(
                    tool_type=self.tool_type,
                    followup_context="你刚刚想更新任务步骤，但没有给出 steps。请自然确认下一步，不要重复调用空的 update_steps。",
                )
            status = str(call.get("status") or "").strip() or self._derive_task_status_from_steps(steps)
            updated = self.task_workspace_service.update_task(
                task_id=str(task["task_id"]),
                status=status,
                steps=steps,
                metadata=self._merge_metadata(task, call),
                timestamp=context.now_ts,
            )
            self.task_workspace_service.append_event(
                task_id=str(task["task_id"]),
                event_type="steps_updated",
                from_actor="frontstage",
                message=str(call.get("reason") or "更新任务步骤。"),
                payload={"steps": steps},
                status="handled",
                timestamp=context.now_ts,
            )
            return self._result_for_task(
                action=action,
                task=updated or task,
                followup=f"任务工作区已更新步骤：{self._task_label(updated or task)}。请基于这个既成事实继续推进，不要重复调用 update_steps。",
                event_type="task_workspace_updated",
            )

        if action == "add_artifact":
            artifacts = self._merge_artifacts(task, list(call.get("artifacts") or []))
            if not artifacts:
                return ToolExecutionResult(
                    tool_type=self.tool_type,
                    followup_context="你刚刚想登记任务产物，但没有给出 artifacts。请自然确认产物编号，不要重复调用空的 add_artifact。",
                )
            updated = self.task_workspace_service.update_task(
                task_id=str(task["task_id"]),
                artifacts=artifacts,
                metadata=self._merge_metadata(task, call),
                timestamp=context.now_ts,
            )
            self.task_workspace_service.append_event(
                task_id=str(task["task_id"]),
                event_type="artifact_added",
                from_actor="frontstage",
                message=str(call.get("reason") or "登记任务产物。"),
                payload={"artifacts": list(call.get("artifacts") or [])},
                status="handled",
                timestamp=context.now_ts,
            )
            return self._result_for_task(
                action=action,
                task=updated or task,
                followup=f"任务工作区已登记产物：{self._task_label(updated or task)}。之后可以继续引用这些产物，不要重复登记同一批产物。",
                event_type="task_workspace_artifact_added",
            )

        if action == "ask_user":
            question = str(call.get("question") or "").strip()
            if not question:
                return ToolExecutionResult(
                    tool_type=self.tool_type,
                    followup_context="你刚刚想向用户确认，但 question 为空。请直接自然追问，不要重复调用 ask_user。",
                )
            pending_question = {
                "text": question,
                "reason": str(call.get("reason") or "").strip(),
                "asked_at": int(context.now_ts),
            }
            updated = self.task_workspace_service.update_task(
                task_id=str(task["task_id"]),
                status="waiting_user",
                pending_question=pending_question,
                metadata=self._merge_metadata(task, call),
                timestamp=context.now_ts,
            )
            self.task_workspace_service.append_event(
                task_id=str(task["task_id"]),
                event_type="user_question",
                from_actor="frontstage",
                priority="high",
                requires_user=True,
                message=question,
                payload=pending_question,
                timestamp=context.now_ts,
            )
            return self._result_for_task(
                action=action,
                task=updated or task,
                followup=(
                    f"任务工作区已记录一个需要问主人的问题：{question} "
                    "请现在直接把这个问题自然问出来，等用户回答后再继续任务。"
                ),
                event_type="task_workspace_question",
            )

        if action == "complete":
            artifacts = self._merge_artifacts(task, list(call.get("artifacts") or []))
            updated = self.task_workspace_service.complete_task(
                task_id=str(task["task_id"]),
                artifacts=artifacts,
                message=str(call.get("reason") or "任务完成。"),
                timestamp=context.now_ts,
            )
            return self._result_for_task(
                action=action,
                task=updated or task,
                followup=f"任务工作区已标记完成：{self._task_label(updated or task)}。请自然告诉用户任务已经完成，不要重复调用 complete。",
                event_type="task_workspace_completed",
            )

        if action == "cleanup":
            updated = self.task_workspace_service.cleanup_task(
                task_id=str(task["task_id"]),
                mode=str(call.get("mode") or "clean_scratch"),
                reason=str(call.get("reason") or ""),
                timestamp=context.now_ts,
            )
            return self._result_for_task(
                action=action,
                task=updated or task,
                followup=f"任务工作区已清理：{self._task_label(updated or task)}。这是既成事实，请自然回应，不要重复调用 cleanup。",
                event_type="task_workspace_cleaned",
            )

        return ToolExecutionResult(
            tool_type=self.tool_type,
            followup_context="你刚刚想管理任务工作区，但动作不受支持。请自然继续对话，不要重复调用 manage_task_workspace。",
        )

    def _execute_create(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        goal = str(call.get("goal") or "").strip()
        raw_request = str(call.get("raw_request") or goal or "").strip()
        if not goal and not raw_request:
            return ToolExecutionResult(
                tool_type=self.tool_type,
                followup_context="你刚刚想创建任务工作区，但目标为空。请自然确认用户要完成什么，不要重复调用空的 create。",
            )
        task = self.task_workspace_service.create_task(
            profile_user_id=context.profile_user_id,
            session_id=context.session_id,
            raw_request_text=raw_request or goal,
            source_message_id=context.current_user_source_id,
            normalized_goal=goal or raw_request,
            success_criteria=list(call.get("success_criteria") or []),
            constraints=list(call.get("constraints") or []),
            steps=list(call.get("steps") or []),
            artifacts=list(call.get("artifacts") or []),
            metadata=self._normalize_dict(call.get("metadata")),
            owner="frontstage",
            status="running" if list(call.get("steps") or []) else "queued",
            timestamp=context.now_ts,
        )
        return self._result_for_task(
            action="create",
            task=task,
            followup=(
                f"任务工作区已创建：{self._task_label(task)}。"
                "后续多步工具结果可以继续登记到这个 task_id；如果任务很简单，不需要向用户解释内部编号。"
            ),
            event_type="task_workspace_created",
        )

    def _resolve_task(self, *, call: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any] | None:
        task_id = str(call.get("task_id") or "").strip()
        if task_id:
            return self.task_workspace_service.get_task(task_id)
        candidates = self.task_workspace_service.list_tasks(
            profile_user_id=context.profile_user_id,
            session_id=context.session_id,
            statuses=["running", "waiting_user", "queued"],
            limit=1,
        )
        if candidates:
            return candidates[0]
        completed = self.task_workspace_service.list_tasks(
            profile_user_id=context.profile_user_id,
            session_id=context.session_id,
            statuses=["completed"],
            limit=1,
        )
        return completed[0] if completed else None

    def _result_for_task(
        self,
        *,
        action: str,
        task: dict[str, Any],
        followup: str,
        event_type: str,
    ) -> ToolExecutionResult:
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=[
                {
                    "type": event_type,
                    "action": action,
                    "task": self._compact_task(task),
                }
            ],
            followup_context=followup,
            state_updates={
                "task_workspace_changed": action != "inspect",
                "task_workspace_action": action,
                "task_id": str(task.get("task_id") or ""),
            },
        )

    def _build_inspect_followup(self, task: dict[str, Any]) -> str:
        steps = task.get("steps") if isinstance(task.get("steps"), list) else []
        artifacts = task.get("artifacts") if isinstance(task.get("artifacts"), list) else []
        pending_question = task.get("pending_question") if isinstance(task.get("pending_question"), dict) else {}
        metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
        workshop = metadata.get("workshop") if isinstance(metadata.get("workshop"), dict) else {}
        recent_events = self.task_workspace_service.list_events(task_id=str(task.get("task_id") or ""), limit=50)
        lines = [
            f"你刚刚查看了任务工作区：{self._task_label(task)}。",
            f"状态：{task.get('status')}",
            f"步骤数：{len(steps)}",
            f"产物数：{len(artifacts)}",
        ]
        assigned_agent = str(workshop.get("assigned_agent") or "").strip()
        workshop_status = str(workshop.get("status") or "").strip()
        if assigned_agent or workshop_status:
            lines.append(f"后台工坊：{assigned_agent or '未指定'} / {workshop_status or 'unknown'}")
        if steps:
            rendered_steps = []
            for step in steps[:4]:
                if not isinstance(step, dict):
                    continue
                title = str(step.get("title") or step.get("name") or step.get("id") or "未命名步骤").strip()
                status = str(step.get("status") or "queued").strip()
                if title:
                    rendered_steps.append(f"{title}({status})")
            if rendered_steps:
                lines.append("当前步骤：" + "；".join(rendered_steps))
        if artifacts:
            rendered_artifacts = []
            for artifact in artifacts[:4]:
                if not isinstance(artifact, dict):
                    continue
                artifact_id = str(artifact.get("id") or "").strip()
                title = str(artifact.get("title") or "").strip()
                if artifact_id or title:
                    rendered_artifacts.append(artifact_id or title)
            if rendered_artifacts:
                lines.append("当前产物：" + "；".join(rendered_artifacts))
        handoff = self.task_workspace_service.get_task_handoff(task)
        if handoff:
            lines.extend(self.task_workspace_service.render_handoff_lines(handoff, bullet=""))
        frontstage_lines = self.task_workspace_service.render_frontstage_status_lines(task, handoff=handoff, bullet="")
        if frontstage_lines:
            lines.extend(frontstage_lines)
        if pending_question.get("text"):
            lines.append(f"待确认问题：{pending_question.get('text')}")
        if recent_events:
            latest = recent_events[-1]
            latest_type = str(latest.get("event_type") or "").strip()
            latest_message = str(latest.get("message") or "").strip()
            if latest_type or latest_message:
                lines.append(f"最近事件：{latest_type or 'event'}: {latest_message[:160]}")
        lines.append("请根据用户是否正在询问任务进展，决定是否自然说明；不要重复调用 inspect。")
        return "\n".join(lines)

    def _task_label(self, task: dict[str, Any]) -> str:
        task_id = str(task.get("task_id") or "").strip()
        goal = str(task.get("normalized_goal") or "").strip()
        if goal:
            return f"{goal} (id:{task_id})"
        return f"id:{task_id}"

    def _compact_task(self, task: dict[str, Any]) -> dict[str, Any]:
        return {
            "task_id": str(task.get("task_id") or ""),
            "status": str(task.get("status") or ""),
            "goal": str(task.get("normalized_goal") or ""),
            "steps_count": len(task.get("steps") or []),
            "artifacts_count": len(task.get("artifacts") or []),
        }

    def _derive_task_status_from_steps(self, steps: list[dict[str, Any]]) -> str:
        statuses = {str(step.get("status") or "").strip().lower() for step in steps if isinstance(step, dict)}
        if "waiting_user" in statuses:
            return "waiting_user"
        if "running" in statuses:
            return "running"
        if statuses and statuses.issubset({"done", "completed"}):
            return "completed"
        return "running"

    def _merge_metadata(self, task: dict[str, Any], call: dict[str, Any]) -> dict[str, Any]:
        metadata = dict(task.get("metadata") or {})
        extra = call.get("metadata")
        if isinstance(extra, dict):
            metadata.update(extra)
        return metadata

    def _merge_artifacts(self, task: dict[str, Any], new_artifacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        seen: set[str] = set()
        for artifact in list(task.get("artifacts") or []) + list(new_artifacts or []):
            if not isinstance(artifact, dict):
                continue
            normalized = self._normalize_artifact(artifact)
            artifact_id = str(normalized.get("id") or normalized.get("handle") or normalized.get("title") or "").strip()
            if not artifact_id:
                artifact_id = repr(sorted(normalized.items()))
            if artifact_id in seen:
                continue
            seen.add(artifact_id)
            merged.append(normalized)
        return merged[:100]

    def _normalize_action(self, value: Any) -> str:
        action = str(value or "").strip().lower()
        aliases = {
            "create": "create",
            "new": "create",
            "start": "create",
            "update": "update_steps",
            "update_steps": "update_steps",
            "steps": "update_steps",
            "progress": "update_steps",
            "add_artifact": "add_artifact",
            "artifact": "add_artifact",
            "record_artifact": "add_artifact",
            "ask": "ask_user",
            "ask_user": "ask_user",
            "question": "ask_user",
            "complete": "complete",
            "finish": "complete",
            "done": "complete",
            "cleanup": "cleanup",
            "clean": "cleanup",
            "clear": "cleanup",
            "inspect": "inspect",
            "list": "inspect",
            "view": "inspect",
        }
        return aliases.get(action, "")

    def _normalize_steps(self, value: Any) -> list[dict[str, Any]]:
        raw_steps = value if isinstance(value, list) else [value] if value else []
        steps: list[dict[str, Any]] = []
        for index, item in enumerate(raw_steps, start=1):
            if isinstance(item, str):
                step = {"id": f"step_{index}", "title": item.strip(), "status": "queued"}
            elif isinstance(item, dict):
                step = {
                    "id": str(item.get("id") or item.get("step_id") or f"step_{index}").strip()[:48],
                    "title": str(item.get("title") or item.get("name") or item.get("description") or "").strip()[:160],
                    "status": self._normalize_step_status(item.get("status")),
                }
                note = str(item.get("note") or item.get("result") or "").strip()[:220]
                if note:
                    step["note"] = note
                owner = str(item.get("owner") or item.get("agent") or "").strip()[:48]
                if owner:
                    step["owner"] = owner
            else:
                continue
            if step.get("title"):
                steps.append(step)
            if len(steps) >= 30:
                break
        return steps

    def _normalize_step_status(self, value: Any) -> str:
        status = str(value or "").strip().lower()
        aliases = {
            "todo": "queued",
            "pending": "queued",
            "working": "running",
            "doing": "running",
            "done": "done",
            "completed": "done",
            "ok": "done",
            "error": "failed",
            "wait": "waiting_user",
            "waiting": "waiting_user",
        }
        status = aliases.get(status, status)
        if status in {"queued", "running", "done", "failed", "waiting_user"}:
            return status
        return "queued"

    def _normalize_artifacts(self, value: Any) -> list[dict[str, Any]]:
        raw_artifacts = value if isinstance(value, list) else [value] if value else []
        artifacts: list[dict[str, Any]] = []
        for item in raw_artifacts:
            if isinstance(item, str):
                normalized = {"id": item.strip()}
            elif isinstance(item, dict):
                normalized = self._normalize_artifact(item)
            else:
                continue
            if normalized.get("id") or normalized.get("title"):
                artifacts.append(normalized)
            if len(artifacts) >= 50:
                break
        return artifacts

    def _normalize_artifact(self, item: dict[str, Any]) -> dict[str, Any]:
        artifact_id = str(
            item.get("id")
            or item.get("artifact_id")
            or item.get("generated_id")
            or item.get("handle")
            or item.get("source_id")
            or ""
        ).strip()[:96]
        artifact = {
            "id": artifact_id,
            "kind": str(item.get("kind") or item.get("type") or item.get("format") or "").strip()[:40],
            "title": str(item.get("title") or item.get("name") or "").strip()[:120],
        }
        status = str(item.get("status") or "").strip()[:40]
        if status:
            artifact["status"] = status
        note = str(item.get("note") or item.get("summary") or "").strip()[:220]
        if note:
            artifact["note"] = note
        return {key: value for key, value in artifact.items() if value}

    def _normalize_text_list(self, value: Any) -> list[str]:
        if isinstance(value, str):
            raw_items = re.split(r"[\n;；|]+", value)
        elif isinstance(value, (list, tuple, set)):
            raw_items = list(value)
        else:
            raw_items = []
        normalized: list[str] = []
        for item in raw_items:
            text = str(item or "").strip()
            if text and text not in normalized:
                normalized.append(text[:160])
            if len(normalized) >= 12:
                break
        return normalized

    def _normalize_dict(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            clean_key = str(key or "").strip()[:48]
            if clean_key:
                normalized[clean_key] = item
        return normalized


class ManageGiftToolHandler(BaseToolHandler):
    tool_type = "manage_gift"

    def __init__(self, *, gift_service, observe_image_fn=None) -> None:
        self.gift_service = gift_service
        self.observe_image_fn = observe_image_fn

    def build_prompt_instruction(self) -> str:
        return (
            "- manage_gift：当用户已经明确表示要怎么处理某份礼物时使用。"
            "格式为 {\"type\":\"manage_gift\",\"action\":\"observe|keep|internalize|defer|reject|remove|purge\",\"asset_id\":\"可选\"}。"
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

        display_name = str(updated.get("display_name") or updated.get("origin_name") or "这份礼物").strip() or "这份礼物"
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
            "格式为 {\"type\":\"manage_artifact\",\"action\":\"claim|rename|move|delete\",\"asset_id\":\"可选\","
            "\"display_name\":\"正式名字\",\"collection_key\":\"稳定英文id\",\"collection_name\":\"中文集合名\","
            "\"asset_role\":\"scene|outfit|expression|portrait|album_photo\",\"placement_hint\":\"可选位置提示\"}。"
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
            "display_name": str(value.get("display_name") or value.get("name") or value.get("title") or "").strip()[:80],
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
        display_name = str(updated.get("display_name") or updated.get("origin_name") or "这份资产").strip() or "这份资产"
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
            followup_context += " 它已经不再显示在你的收藏或资源里；如果用户要彻底删除文件，应该再明确确认后使用 manage_gift 的 purge。"
        else:
            followup_context += " 请基于这个既成事实自然回应，不要重复调用 manage_artifact。"

        projection_changed = (
            str(updated.get("asset_type") or "").strip().lower() == "image"
            and (
                str(payload.get("projection_role") or "").strip().lower() == "scene"
                or action in {"claim", "rename", "move", "delete"}
                or str(updated.get("status") or "").strip().lower() == "internalized"
            )
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
            "格式为 {\"type\":\"manage_persona\",\"action\":\"create|update|inspect|archive|delete\","
            "\"card_id\":\"可选\",\"name\":\"名字\",\"summary\":\"核心摘要\","
            "\"speech_style\":\"说话方式\",\"interaction_bias\":\"互动倾向\","
            "\"resource_preference\":\"场景/BGM/服装偏好\",\"switch_hint\":\"适合进入的氛围\","
            "\"unsuitable_contexts\":\"不擅长应对的情景\",\"reason\":\"为什么这样做\"}。"
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
                value.get("unsuitable_contexts")
                or value.get("weakness")
                or value.get("not_good_at")
                or ""
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
                        f"新的表达侧面「{card['name']}」(id:{card['card_id']}) 已形成。"
                        "接下来让回应自然贴合这张卡。"
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
                    f"你刚刚想管理人设卡，但信息还不够完整：{exc}。"
                    "请自然继续对话，不要重复调用 manage_persona。"
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
                "active_persona_id": str(card.get("card_id") or "") if str(card.get("status") or "") == "active" else "",
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
