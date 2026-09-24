from __future__ import annotations

import json

from ..contracts import ActionRequest, ActionResult, Identity, Snapshot
from .journal import ActionJournal
from ..resources.wardrobe import publish_bundled_outfit
from ..story.repository import StoryRepository
from ..story.service import StoryService


class RoomService:
    def __init__(self, *, repository, care, resources, record_event, story_loader=None):
        self.repository = repository
        self.care = care
        self.resources = resources
        self.record_event = record_event
        self.journal = ActionJournal(self)
        self.story = StoryService(
            StoryRepository(repository.path),
            loader=story_loader,
            care=self.care,
            snapshot_fn=self.snapshot,
        )

    def snapshot(self, identity: Identity) -> Snapshot:
        with self.repository.transaction() as db:
            self.journal.recover(db, identity)
            room = self.repository.read(db, identity)
        catalog = self.resources.build(identity.character_pack_id)
        if not room.outfit_id and catalog.outfits:
            room.outfit_id = catalog.outfits[0].id
        if not room.background_id and catalog.backgrounds:
            preferred = next((b for b in catalog.backgrounds if "白天客厅" in b.name), catalog.backgrounds[0])
            room.background_id = preferred.id
        name = self.resources.characters.build_character_identity(identity.character_pack_id).get("assistant_name", "")
        return Snapshot(identity=identity, character_name=name, room=room,
                        care=self.care.snapshot(identity), shop=self.care.shop(identity), catalog=catalog)

    def action(self, request: ActionRequest) -> ActionResult:
        identity = request.identity
        result, duplicate = self.journal.execute(request)
        self.flush_events()
        return ActionResult(
            ok=result["ok"],
            reason=result["reason"],
            event=result.get("event"),
            duplicate=duplicate,
            snapshot=self.snapshot(identity),
        )

    def _apply(self, request, room, catalog):
        kind, target = request.kind, request.target
        if kind in {"buy", "feed", "claim_allowance"}:
            care = self.care.action(
                request.identity,
                kind=kind,
                target=target,
                count=getattr(request, "count", 1),
                request_id=request.request_id,
            )
            res = {"ok": bool(care.get("ok")), "reason": str(care.get("reason") or "care_failed")}
            if "item_name" in care:
                res["item_name"] = str(care["item_name"])
            if "quantity" in care:
                res["quantity"] = int(care["quantity"])
            if "effects_applied" in care:
                res["effects"] = {str(k): int(v) for k, v in care["effects_applied"].items()}
            return res
        candidates = {
            "equip": ("outfit_id", [o.id for o in catalog.outfits]),
            "background": ("background_id", [b.id for b in catalog.backgrounds]),
            "music": ("music_id", ["", *[m.id for m in catalog.music]]),
            "position": ("position", ["center", "right"]),
            "touch": (None, ["head", "hand", "shoulder"]),
        }
        field, allowed = candidates[kind]
        if target not in allowed:
            return {"ok": False, "reason": "resource_unavailable"}
        if kind == "equip":
            publish_bundled_outfit(self.resources, request.identity.character_pack_id, target)
        if field:
            setattr(room, field, target)
        return {"ok": True, "reason": "applied"}

    def flush_events(self):
        # Delivery uses stable source_id: a crash between append and mark is safe.
        with self.repository.transaction() as db:
            pending = db.execute("SELECT event_id,identity,fact FROM outbox WHERE delivered=0 ORDER BY rowid LIMIT 50").fetchall()
        for event_id, identity_json, fact_json in pending:
            identity = Identity.model_validate_json(identity_json)
            fact = json.loads(fact_json)
            try:
                result = self.record_event({
                    "source_id": f"scene:{event_id}", "user_id": identity.session_id,
                    "real_user_id": identity.profile_user_id, "character_pack_id": identity.character_pack_id,
                    "event": {"event_type": fact.get("event_type", "scene.action"), "source": "host",
                              "fields": fact.get("fields", fact)},
                })
            except Exception:
                continue
            if result and result.get("ok"):
                with self.repository.transaction() as db:
                    db.execute("UPDATE outbox SET delivered=1 WHERE event_id=?", (event_id,))
