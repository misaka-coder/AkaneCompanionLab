import asyncio
import json
import uuid
from typing import Callable

from urllib.parse import quote

from ..contracts import (
    Beat,
    Identity,
    ModelBeat,
    Presentation,
    StoryCatalogResponse,
    StoryItemSummary,
    StoryNode,
    StoryPack,
    StoryRunState,
    StoryStepRequest,
)
from .conditions import evaluate_condition
from .loader import StoryLoader
from .packs.twilight_tea_party import get_twilight_tea_party_pack
from .repository import StoryRepository


class StoryService:
    def __init__(
        self,
        repository: StoryRepository,
        generate: Callable | None = None,
        loader: StoryLoader | None = None,
        care = None,
        snapshot_fn: Callable | None = None,
    ):
        self.repository = repository
        self.generate = generate
        self.active = {}
        self.loader = loader or StoryLoader()
        self.care = care
        self.snapshot_fn = snapshot_fn
        self.loader.register_builtin_pack(get_twilight_tea_party_pack())

    def register_pack(self, pack: StoryPack):
        self.loader.register_builtin_pack(pack)

    def get_pack(self, story_id: str, character_pack_id: str = "") -> StoryPack:
        pack = self.loader.get_pack(story_id, character_pack_id)
        if not pack:
            raise ValueError("story_pack_not_found")
        return pack

    def catalog(self, identity: Identity) -> StoryCatalogResponse:
        with self.repository.read_connection() as db:
            runs = self.repository.list_runs(db, identity)
            items: list[StoryItemSummary] = []
            packs = self.loader.list_packs(identity.character_pack_id)
            for story_id, pack in packs.items():
                run = runs.get(story_id)
                completed = self.repository.get_completed_endings(db, identity, story_id)
                cover_url = pack.cover_image
                if cover_url:
                    if cover_url.startswith(("http://", "https://", "/", "data:", "blob:")):
                        pass
                    elif self.loader.characters_dir and (self.loader.characters_dir / identity.character_pack_id / "stories" / cover_url).is_file():
                        cover_url = f"/scene/assets/characters/{identity.character_pack_id}/stories/{quote(cover_url, safe='/')}"
                    elif self.loader.characters_dir and (self.loader.characters_dir / identity.character_pack_id / cover_url).is_file():
                        cover_url = f"/scene/assets/characters/{identity.character_pack_id}/{quote(cover_url, safe='/')}"
                    else:
                        cover_url = f"/scene/assets/{quote(cover_url, safe='/')}"
                items.append(
                    StoryItemSummary(
                        story_id=story_id,
                        title=pack.title,
                        description=pack.description,
                        cover_image=cover_url,
                        has_active_run=bool(run and run.status == "in_progress"),
                        current_node_id=run.current_node_id if run else "",
                        status=run.status if run else "not_started",
                        completed_endings=completed,
                    )
                )
            return StoryCatalogResponse(stories=items)

    def import_story(self, identity: Identity, file_name: str, content: str) -> StoryCatalogResponse:
        self.loader.save_story(identity.character_pack_id, file_name, content)
        return self.catalog(identity)

    @staticmethod
    def build_node_presentation(
        identity: Identity,
        run_id: str,
        node: StoryNode,
        step_count: int,
    ) -> Presentation | None:
        beats: list[Beat] = []
        turn_id = f"story_{run_id}_{node.node_id}_{step_count}"
        if node.beats:
            for idx, b in enumerate(node.beats):
                beats.append(
                    Beat(
                        speech=b.speech,
                        emotion_id=b.emotion_id or node.emotion_id or "normal",
                        motion_id=b.motion_id or node.motion_id or "idle",
                        advance=b.advance or "click",
                        beat_id=f"{turn_id}:{idx}",
                        sequence=idx,
                    )
                )
        elif node.speech:
            beats.append(
                Beat(
                    speech=node.speech,
                    emotion_id=node.emotion_id or "normal",
                    motion_id=node.motion_id or "idle",
                    advance="click",
                    beat_id=f"{turn_id}:0",
                    sequence=0,
                )
            )
        if not beats:
            return None
        return Presentation(
            protocol="scene_presentation_v1",
            turn_id=turn_id,
            generation=step_count,
            resource_revision="",
            beats=beats,
            diagnostics=[],
        )

    def start(self, identity: Identity, story_id: str, force_restart: bool = False) -> StoryRunState:
        pack = self.get_pack(story_id, identity.character_pack_id)
        with self.repository.transaction() as db:
            existing = self.repository.get_run(db, identity, story_id)
            if existing and existing.status == "in_progress" and not force_restart:
                self.repository.pin_pack(db, existing.run_id, pack)
                if not existing.presentation:
                    step_count = max(0, len(existing.history) - 1)
                    p = self.build_node_presentation(identity, existing.run_id, existing.current_node, step_count)
                    if p:
                        existing.presentation = p
                        self.repository.record_turn_presentation(db, identity, p.turn_id, p)
                return existing

            initial = pack.nodes.get(pack.initial_node_id)
            if not initial:
                raise ValueError("invalid_initial_node")

            completed = self.repository.get_completed_endings(db, identity, story_id)
            run_id = f"run_{uuid.uuid4().hex[:12]}"
            presentation = self.build_node_presentation(identity, run_id, initial, 0)
            run = StoryRunState(
                run_id=run_id,
                story_id=story_id,
                current_node_id=pack.initial_node_id,
                status="in_progress",
                current_node=initial,
                variables={},
                history=[pack.initial_node_id],
                completed_endings=completed,
                presentation=presentation,
            )
            if presentation:
                self.repository.record_turn_presentation(db, identity, presentation.turn_id, presentation)
            self.repository.pin_pack(db, run_id, pack)
            self.repository.save_run(db, identity, run)
            return run

    async def step(self, payload: StoryStepRequest) -> StoryRunState:
        key = (payload.identity.model_dump_json(), payload.run_id, payload.expected_node_id)
        fingerprint = (payload.choice_id, payload.user_message)
        previous = self.active.get(key)
        if previous:
            if previous[0] != fingerprint:
                raise ValueError("model_busy")
            return await asyncio.shield(previous[1])
        task = asyncio.create_task(self._step(payload))
        self.active[key] = (fingerprint, task)
        def finished(done):
            if self.active.get(key, (None, None))[1] is done:
                self.active.pop(key, None)
            if not done.cancelled():
                done.exception()
        task.add_done_callback(finished)
        return await asyncio.shield(task)

    def cancel(self, payload: StoryStepRequest):
        key = (payload.identity.model_dump_json(), payload.run_id, payload.expected_node_id)
        active = self.active.get(key)
        if active:
            active[1].cancel()
        return {"ok": True, "status": "stop_requested"}

    async def _step(self, payload: StoryStepRequest) -> StoryRunState:
        story_id = self._get_story_id_for_run(payload.identity, payload.run_id)

        # 1. Short read to inspect current state
        with self.repository.read_connection() as db:
            run = self.repository.get_run(db, payload.identity, story_id)
            pack = self.repository.get_pack(db, payload.run_id)
            completed_endings = self.repository.get_completed_endings(db, payload.identity, story_id) if run and pack else []
        if pack is None:
            raise ValueError("story_restart_required")
        if not run or run.run_id != payload.run_id:
            raise ValueError("story_run_not_found")
        if run.current_node_id != payload.expected_node_id:
            # Idempotent return if already advanced
            return run
        if run.status == "completed":
            return run

        curr = run.current_node if run.current_node.node_id == run.current_node_id else None
        if not curr:
            raise ValueError("invalid_current_node")

        target_node_id = ""
        next_node = None
        new_variables = dict(run.variables)

        if curr.kind == "choice":
            if not payload.choice_id:
                raise ValueError("missing_choice_id")
            chosen = next((o for o in curr.options if o.id == payload.choice_id), None)
            if not chosen:
                raise ValueError("invalid_choice_option")

            inv = {}
            coins = 0
            equipment = ""
            if self.snapshot_fn and (chosen.condition or chosen.action_kind):
                try:
                    snap = self.snapshot_fn(payload.identity)
                    if isinstance(snap, dict):
                        care_dict = snap.get("care") or {}
                        inv = care_dict.get("inventory") or {}
                        coins = care_dict.get("coins", 0)
                        room_dict = snap.get("room") or {}
                        equipment = room_dict.get("outfit_id", "")
                    else:
                        inv = (getattr(snap.care, "inventory", None) if hasattr(snap, "care") else None) or {}
                        coins = getattr(snap.care, "coins", 0) if hasattr(snap, "care") else 0
                        equipment = getattr(snap.room, "outfit_id", "") if hasattr(snap, "room") else ""
                except Exception:
                    pass

            # 校验条件 (Condition evaluation)
            if chosen.condition:
                cond_ok, cond_reason = evaluate_condition(
                    chosen.condition,
                    inventory=inv,
                    coins=coins,
                    equipment=equipment,
                    completed_endings=completed_endings,
                    variables=new_variables,
                    story_id=pack.story_id,
                )
                if not cond_ok:
                    raise ValueError(f"choice_condition_unmet:{cond_reason}")

            # 动作原子编排 (Choice action execution: feed / direct buy-and-feed)
            if chosen.action_kind and self.care:
                action_kind = chosen.action_kind
                target = chosen.action_target
                count = chosen.action_count or 1
                cost = chosen.cost_coins or 0
                if action_kind == "feed" and target:
                    has_item = inv.get(target, 0) >= count if self.snapshot_fn else False
                    if not has_item and cost > 0:
                        buy_res = self.care.action(
                            payload.identity,
                            kind="buy",
                            target=target,
                            count=count,
                            request_id=f"{payload.run_id}:{payload.expected_node_id}:buy:{target}",
                        )
                        if not buy_res.get("ok"):
                            raise ValueError(f"action_failed:{buy_res.get('reason') or 'insufficient_coins'}")
                    feed_res = self.care.action(
                        payload.identity,
                        kind="feed",
                        target=target,
                        count=count,
                        request_id=f"{payload.run_id}:{payload.expected_node_id}:feed:{target}",
                    )
                    if not feed_res.get("ok"):
                        raise ValueError(f"action_failed:{feed_res.get('reason') or 'feed_failed'}")
                    new_variables["last_action"] = f"feed:{target}"
                    new_variables[f"action_done:{curr.node_id}"] = "true"

            target_node_id = chosen.next_node_id
        elif curr.kind == "script":
            target_node_id = curr.next_node_id
        elif curr.kind == "conversation":
            speaker_name = curr.speaker or (pack.nodes.get(pack.initial_node_id).speaker if pack.initial_node_id in pack.nodes else "") or "你"
            if payload.advance_only or (run.turn_count >= curr.max_turns and not payload.user_message.strip()):
                target_node_id = curr.next_node_id
                new_variables[f"conv_done:{curr.node_id}"] = "true"
            else:
                user_msg = payload.user_message.strip()
                if not user_msg:
                    raise ValueError("empty_user_message")
                if run.turn_count >= curr.max_turns:
                    raise ValueError("max_conversation_turns_reached")

                conv_history_lines = []
                for t in run.conversation_turns:
                    conv_history_lines.append(f"{t.get('speaker', '人物')}：“{t.get('text', '')}”")
                conv_context = "\n".join(conv_history_lines)
                conv_section = f"【本幕已发生的多轮交谈】：\n{conv_context}\n" if conv_context else ""

                suggest_close = ""
                if run.turn_count + 1 >= curr.suggested_turns:
                    suggest_close = "\n【系统温和收尾提示】：玩家与你已经聊了数轮，请在本次回复中保持温柔体贴的同时，自然提及时间不早了、注意休息，或自然将话题引向下一个情境，温和引导玩家继续故事。"

                prompt_text = (
                    f"【互动剧本模式·多轮自由互动】你正在与玩家进行专属剧本《{pack.title}》的互动演出。\n"
                    f"剧本情境：{pack.description}\n"
                    f"当前情境：{curr.speech}\n"
                    f"{conv_section}"
                    f"玩家现在对你说：\"{user_msg}\"\n"
                    f"本幕互动目标：{curr.prompt_objective or '以角色的人设自然回应玩家，表达情感并交流。'}\n"
                    f"{suggest_close}\n"
                    f"请完全保持当前角色的人设与口吻，针对玩家的这句回答做出充满情感的自然回应。"
                )

                if not self.generate:
                    raise ValueError("model_generation_unavailable")

                gen_payload = {
                    "user_id": payload.identity.session_id,
                    "real_user_id": payload.identity.profile_user_id,
                    "character_pack_id": payload.identity.character_pack_id,
                    "client_mode": "desktop_pet",
                    "client_capabilities": ["scene_presentation_v1", "speech_segments", "tool_actions", "tts", "static_sprite"],
                    "message": prompt_text,
                    "memory_message": f"[剧本互动·{pack.title}] 与玩家多轮交谈第{run.turn_count + 1}轮，玩家说: {user_msg}",
                    "current_visual": {"character": {}},
                }

                import inspect
                try:
                    if inspect.iscoroutinefunction(self.generate):
                        output = await self.generate(gen_payload)
                    else:
                        output = await asyncio.to_thread(self.generate, gen_payload)
                except Exception as exc:
                    raise ValueError("model_generation_failed") from exc

                if isinstance(output, dict) and (output.get("_transient_final_failure") or output.get("status") == "stopped"):
                    raise ValueError("model_generation_failed")

                raw_beats = (output.get("beats") if isinstance(output, dict) else None) or []
                model_beats: list[ModelBeat] = []
                for b in raw_beats:
                    if isinstance(b, dict):
                        sp = (b.get("speech") or "").strip()
                        if sp:
                            model_beats.append(
                                ModelBeat(
                                    speech=sp,
                                    emotion_id=b.get("emotion_id") or "开心",
                                    motion_id=b.get("motion_id") or "bounce",
                                    advance=b.get("advance") or "click",
                                )
                            )
                if not model_beats:
                    raise ValueError("model_empty_response")

                dynamic_speech = " ".join(b.speech for b in model_beats)
                new_turns = list(run.conversation_turns) + [
                    {"speaker": "你", "text": user_msg},
                    {"speaker": curr.speaker or speaker_name, "text": dynamic_speech},
                ]
                new_turn_count = run.turn_count + 1

                dynamic_node = curr.model_copy(update={
                    "speech": dynamic_speech[:3000],
                    "beats": model_beats,
                })
                next_node = dynamic_node
                target_node_id = curr.node_id
        elif curr.kind == "agent":
            speaker_name = curr.speaker or (pack.nodes.get(pack.initial_node_id).speaker if pack.initial_node_id in pack.nodes else "") or "你"
            user_msg = payload.user_message.strip() or "（默默微笑着注视着你）"
            new_variables[f"msg:{curr.node_id}"] = user_msg

            # Build recent story context from traversed history
            recent_lines = []
            for nid in run.history[-6:]:
                prev_node = pack.nodes.get(nid)
                if prev_node and prev_node.speech:
                    speaker_label = prev_node.speaker or speaker_name
                    recent_lines.append(f"{speaker_label}：“{prev_node.speech}”")
                prev_user_msg = run.variables.get(f"msg:{nid}")
                if prev_user_msg:
                    recent_lines.append(f"玩家：“{prev_user_msg}”")
            dialogue_context = "\n".join(recent_lines)
            context_section = f"【此前发生的剧情对白脉络】：\n{dialogue_context}\n" if dialogue_context else ""

            prompt_text = (
                f"【互动剧本模式】你正在与玩家进行专属剧本《{pack.title}》的互动演出。\n"
                f"剧本情境：{pack.description}\n"
                f"{context_section}"
                f"上一步你刚对玩家说：\"{curr.speech}\"\n"
                f"玩家现在的回复说：\"{user_msg}\"\n"
                f"本幕演出目标与情绪设定：{curr.prompt_objective or '以角色的人设自然回应玩家，表达情感并推进剧情。'}\n"
                f"请结合前面的剧情脉络，完全保持当前角色的人设与口吻，针对玩家的这句回答做出充满情感的自然回应，推进剧情走向。"
            )

            if not self.generate:
                raise ValueError("model_generation_unavailable")

            gen_payload = {
                "user_id": payload.identity.session_id,
                "real_user_id": payload.identity.profile_user_id,
                "character_pack_id": payload.identity.character_pack_id,
                "client_mode": "desktop_pet",
                "client_capabilities": ["scene_presentation_v1", "speech_segments", "tool_actions", "tts", "static_sprite"],
                "message": prompt_text,
                "memory_message": f"[剧本互动·{pack.title}] 与玩家互动，玩家说: {user_msg}",
                "current_visual": {"character": {}},
            }

            import inspect
            try:
                if inspect.iscoroutinefunction(self.generate):
                    output = await self.generate(gen_payload)
                else:
                    output = await asyncio.to_thread(self.generate, gen_payload)
            except Exception as exc:
                raise ValueError("model_generation_failed") from exc

            if isinstance(output, dict) and (output.get("_transient_final_failure") or output.get("status") == "stopped"):
                raise ValueError("model_generation_failed")

            raw_beats = (output.get("beats") if isinstance(output, dict) else None) or []
            model_beats: list[ModelBeat] = []
            for b in raw_beats:
                if isinstance(b, dict):
                    sp = (b.get("speech") or "").strip()
                    if sp:
                        model_beats.append(
                            ModelBeat(
                                speech=sp,
                                emotion_id=b.get("emotion_id") or "开心",
                                motion_id=b.get("motion_id") or "bounce",
                                advance=b.get("advance") or "click",
                            )
                        )
            if not model_beats:
                raise ValueError("model_empty_response")

            dynamic_speech = " ".join(b.speech for b in model_beats)
            dynamic_emotion = model_beats[0].emotion_id
            dynamic_motion = model_beats[0].motion_id
            dynamic_node_id = f"{curr.node_id}_reply"
            next_node = StoryNode(
                node_id=dynamic_node_id,
                kind="script",
                speaker=curr.speaker or speaker_name,
                speech=dynamic_speech[:3000],
                emotion_id=dynamic_emotion,
                motion_id=dynamic_motion,
                beats=model_beats,
                next_node_id=curr.next_node_id,
            )
            target_node_id = dynamic_node_id
        elif curr.kind == "ending":
            return run

        if not next_node:
            if not target_node_id or target_node_id not in pack.nodes:
                raise ValueError("invalid_target_node")
            next_node = pack.nodes[target_node_id]

        next_node = next_node.model_copy(update={
            "background_id": next_node.background_id or curr.background_id,
            "music_id": next_node.music_id or curr.music_id,
        })
        is_ending = next_node.kind == "ending"
        new_status = "in_progress"
        if is_ending:
            new_variables["reached_ending"] = target_node_id

        is_same_conversation_turn = curr.kind == "conversation" and target_node_id == curr.node_id
        new_history = list(run.history) if is_same_conversation_turn else list(run.history) + [target_node_id]

        # 2. Short write transaction to persist state
        with self.repository.transaction() as db:
            latest_run = self.repository.get_run(db, payload.identity, pack.story_id)
            if not latest_run or latest_run.run_id != payload.run_id:
                raise ValueError("story_run_not_found")
            if latest_run.current_node_id != payload.expected_node_id:
                return latest_run

            completed = latest_run.completed_endings
            step_count = len(new_history) - 1
            presentation = self.build_node_presentation(payload.identity, run.run_id, next_node, step_count)
            updated = StoryRunState(
                run_id=run.run_id,
                story_id=run.story_id,
                current_node_id=target_node_id,
                status=new_status,
                current_node=next_node,
                variables=new_variables,
                history=new_history,
                completed_endings=completed,
                presentation=presentation,
                conversation_turns=new_turns if is_same_conversation_turn else [],
                turn_count=new_turn_count if is_same_conversation_turn else 0,
            )
            if presentation:
                self.repository.record_turn_presentation(db, payload.identity, presentation.turn_id, presentation)
            self.repository.save_run(db, payload.identity, updated)
            return updated

    def complete_ending(self, identity: Identity, run_id: str, ending_id: str) -> StoryRunState:
        story_id = self._get_story_id_for_run(identity, run_id)
        with self.repository.transaction() as db:
            run = self.repository.get_run(db, identity, story_id)
            if not run or run.run_id != run_id:
                raise ValueError("story_run_not_found")
            self.repository.record_completion(db, identity, story_id, ending_id)
            completed = self.repository.get_completed_endings(db, identity, story_id)
            if ending_id not in completed:
                completed.append(ending_id)
            pack = self.repository.get_pack(db, run_id)
            ending_title = ending_id
            ending_summary = ""
            if pack and ending_id in pack.nodes:
                ending_title = pack.nodes[ending_id].ending_title or ending_id
                ending_summary = pack.nodes[ending_id].ending_summary or ""

            run = run.model_copy(update={"status": "completed", "completed_endings": completed})
            self.repository.save_run(db, identity, run)

            event_id = f"story:{run.run_id}:{ending_id}"
            fact_payload = {
                "event_type": "scene.story_completed",
                "source": "host",
                "fields": {
                    "story_id": story_id,
                    "ending_id": ending_id,
                    "ending_title": ending_title,
                    "summary": ending_summary,
                    "delivery": "confirmed",
                },
            }
            db.execute(
                "INSERT OR IGNORE INTO outbox(event_id,identity,fact) VALUES (?,?,?)",
                (event_id, identity.model_dump_json(), json.dumps(fact_payload, ensure_ascii=False)),
            )
            return run

    def reset(self, identity: Identity, story_id: str) -> bool:
        with self.repository.transaction() as db:
            self.repository.delete_run(db, identity, story_id)
            return True

    def _get_story_id_for_run(self, identity: Identity, run_id: str) -> str:
        with self.repository.read_connection() as db:
            runs = self.repository.list_runs(db, identity)
            for sid, r in runs.items():
                if r.run_id == run_id:
                    return sid
            raise ValueError("story_run_not_found")
