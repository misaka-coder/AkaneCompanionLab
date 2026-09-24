from __future__ import annotations

import asyncio
import hashlib
import json
import uuid

from ...care_runtime import format_care_feed_prompt
from ..contracts import Beat, Identity, ModelPresentation, Presentation, Receipt, TurnRequest


class PresentationService:
    def __init__(self, *, room, generate):
        self.room = room
        self.generate = generate
        self.active = {}
        with room.repository.transaction() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS scene_turns (
                    turn_id TEXT PRIMARY KEY, scope TEXT NOT NULL, request_id TEXT NOT NULL,
                    fingerprint TEXT NOT NULL, status TEXT NOT NULL, presentation TEXT,
                    UNIQUE(scope, request_id));
                CREATE TABLE IF NOT EXISTS scene_receipts (
                    turn_id TEXT NOT NULL, beat_id TEXT NOT NULL, kind TEXT NOT NULL,
                    PRIMARY KEY(turn_id, beat_id, kind));
                CREATE TABLE IF NOT EXISTS scene_cancellations (
                    scope TEXT NOT NULL, request_id TEXT NOT NULL, PRIMARY KEY(scope,request_id));
            """)

    async def turn(self, request: TurnRequest) -> Presentation:
        identity = request.identity
        snapshot = await asyncio.to_thread(self.room.snapshot, identity)
        scope = identity.model_dump_json()
        fingerprint = hashlib.sha256(json.dumps([request.message, request.event_id]).encode()).hexdigest()
        turn_id = uuid.uuid4().hex
        with self.room.repository.transaction() as db:
            previous = db.execute("SELECT fingerprint,status,presentation FROM scene_turns WHERE scope=? AND request_id=?",
                                  (scope, request.request_id)).fetchone()
            if previous:
                if previous[0] != fingerprint:
                    raise ValueError("request_id_conflict")
                if previous[1] == "completed":
                    return Presentation.model_validate_json(previous[2])
                raise ValueError("presentation_" + previous[1])
            event = None
            if request.event_id:
                row = db.execute("SELECT identity,fact FROM outbox WHERE event_id=?", (request.event_id,)).fetchone()
                if not row or Identity.model_validate_json(row[0]) != identity:
                    raise ValueError("event_not_found")
                event = json.loads(row[1])
            if not request.message.strip() and not event:
                raise ValueError("empty_scene_turn")
            db.execute("INSERT INTO scene_turns VALUES (?,?,?,?,?,NULL)",
                       (turn_id, scope, request.request_id, fingerprint, "generating"))
        outfit = next((o for o in snapshot.catalog.outfits if o.id == snapshot.room.outfit_id), None)
        active_story = None
        if hasattr(self.room, "story") and hasattr(self.room.story, "repository"):
            try:
                with self.room.story.repository.transaction() as sdb:
                    runs = self.room.story.repository.list_runs(sdb, identity)
                    active_run = next((r for r in runs.values() if r.status == "in_progress"), None)
                    if active_run and active_run.current_node:
                        pack = self.room.story._packs.get(active_run.story_id)
                        active_story = {
                            "story_title": pack.title if pack else active_run.story_id,
                            "current_node_kind": active_run.current_node.kind,
                            "current_speech": active_run.current_node.speech,
                            "prompt_objective": active_run.current_node.prompt_objective,
                        }
            except Exception:
                pass
        context = {"room": snapshot.room.model_dump(), "care": snapshot.care.model_dump(),
                   "recent_delivery": self.delivery_projection(identity),
                   "available_emotions": [e.id for e in outfit.emotions] if outfit else [], "confirmed_action": event,
                   "available_outfits": [{"id": o.id, "name": o.name} for o in snapshot.catalog.outfits],
                   "available_backgrounds": [{"id": b.id, "name": b.name} for b in snapshot.catalog.backgrounds]}
        if active_story:
            context["active_story"] = active_story
        user_text = request.message.strip()
        turn_kind = ""
        memory_message = user_text

        if event:
            kind = event.get("kind")
            is_ok = bool(event.get("ok", True))
            if is_ok and kind == "feed":
                target_id = event.get("target", "")
                item = next((it for it in snapshot.shop if it.id == target_id), None)
                item_name = event.get("item_name") or (item.name if item else "") or target_id or "点心"
                effects = event.get("effects") or (item.effects if item else {})
                quantity = int(event.get("quantity") or 1)
                care = snapshot.care
                feed_pack = format_care_feed_prompt(
                    item_name=item_name,
                    hunger_delta=effects.get("hunger", 0),
                    energy_delta=effects.get("energy", 0),
                    affection_delta=effects.get("affection", 0),
                    current_hunger=care.hunger,
                    current_energy=care.energy,
                    current_affection=care.affection,
                    actor_label="我",
                    quantity=quantity,
                    user_message=user_text,
                )
                message = feed_pack["turn_message"]
                turn_kind = feed_pack["turn_kind"]
                memory_message = feed_pack["memory_message"]
            elif is_ok and kind == "touch":
                part_map = {"head": "头", "hand": "手", "shoulder": "肩膀"}
                target_part = event.get("target", "head")
                part_name = part_map.get(target_part, target_part or "头")
                touch_text = f"刚才发生的互动：我摸了摸你的{part_name}。"
                message = f"{touch_text}\n{user_text}".strip() if user_text else touch_text
                memory_message = user_text or touch_text
            elif is_ok and kind == "equip":
                target_id = event.get("target", "")
                outfit_match = next((o for o in snapshot.catalog.outfits if o.id == target_id), None)
                outfit_name = outfit_match.name if outfit_match else target_id
                equip_text = f"刚才发生的互动：我为你换上了「{outfit_name}」。"
                message = f"{equip_text}\n{user_text}".strip() if user_text else equip_text
                memory_message = user_text or equip_text
            elif is_ok and kind == "claim_allowance":
                allowance_text = "刚才发生的互动：领取了今天的零花钱。"
                message = f"{allowance_text}\n{user_text}".strip() if user_text else allowance_text
                memory_message = user_text or allowance_text
            else:
                message = user_text or "请自然回应刚刚发生的互动。"
                memory_message = user_text or f"[房间互动] {event.get('kind', 'action')}: {event.get('target', '')}"
        else:
            message = user_text or "请自然回应刚刚发生的互动。"
            memory_message = user_text

        # Changing state is appended to current input, never the stable prefix.
        payload = {
            "user_id": identity.session_id, "real_user_id": identity.profile_user_id,
            "character_pack_id": identity.character_pack_id, "client_mode": "desktop_pet",
            "client_capabilities": ["scene_presentation_v1", "speech_segments", "tool_actions", "tts", "static_sprite"],
            "message": message + "\n[当前房间事实，由宿主提供]\n" + json.dumps(context, ensure_ascii=False, sort_keys=True),
            "memory_message": memory_message,
            "current_visual": {"character": {"outfit": snapshot.room.outfit_id}},
        }
        if turn_kind:
            payload["turn_kind"] = turn_kind
        try:
            with self.room.repository.transaction() as db:
                if db.execute("SELECT 1 FROM scene_cancellations WHERE scope=? AND request_id=?",
                              (scope, request.request_id)).fetchone():
                    raise ValueError("presentation_cancelled")
            task = asyncio.create_task(self.generate(payload))
            self.active[(scope, request.request_id)] = task
            try:
                output = await task
            finally:
                self.active.pop((scope, request.request_id), None)
            if output.get("_transient_final_failure") or output.get("status") == "stopped":
                raise ValueError("model_response_failed")
            parsed = ModelPresentation.model_validate({"beats": output.get("beats")})
            diagnostics = []
            beats = []
            allowed = context["available_emotions"]
            fallback = "normal" if "normal" in allowed else (allowed[0] if allowed else "normal")
            for i, beat in enumerate(parsed.beats):
                if beat.emotion_id not in allowed:
                    diagnostics.append(f"emotion_unavailable:{beat.emotion_id}")
                    beat.emotion_id = fallback
                beats.append(Beat(**beat.model_dump(), beat_id=f"{turn_id}:{i}", sequence=i))
            presentation = Presentation(turn_id=turn_id, generation=request.generation,
                                        resource_revision=snapshot.catalog.revision, beats=beats, diagnostics=diagnostics)
            with self.room.repository.transaction() as db:
                db.execute("UPDATE scene_turns SET status='completed',presentation=? WHERE turn_id=?",
                           (presentation.model_dump_json(), turn_id))
                self._event(db, identity, f"presentation:{turn_id}", "scene.presentation", {
                    "turn_id": turn_id, "status": "generated_not_delivered", "beat_count": len(beats),
                    "speech": "\n".join(b.speech for b in beats),
                })
            await asyncio.to_thread(self.room.flush_events)
            return presentation
        except BaseException:
            with self.room.repository.transaction() as db:
                db.execute("UPDATE scene_turns SET status='failed' WHERE turn_id=?", (turn_id,))
            raise

    def cancel(self, request):
        key = (request.identity.model_dump_json(), request.request_id)
        with self.room.repository.transaction() as db:
            db.execute("INSERT OR IGNORE INTO scene_cancellations VALUES (?,?)", key)
        task = self.active.get(key)
        if task and not task.cancelling():
            task.cancel()
        return {"ok": True, "status": "stop_requested"}

    def delivery_projection(self, identity):
        """Generated text remains raw history; delivery is independently explicit."""
        result = []
        with self.room.repository.transaction() as db:
            rows = db.execute("SELECT presentation FROM scene_turns WHERE scope=? AND status='completed' ORDER BY rowid DESC LIMIT 2",
                              (identity.model_dump_json(),)).fetchall()
            for (raw,) in reversed(rows):
                presentation = Presentation.model_validate_json(raw)
                for beat in presentation.beats:
                    kinds = {r[0] for r in db.execute("SELECT kind FROM scene_receipts WHERE turn_id=? AND beat_id=?",
                                                     (presentation.turn_id, beat.beat_id))}
                    result.append({"beat_id": beat.beat_id, "speech_preview": beat.speech[:180],
                                   "text_fully_shown": "text_revealed" in kinds,
                                   "audio_fully_played": "audio_completed" in kinds,
                                   "interrupted": bool(kinds & {"interrupted", "audio_interrupted"})})
        return result

    def receipt(self, receipt: Receipt) -> dict:
        with self.room.repository.transaction() as db:
            row = db.execute("SELECT scope,presentation FROM scene_turns WHERE turn_id=? AND status='completed'",
                             (receipt.turn_id,)).fetchone()
            if not row or row[0] != receipt.identity.model_dump_json():
                raise ValueError("presentation_not_found")
            presentation = Presentation.model_validate_json(row[1])
            beat = next((b for b in presentation.beats if b.beat_id == receipt.beat_id), None)
            if not beat or receipt.generation != presentation.generation:
                raise ValueError("stale_presentation")
            kinds = {r[0] for r in db.execute("SELECT kind FROM scene_receipts WHERE turn_id=? AND beat_id=?",
                                             (receipt.turn_id, receipt.beat_id)).fetchall()}
            if receipt.kind in kinds:
                return {"ok": True, "duplicate": True}
            if ("interrupted" in kinds and receipt.kind != "audio_interrupted") or (receipt.kind != "display_started" and "display_started" not in kinds):
                raise ValueError("delivery_order_invalid")
            if receipt.kind == "audio_completed" and ("audio_started" not in kinds or "audio_interrupted" in kinds):
                raise ValueError("delivery_order_invalid")
            db.execute("INSERT INTO scene_receipts VALUES (?,?,?)", (receipt.turn_id, receipt.beat_id, receipt.kind))
            self._event(db, receipt.identity, f"receipt:{receipt.beat_id}:{receipt.kind}", "scene.delivery", {
                "turn_id": receipt.turn_id, "beat_id": receipt.beat_id, "kind": receipt.kind,
                "speech": beat.speech if receipt.kind in {"text_revealed", "audio_completed"} else "",
            })
        self.room.flush_events()
        return {"ok": True, "duplicate": False}

    @staticmethod
    def _event(db, identity, event_id, kind, fields):
        payload = {"event_type": kind, "fields": fields}
        db.execute("INSERT OR IGNORE INTO outbox(event_id,identity,fact) VALUES (?,?,?)",
                   (event_id, identity.model_dump_json(), json.dumps(payload, ensure_ascii=False)))
