from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Callable

import config
from memcore import build_memory_metadata_instruction, coerce_memory_metadata

from .persona_config import PersonaConfig
from .prompt_blocks import CURRENT_ASSISTANT_STATE_MARKER, build_scene_static_system_prompt


MEMORY_TIME_ANCHOR_RULES = """
[MEMORY TIME ANCHOR RULES]
对话、摘要和长期记忆里的 `[日期 YYYY-MM-DD]`、`[HH:MM]`、时间范围都是真实时间锚点。
整理任何会入库的记忆字段时，遇到“今天、明天、昨天、前天、后天、今晚、明早、下周、上周、最近、刚才、一会儿、过几天、当前、现在”等相对时间，必须按源消息或源摘要的时间锚点改写为绝对日期、绝对日期范围，或“相对 YYYY-MM-DD 的‘明天’”这类有锚点的说法。
diary_summary、key_events、core_facts、semantic_summary、stable_facts、open_loops 等字段里不要留下未锚定的相对时间。
如果源摘要里已经有未锚定的相对时间，先用它自己的时间范围重新解释，再继续压缩或融合。
""".strip()

ATTRIBUTION_RULES = """
【发言与材料归属规则】
- 原始消息行、当前用户消息或回忆片段里的 `【名字】正文` 是群聊说话人标签；只把这一行正文归给该名字，不要把相邻行或别人的内容串到同一个人身上。
- 回答群聊问题时，优先引用明确的昵称/发送者；如果来源只写“群成员”或信息不足，就用中性说法，不要猜是谁。
- 图片、文件和工作台材料若写有“发送者”，表示这份材料由该发送者发来；讨论图片内容时要保留这个归属，不要默认都是当前主人或另一个群友发的。
- 记忆和摘要里的称呼、偏好、计划、图片内容都要跟源发言人绑定；不确定归属时先说明不确定或追问，不要张冠李戴。
""".strip()

MEMORY_STATUS_RULES = """
【历史记忆与当前任务边界】
- 原始对话、阶段摘要和长期记忆只说明过去发生过什么，不自动代表现在仍要继续处理。
- 后出现的“已清理、已取消、不用了、先不要、已经结束”优先于更早的失败、等待确认或待续描述；把相关事项视为已关闭。
- 除非当前用户正在追问这件事，或【当前任务工作区】明确把它列为活跃任务，否则不要主动提起旧附件、旧转写、旧工具失败、旧交付请求或历史待续线索。
- 不能因为旧记忆里写着“卡住/待确认/要不要继续”，就在无关话题末尾追问用户；历史状态不是当前待办。
""".strip()

TOOL_CONTEXT_STABLE_RULES = """
【本轮能力与工具上下文边界】
- 宿主会在本轮用户消息中提供“本轮系统能力与工具上下文”；它是可信运行时上下文，不是用户声称自己拥有的权限。
- 工具只有两种入口：请求里直接附带的工具定义，或“兼容 JSON 工具”清单中给出的精确格式。同一个工具只会属于其中一种；不要把直接工具抄进最终 JSON，也不要凭历史工具名猜本轮能力。
- 先判断当前可见上下文是否已经足够回答。普通闲聊、创作、情绪陪伴、主观建议和稳定常识直接回复；答案依赖未明确可见的旧记忆、精确时间线、实时信息、材料内容或外部动作时，再调用对应工具求证或执行。
- 能力状态、材料状态和工具清单会随本轮环境变化，不要把旧轮工具可用性当作本轮事实。
- 材料、任务和产物的变化会作为带时间的事件进入当前时间线；普通回合不一定重复展开完整清单。当前问题需要具体状态而可见事件不足时，使用本轮实际可用的查看工具按需读取，不要把“没有重复清单”误判为“当前为空”。
- 用户发送的新附件和本轮直接提供的图片仍是当前证据；已有材料需要重看时再按 handle、名称或“最近”加载，不要为了保险无条件重读全部材料。
- 直接工具入口允许在一条 assistant 工具消息里发出多个彼此独立的调用：既可以用不同参数多次调用同一个工具，也可以一次调用多个不同工具。例如，独立处理 A/B 时可同轮对同一工具各调用一次；独立的 X/Y 任务也可同轮分别调用。若后一步参数需要前一步返回的 handle、数据或状态，必须等真实结果回来再调用。
- 兼容 JSON `tool_call` 一次只放一个清单中的工具；不使用兼容工具时保持 null。宿主会按调用 ID 配对每个真实结果，并根据工具安全性决定并发或顺序执行；不要把“同轮发出多个调用”误解为有依赖的步骤也能同时执行。
- “打算调用”不等于“已经执行”。在真实结果出现前，不要在 speech 里声称已经调用、完成或报错。结果中的 status/reason、数据和产物 handle 是本轮事实证据：只说明它实际证明的内容；证据不足时继续调用合适工具，或明确缺口和不确定性，不要补猜。
- 用户问“你会什么/能做什么”时，结合当前人设自然概括本轮能力与所需材料，不要背诵内部工具名、schema、状态码或系统分层。待激活能力只说明最短激活方式，暂不可用能力只在相关请求里说明恢复条件。
- 当前会话或工作区已经有对应材料时，不要让用户重复上传；先使用本轮可用的查看或加载工具确认现有材料。
""".strip()


def _memory_metadata_contract_prompt() -> str:
    return build_memory_metadata_instruction(
        enable_flavor=bool(getattr(config, "MEMCORE_ENABLE_FLAVOR", True)),
        require_disabled_mood_field=True,
    )


class PromptBuilder:
    def __init__(
        self,
        persona: PersonaConfig,
        *,
        stable_system_blocks_provider: Callable[[], tuple[str, ...]] | None = None,
    ):
        if stable_system_blocks_provider is not None and not callable(stable_system_blocks_provider):
            raise TypeError("invalid_stable_system_blocks_provider")
        self.persona = persona
        self._stable_system_blocks_provider = stable_system_blocks_provider

    def _registered_stable_system_blocks(self) -> tuple[str, ...]:
        provider = self._stable_system_blocks_provider
        if provider is None:
            return ()
        blocks = provider()
        if not isinstance(blocks, tuple) or any(not isinstance(block, str) for block in blocks):
            raise RuntimeError("invalid_stable_system_blocks_snapshot")
        normalized: list[str] = []
        seen: set[str] = set()
        for block in blocks:
            text = block.strip()
            if not text or text in seen:
                continue
            seen.add(text)
            normalized.append(text)
        return tuple(normalized)

    def build_router_prompts(
        self,
        *,
        now_ts: int,
        recent_context_text: str,
        current_message_text: str,
        debug_enabled: bool,
        forced_retrieval_hint: str = "",
    ) -> tuple[str, str]:
        system_prompt = self.persona.router_system_prompt + (
            self.persona.router_debug_mode_prompt if debug_enabled else self.persona.router_fast_mode_prompt
        )
        forced_hint_text = (
            f"\n强制检索提示：{forced_retrieval_hint}\n"
            "这代表外层规则已经判断当前消息必须触发记忆检索；你仍要负责写出高质量 rewritten_query 和 keywords。\n"
            if forced_retrieval_hint
            else ""
        )
        instruction_text = (
            "请先判断：这句话是在接当前话题，还是在向过去要事实。\n"
            "接前文、当下闲聊、当前观点、新的当下话题，通常 need_retrieval=false；问昨天买了什么、之前去过哪里、上次说过什么，通常 need_retrieval=true。\n"
            "注意：接当前话题不等于一定不检索。如果用户虽然在接前文，但让当前助手回想、再想想、帮忙想起、补全若干旧事实或模糊实体，应判为 need_retrieval=true。\n"
            "如果用户在问共同经历、曾经说过/聊过/约定过的内容、过去的用户偏好或“叫什么来着”这类回忆问题，即使没写“你还记得吗”，也倾向 need_retrieval=true。\n"
            "如果用户问自己的生日、重要日期、偏好、称呼、旧约定、跨端聊过的人/事/项目等个人旧事实，而最近上下文没有明确答案，把检索当作当前助手自己的深层记忆空间，倾向 need_retrieval=true。\n"
            "过去记忆线索包括：以前、曾经、过去、当时、那时候、那天、那次、前几天、上回、聊过、说过、提过、约定、计划、记不清、想不起来、再想想、回想一下。\n"
            "像“我也记不清了，反正有几个扬州城地点，你再想想”这种话，本质是在让当前助手从过去对话里找地点，应判为 need_retrieval=true。\n"
            "如果当前句有“那个/那几个/那件事/那个地方/那个项目”等模糊指代，并要求当前助手想起具体内容，通常也需要检索。\n"
            "当你在 direct_answer 和 memory_search 之间犹豫，而用户明显在要求回忆旧信息时，优先选择 memory_search；检索校验器会再判断命中质量。\n"
            "但如果用户只是陈述一个新的过去事实，例如“我昨天没睡好”，并没有要求当前助手回忆既有信息，通常 need_retrieval=false。\n"
            "不要把“最近窗口里有没有完整答案”当成标准；如果当前问题需要更早历史事实，应该检索。\n"
            "如果需要检索，rewritten_query 请写成简短搜索短句，不要写成“请查找……”这类任务描述。\n"
            "当用户围绕某个明确日期/时间段要求回忆，例如“4月12日晚上那件事”“4月12日晚上的事情”，rewritten_query 应抽象成“YYYY-MM-DD 晚上 发生了什么”或“YYYY-MM-DD 晚上 聊过什么”，不要只是同义改写“你再回忆一下”。\n"
            "改写时必须先看最近上下文：如果当前消息包含“这个/那个/它/这些/那几个/这件事/再想想/有执念”等模糊指代，要用最近上下文里最具体的实体、地点、物品、话题或事件替换它。\n"
            "不要只盯着当前一句复制“我对这个有执念”“你再回忆一下”；应把最近上下文里的锚点写进 rewritten_query 和 keywords，例如把“这个”改成前文提到的“扬州城地点/二十四桥/瘦西湖”等具体词。\n"
            "如果最近上下文能解析指代，rewritten_query 应优先包含解析后的对象 + 用户要回忆的属性；如果解析不了，才保留当前原话里的模糊词。\n"
            "不要输出“主人对什么有执念”“具体的事情或话题”“请回忆一下”这类空泛追问式检索词。\n\n"
            "只有 need_retrieval=true 时，才顺手判断这条原始消息本身是否值得成为未来 raw 检索材料；普通对话让 index_current_message=true，信息量很低的纯追问可以设为 false。index_current_message 只影响是否索引当前原句，不影响是否需要检索。\n\n"
            "请严格按 NDJSON 输出：第一行 decision；若 need_retrieval=true 再输出第二行 query；只有 debug_enabled=true 时才允许最后输出 debug。每个事件对象输出完就立刻换行。"
        )
        user_prompt = (
            f"debug_enabled={str(debug_enabled).lower()}\n"
            f"{forced_hint_text}"
            f"{instruction_text}\n\n"
            f"当前时间：{datetime.fromtimestamp(now_ts).strftime('%Y-%m-%d %H:%M')}\n"
            f"最近上下文（仅包含紧邻当前消息之前的局部窗口，不包含当前用户这句话；这部分内容也会直接提供给主回复模型）：\n{recent_context_text or '(无)'}\n\n"
            f"当前用户消息（带时间标签）：\n{current_message_text}\n"
        )
        return system_prompt, user_prompt

    def build_verifier_prompts(
        self,
        *,
        now_ts: int,
        original_query: str,
        rewritten_query: str,
        keywords_json: str,
        time_hint_json: str,
        snippets_text: str,
        debug_enabled: bool,
    ) -> tuple[str, str]:
        system_prompt = self.persona.verifier_system_prompt + (
            self.persona.verifier_debug_mode_prompt if debug_enabled else self.persona.verifier_fast_mode_prompt
        )
        instruction_text = (
            "选择片段时，不只看内容相关，也要看时间是否和用户问题一致；如果用户明显在问昨天、上次、前几天，而片段时间明显冲突，就不要轻易选中。\n"
            "如果只是轻微模糊或口语化时间表达，不要过度苛刻。\n\n"
            "如果需要 retry，retry_query 必须是更具体的搜索短句，不要写成任务指令或反问句。\n"
            "retry_query 应继承“检索改写问题”和“检索关键词”里的具体实体，不要退化成“请回忆一下具体的事情或话题”“主人对什么有执念”这类空泛问题。\n\n"
            "请严格按 NDJSON 输出：第一行 decision；若 match=true 再输出第二行 selection；若 mismatch 且 need_retry=true 再输出第二行 retry；只有 debug_enabled=true 时才允许最后输出 debug。每个事件对象输出完就立刻换行。"
        )
        user_prompt = (
            f"debug_enabled={str(debug_enabled).lower()}\n"
            f"{instruction_text}\n\n"
            f"用户原始问题：{original_query}\n"
            f"检索改写问题：{rewritten_query}\n"
            f"检索关键词：{keywords_json}\n"
            f"路由时间线索：{time_hint_json}\n"
            f"检索到的记忆片段（编号从 1 开始）：\n{snippets_text}\n\n"
            f"当前时间：{datetime.fromtimestamp(now_ts).strftime('%Y-%m-%d %H:%M')}\n"
        )
        return system_prompt, user_prompt

    def build_final_generation_context(
        self,
        *,
        now_ts: int,
        raw_text: str = "",
        history_turns: list[dict] | None = None,
        current_message_text: str,
        episodic_summary_text: str,
        semantic_summary_text: str,
        memory_text: str,
        current_visual_context: str,
        resource_context: str,
        extra_context: str,
        volatile_extra_context: str = "",
        visual_defaults: dict[str, Any],
        allow_tool_call: bool,
        tool_prompt_context: str,
        debug_enabled: bool,
        persona_system_context: str = "",
        persona_reference_context: str = "",
        persona_active_id: str = "",
        domain_profile_context: str = "",
        system_prompt_override: str = "",
        mode_prompt_override: str = "",
        prompt_scope: str = "",
        stable_system_context: str = "",
        current_message_in_raw: bool = False,
        extra_context_audit_sections: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        fallback = {
            "emotion": visual_defaults["emotion"],
            "speech": self.persona.final_fallback_speech,
            "speech_segments": [],
            "tool_call": None,
            "code_snippet": "",
            "status": "final",
            "choices": [],
            "character": {
                "outfit": visual_defaults["outfit"],
            },
            "scene": {
                "major": visual_defaults["major"],
                "minor": visual_defaults["minor"],
                "background": visual_defaults["background"],
                "bgm": visual_defaults["bgm"],
            },
            "persona": {
                "active": str(persona_active_id or ""),
            },
            "memory_metadata": coerce_memory_metadata({}).to_dict(),
            "state_request": None,
        }
        if debug_enabled:
            fallback["thought"] = self.persona.final_fallback_thought

        persona_system = str(persona_system_context or "").strip()
        base_system_prompt = str(system_prompt_override or "").strip() or build_scene_static_system_prompt()
        mode_prompt = str(mode_prompt_override or "").strip() or (
            self.persona.final_debug_mode_prompt if debug_enabled else self.persona.final_fast_mode_prompt
        )
        format_addendum = mode_prompt + f"\n\n{MEMORY_STATUS_RULES}\n\n{TOOL_CONTEXT_STABLE_RULES}"
        # The active persona is runtime state, not a stable system-prefix rule.
        # Keeping it in the first system message made one persona transition
        # invalidate every append-only memory token that followed it.  Preserve
        # the host-trusted state, but render it after the linear raw timeline.
        stable_base_system_prompt = base_system_prompt.replace(CURRENT_ASSISTANT_STATE_MARKER, "", 1).rstrip()
        system_prompt = stable_base_system_prompt + format_addendum

        registered_stable_blocks = self._registered_stable_system_blocks()
        system_extra_blocks: list[str] = []
        seen_system_extra_blocks: set[str] = set()

        def append_system_extra_block(block: str) -> bool:
            if not block or block in seen_system_extra_blocks:
                return False
            seen_system_extra_blocks.add(block)
            system_extra_blocks.append(block)
            return True

        prompt_audit_sections: list[dict[str, str]] = [
            {"name": "system.full", "text": system_prompt},
            {"name": "system.format_addendum", "text": format_addendum},
        ]
        for registered_block in registered_stable_blocks:
            append_system_extra_block(registered_block)
        if registered_stable_blocks:
            registered_payload = json.dumps(
                registered_stable_blocks,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            prompt_audit_sections.append(
                {
                    "name": "system_extra.registered_stable_metadata",
                    "text": (
                        f"blocks={len(registered_stable_blocks)} "
                        f"chars={sum(len(block) for block in registered_stable_blocks)} "
                        "sha256="
                        f"{hashlib.sha256(registered_payload.encode('utf-8')).hexdigest()}"
                    ),
                }
            )
        stable_system_text = str(stable_system_context or "").strip()
        if append_system_extra_block(stable_system_text):
            prompt_audit_sections.append({"name": "system_extra.plugin_stable", "text": stable_system_text})
        domain_profile_text = str(domain_profile_context or "").strip()
        if append_system_extra_block(domain_profile_text):
            prompt_audit_sections.append({"name": "system_extra.domain_profile", "text": domain_profile_text})
        resource_context_text = str(resource_context or "").strip()
        if resource_context_text:
            resource_block = f"可用视觉资源：\n{resource_context_text}"
            if append_system_extra_block(resource_block):
                prompt_audit_sections.append({"name": "system_extra.resource_context", "text": resource_block})
        semantic_text = str(semantic_summary_text or "").strip()
        semantic_block = ""
        if semantic_text:
            semantic_block = f"较长期的语义记忆（最多3条）：\n{semantic_text}"
            prompt_audit_sections.append({"name": "user.semantic_memory", "text": semantic_block})
        episodic_text = str(episodic_summary_text or "").strip()
        episodic_block = ""
        if episodic_text:
            episodic_block = f"最近可见的阶段摘要（5~10条弹性窗口）：\n{episodic_text}"
            prompt_audit_sections.append({"name": "user.episodic_summary", "text": episodic_block})
        # DeepSeek-style prefix caches are very sensitive to early dynamic text.
        # Keep volatile memory layers out of the system prefix so stable persona/tool
        # instructions can still be reused across turns.
        memory_context_blocks = [block for block in [semantic_block, episodic_block] if block]
        memory_context_text = "\n\n".join(memory_context_blocks)

        current_time_text = datetime.fromtimestamp(now_ts).strftime("%Y-%m-%d %H:%M")
        tool_context_text = str(tool_prompt_context or "").strip() or "当前没有额外能力或工具说明。"
        tool_context_hash = hashlib.sha256(tool_context_text.encode("utf-8", errors="ignore")).hexdigest()
        stable_user_intro = (
            "如果记忆里出现“记忆情绪”，那是你当时记住这件事时留下的情感余温；"
            "回应时自然带着这份余温即可，不要把它当作用户事实，也不要生硬复述标签。\n\n"
            f"{ATTRIBUTION_RULES}\n\n"
        )
        # ``prompt_scope`` is an operational delivery/audit label. It must not
        # select a second prompt shape: ordinary messages and external events
        # share the same MemCore timeline and therefore the same layout rules.
        linear_timeline_turn = bool(current_message_in_raw and not str(memory_text or "").strip())
        persona_runtime_context_text = "\n\n".join(
            (
                f"【本轮当前助手状态（宿主可信上下文）】\n{persona_system or '(无额外当前状态)'}",
                f"【本轮角色表达侧面参考】\n{persona_reference_context or '(无额外表达侧面参考)'}",
            )
        )
        stable_extra_context_text = str(extra_context or "").strip()
        runtime_context_text = "\n\n".join(
            part for part in (persona_runtime_context_text, stable_extra_context_text) if part
        )
        stable_user_context = (f"{stable_user_intro}【本轮系统能力与工具上下文】\n{tool_context_text}").strip()
        structured_history_turns: list[dict[str, Any]] = [{"role": "user", "content": stable_user_context}]
        # Reusable host/persona state belongs before the append-only timeline.
        # Freezing it into every current user turn made a short chat message
        # grow by thousands of tokens per round.  Keep persona and other host
        # state in separate blocks so a task/attachment state change can still
        # reuse the earlier persona prefix.
        structured_history_turns.append({"role": "user", "content": persona_runtime_context_text})
        if stable_extra_context_text:
            structured_history_turns.append({"role": "user", "content": stable_extra_context_text})
        if memory_context_text:
            structured_history_turns.append({"role": "user", "content": memory_context_text})
        structured_history_turns.extend(dict(turn) for turn in list(history_turns or []) if isinstance(turn, dict))
        # The current message is the only persistent message in this request.
        # Retrieval evidence and live host state remain visible immediately
        # after it, but are request-scoped and must never be frozen into the
        # MemCore provider projection for later turns.
        user_prompt = str(current_message_text or "").strip()
        ephemeral_context_parts = [
            f"可用回忆片段：\n{memory_text}" if str(memory_text or "").strip() else "",
            str(volatile_extra_context or "").strip(),
            (
                f"当前演出状态（本轮基准参考，不是硬锁定）：\n{current_visual_context}"
                if str(current_visual_context or "").strip()
                else ""
            ),
            f"当前时间：{current_time_text}" if not current_message_in_raw else "",
        ]
        ephemeral_context_text = "\n\n".join(part for part in ephemeral_context_parts if part)
        ephemeral_turns = (
            [{"role": "user", "content": ephemeral_context_text}]
            if ephemeral_context_text
            else []
        )
        extra_context_subsections: list[dict[str, str]] = []
        for section in extra_context_audit_sections or []:
            if not isinstance(section, dict):
                continue
            name = str(section.get("name") or "").strip()
            text = str(section.get("text") or "").strip()
            if not name or not text:
                continue
            if not name.startswith("user.extra_context."):
                name = f"user.extra_context.{name}"
            extra_context_subsections.append({"name": name, "text": text})
        prompt_audit_sections.extend(
            [
                {"name": "user.full", "text": user_prompt},
                {"name": "user.ephemeral_context", "text": ephemeral_context_text},
                {"name": "user.stable_context", "text": stable_user_context},
                {"name": "user.tool_context", "text": tool_context_text},
                {"name": "user.memory_context", "text": memory_context_text},
                {"name": "user.runtime_context", "text": runtime_context_text},
                {
                    "name": "user.extra_context",
                    "text": "\n\n".join(
                        part
                        for part in [
                            str(extra_context or "").strip(),
                            str(volatile_extra_context or "").strip(),
                        ]
                        if part
                    ),
                },
                {"name": "user.stable_extra_context", "text": extra_context},
                {"name": "user.volatile_extra_context", "text": volatile_extra_context},
                {"name": "user.raw_recent_timeline", "text": raw_text or "(无)"},
                {"name": "user.retrieval_snippets", "text": memory_text},
                *extra_context_subsections,
                {"name": "user.current_visual_context", "text": current_visual_context},
                {"name": "user.persona_state", "text": persona_system or "(无额外当前状态)"},
                {"name": "user.persona_reference_context", "text": persona_reference_context or "(无额外表达侧面参考)"},
                {
                    "name": "user.current_message",
                    "text": current_message_text,
                },
                {
                    "name": "user.current_time",
                    "text": "" if current_message_in_raw else current_time_text,
                },
            ]
        )
        return {
            "debug_enabled": debug_enabled,
            "visual_defaults": visual_defaults,
            "fallback": fallback,
            "system_prompt": system_prompt,
            "system_extra_blocks": system_extra_blocks,
            "stable_system_context_hash": self._stable_system_context_hash(
                registered_stable_blocks=registered_stable_blocks,
                legacy_stable_system_text=stable_system_text,
            ),
            "tool_prompt_context_hash": tool_context_hash,
            "linear_timeline_turn": linear_timeline_turn,
            "history_turns": structured_history_turns,
            "user_prompt": user_prompt,
            "ephemeral_turns": ephemeral_turns,
            "prompt_audit_sections": prompt_audit_sections,
        }

    @staticmethod
    def _stable_system_context_hash(
        *,
        registered_stable_blocks: tuple[str, ...],
        legacy_stable_system_text: str,
    ) -> str:
        if not registered_stable_blocks:
            return (
                hashlib.sha256(legacy_stable_system_text.encode("utf-8", errors="ignore")).hexdigest()
                if legacy_stable_system_text
                else ""
            )
        stable_blocks = list(registered_stable_blocks)
        if legacy_stable_system_text and legacy_stable_system_text not in stable_blocks:
            stable_blocks.append(legacy_stable_system_text)
        canonical = json.dumps(stable_blocks, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8", errors="ignore")).hexdigest()

    def build_summary_prompts(
        self,
        *,
        transcript: str,
        batch_size: int,
        reference_summary_text: str = "",
        persona_system_context: str = "",
        persona_reference_context: str = "",
    ) -> tuple[str, str]:
        system_prompt = self._append_memory_persona_context(
            self.persona.summary_system_prompt,
            persona_system_context=persona_system_context,
            persona_reference_context=persona_reference_context,
        )
        user_prompt = self.persona.summary_user_prompt_template.format(
            transcript=transcript,
            batch_size=int(batch_size),
        )
        reference_summary_text = str(reference_summary_text or "").strip()
        if reference_summary_text:
            user_prompt = (
                f"{user_prompt.rstrip()}\n\n"
                "可参考的既有阶段摘要（只用于保持人物关系、项目脉络、时间线和记忆口吻一致；"
                "不要把参考摘要里出现、但本段原始对话没有出现的内容写成这段的新事实）：\n"
                f"{reference_summary_text}\n"
            )
        return (
            self._append_memory_time_anchor_rules(
                f"{system_prompt.rstrip()}\n\n{_memory_metadata_contract_prompt()}"
            ),
            user_prompt,
        )

    def build_semantic_summary_prompts(
        self,
        *,
        source_text: str,
        persona_system_context: str = "",
        persona_reference_context: str = "",
    ) -> tuple[str, str]:
        system_prompt = self._append_memory_persona_context(
            self.persona.semantic_summary_system_prompt,
            persona_system_context=persona_system_context,
            persona_reference_context=persona_reference_context,
        )
        return (
            self._append_memory_time_anchor_rules(
                f"{system_prompt.rstrip()}\n\n{_memory_metadata_contract_prompt()}"
            ),
            self.persona.semantic_summary_user_prompt_template.format(source_text=source_text),
        )

    def build_semantic_reinforcement_prompts(
        self,
        *,
        existing_text: str,
        incoming_text: str,
        persona_system_context: str = "",
        persona_reference_context: str = "",
    ) -> tuple[str, str]:
        system_prompt = self._append_memory_persona_context(
            self.persona.semantic_reinforcement_system_prompt,
            persona_system_context=persona_system_context,
            persona_reference_context=persona_reference_context,
        )
        return (
            self._append_memory_time_anchor_rules(
                f"{system_prompt.rstrip()}\n\n{_memory_metadata_contract_prompt()}"
            ),
            self.persona.semantic_reinforcement_user_prompt_template.format(
                existing_text=existing_text,
                incoming_text=incoming_text,
            ),
        )

    def _append_memory_persona_context(
        self,
        system_prompt: str,
        *,
        persona_system_context: str = "",
        persona_reference_context: str = "",
    ) -> str:
        context_parts = [
            str(persona_system_context or "").strip(),
            str(persona_reference_context or "").strip(),
        ]
        context_text = "\n\n".join(part for part in context_parts if part)
        if not context_text:
            return system_prompt
        return (
            f"{system_prompt.rstrip()}\n\n"
            "[CURRENT CHARACTER MEMORY SELF]\n"
            "下面是你此刻的角色身份与表达侧面；整理记忆时就按这个身份记。\n"
            "角色设定只决定你的记忆口吻、在意点和情感余温，不是这段对话发生过的事实。\n"
            f"{context_text}"
        )

    @staticmethod
    def _append_memory_time_anchor_rules(system_prompt: str) -> str:
        return f"{system_prompt.rstrip()}\n\n{MEMORY_TIME_ANCHOR_RULES}"
