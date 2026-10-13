"""Host-private transfer adapter; the kernel only receives a complete exchange."""
from __future__ import annotations


class MemcoreCompletedHistoryMixin:
    def read_completed_private_exchange(self, job):
        system = self._get_system(profile_user_id="master", session_id="master",
                                  character_pack_id=job["character_id"], warm_index=False)
        handle = system.get_turn(job["turn_id"])
        if handle is None or str(handle.status) != "closed":
            return {"status": str(handle.status) if handle else "not_found"}
        finals = system.get_turn_entries(job["turn_id"], roles=(self._memcore_module.TurnRole.FINAL,))
        # A failed final write can be retried with a new source ID. Only the
        # unique, actually committed final of this owned turn is authoritative.
        final = finals[0] if len(finals) == 1 and finals[0].semantic_text.strip() else None
        # Real steering inputs use INTERMEDIATE, so read message kinds directly
        # without loading intervening tool calls, results or host events.
        users = [entry for entry in system.get_turn_entries(job["turn_id"], kinds=("message.user", "message.user.voice"))
                 if entry.prompt_visible and entry.semantic_text.strip()]
        owner_ids = {"master", "desktop:master", f"qq:{job['owner_qq']}"}
        if final is None or not users or any(entry.namespace.actor and entry.namespace.actor.stable_id not in owner_ids for entry in users):
            return {"status": "no_owner_dialogue"}
        return {"status": "closed", "users": users, "final": final}

    def import_private_exchange(self, job, snapshot, *, session):
        core = self._memcore_module
        system = self._get_system(profile_user_id=session, session_id=session,
                                  character_pack_id=job["character_id"], warm_index=False)
        prefix = f"private_copy.{job['source_key']}.{session}"
        owner = core.Actor(stable_id=f"qq:{job['owner_qq']}", display_name=job["owner_label"])
        users, annotations = [], []
        for index, entry in enumerate(snapshot["users"]):
            source_id = f"{prefix}.u{index}"
            actor = core.Actor(stable_id=owner.stable_id,
                               display_name=(entry.namespace.actor.display_name if entry.namespace.actor else "") or owner.display_name)
            channel = entry.payload.get("channel")
            channel = channel if channel in {"desktop", "qq_private"} else job["channel"]
            users.append(core.TimelineEntryInput(
                source_id=source_id, kind=entry.kind, origin=core.EntryOrigin.USER,
                turn_role=core.TurnRole.STIMULUS, semantic_text=entry.semantic_text,
                timestamp=entry.timestamp, actor=actor, payload={"channel": channel},
                trace_metadata={"source_conversation": "master", "source_id": entry.source_id, "source_turn_id": job["turn_id"]},
                retrieval_policy=core.RetrievalPolicy.AUTO,
            ))
            status = entry.annotation_status
            if str(status) in {"unannotated", "derived", "derived_turn_final"}:
                status = core.AnnotationStatus.MISSING
            annotations.append(core.MemoryAnnotation(source_id, status, dict(entry.memory_metadata), source="host"))
        final = snapshot["final"]
        result = system.import_completed_turn(
            turn_id=f"{prefix}.turn", stimuli=users, annotations=annotations,
            final=core.TimelineEntryInput(
                source_id=f"{prefix}.a", kind=final.kind, origin=core.EntryOrigin.ASSISTANT,
                turn_role=core.TurnRole.FINAL, semantic_text=final.semantic_text, timestamp=final.timestamp,
                target_actor=owner, payload={"channel": job["channel"]},
                trace_metadata={"source_conversation": "master", "source_id": final.source_id, "source_turn_id": job["turn_id"]},
            ),
            provider_output_raw=str(final.payload.get("provider_output_raw") or ""), defer_index=True,
        )
        if result.completed:
            # Indexing is host-supervised; shutdown waits before closing the store.
            with self._lock:
                if not self._closing and not self._closed:
                    future = self._runtime.submit_index_repair(system.reindex_pending, limit=100, batch_size=64)
                    self._track_background_future_locked(future)
        return result
