"""Write-ahead intents bridge the room transaction to the existing Care authority."""
import json
import uuid

from ..contracts import ActionRequest, Fact
from ..repositories.room import room_key


class ActionJournal:
    def __init__(self, room):
        self.room = room
        with room.repository.transaction() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS scene_action_intents (
                scope TEXT NOT NULL, request_id TEXT NOT NULL, request TEXT NOT NULL,
                PRIMARY KEY(scope, request_id))""")

    def recover(self, db, identity):
        rows = db.execute("SELECT request FROM scene_action_intents WHERE scope=? ORDER BY rowid",
                          (room_key(identity),)).fetchall()
        for (raw,) in rows:
            request = ActionRequest.model_validate_json(raw)
            state = self.room.repository.read(db, request.identity)
            catalog = self.room.resources.build(request.identity.character_pack_id)
            result = self.room._apply(request, state, catalog)
            if result["ok"]:
                state.revision += 1
                self.room.repository.save(db, request.identity, state)
            fact = Fact(
                event_id=uuid.uuid4().hex,
                kind=request.kind,
                target=request.target,
                quantity=int(result.get("quantity") or getattr(request, "count", 1)),
                ok=result["ok"],
                reason=result["reason"],
                item_name=str(result.get("item_name") or ""),
                effects=result.get("effects") or {},
            )
            db.execute("INSERT INTO outbox(event_id,identity,fact) VALUES (?,?,?)",
                       (fact.event_id, request.identity.model_dump_json(), fact.model_dump_json()))
            action_record = {"ok": result["ok"], "reason": result["reason"], "event": fact.model_dump()}
            db.execute("INSERT INTO actions VALUES (?,?,?,?)", (
                room_key(request.identity), request.request_id, self.fingerprint(request),
                json.dumps(action_record, ensure_ascii=False),
            ))
            db.execute("DELETE FROM scene_action_intents WHERE scope=? AND request_id=?",
                       (room_key(request.identity), request.request_id))

    @staticmethod
    def fingerprint(request):
        return json.dumps([request.kind, request.target, getattr(request, "count", 1)], ensure_ascii=False)

    def execute(self, request):
        key = room_key(request.identity)
        duplicate = False
        with self.room.repository.transaction() as db:
            self.recover(db, request.identity)
            row = db.execute("SELECT fingerprint,result FROM actions WHERE scope=? AND request_id=?",
                             (key, request.request_id)).fetchone()
            if row:
                if row[0] != self.fingerprint(request):
                    return {"ok": False, "reason": "request_id_conflict"}, False
                return json.loads(row[1]), True
            state = self.room.repository.read(db, request.identity)
            if state.revision != request.expected_revision:
                return {"ok": False, "reason": "revision_conflict"}, False
            db.execute("INSERT INTO scene_action_intents VALUES (?,?,?)", (key, request.request_id, request.model_dump_json()))
        # The intent is durable before any consumable mutation. If a process
        # dies after Care's atomic save, recover repeats the SAME Care receipt.
        with self.room.repository.transaction() as db:
            self.recover(db, request.identity)
            row = db.execute("SELECT result FROM actions WHERE scope=? AND request_id=?", (key, request.request_id)).fetchone()
        return json.loads(row[0]), duplicate
