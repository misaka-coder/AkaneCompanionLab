"""One-way completed-dialogue copies, independent of chat and delivery routing."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time

from .owner_private_session import current_private_scope
from .private_memory_mirror_store import PrivateMemoryMirrorStore

logger = logging.getLogger("akane.private_memory_mirror")
_GROUP = re.compile(r"qq_group_shared_([1-9][0-9]{0,19})\Z")


class OwnerPrivateGroupMemory:
    def __init__(self, *, engine, gateway, coordinator, bot_id, enabled):
        self.engine, self.gateway, self.coordinator = engine, gateway, coordinator
        self.bot_id = str(bot_id)
        policy = getattr(engine, "owner_private_policy", None)
        manager = engine.memcore_manager
        self.enabled = bool(enabled and gateway and engine.memcore_manager.available
                            and manager.backend == "memcore" and policy and policy.enabled and policy.master_qq)
        self.receipts = PrivateMemoryMirrorStore(engine.store.db_path)
        self._loop = None
        self._wake = None
        self._pump_task = None
        self._workers = {}
        self._imports = set()
        self._closing = False
        coordinator.bind_before_turn_listener(self.before_turn)

    def prepare_completion(self, *, system, turn_id, final_id, character_id):
        scope = current_private_scope(self.engine)
        if (not self.enabled or self._closing or scope is None or not scope.shared
                or scope.character != character_id or scope.channel not in {"desktop", "qq_private"}
                or system.namespace.user_id != "master" or system.namespace.conversation_id != "master"):
            return
        handle = system.get_turn(turn_id)
        if handle is None or str(handle.status) != "open":
            return
        # Proactive events, tool-only responses and observations are not owner input.
        users = [entry for entry in handle.stimuli if entry.kind in {"message.user", "message.user.voice"}
                 and entry.prompt_visible and entry.semantic_text.strip()]
        if not users:
            return
        targets = {}
        for group_id, generation in self.gateway._preferences.private_memory_receivers().items():
            session = f"qq_group_shared_{group_id}"
            if self.engine.store.list_trusted_qq_group_sessions(character_pack_id=character_id, exact_session_id=session, limit=1):
                targets[session] = generation
        if not targets:
            return
        actor = users[-1].namespace.actor
        label = actor.display_name if actor else ""
        if not label:
            label = self.gateway.sender_label_cache.get(self.gateway._sender_label_cache_key(
                group_id=0, user_id=int(scope.policy.master_qq)), "")
        source_key = hashlib.sha256(f"{self.bot_id}\0{character_id}\0{turn_id}".encode()).hexdigest()
        self.receipts.reserve(
            source_key=source_key, bot_id=self.bot_id, character_id=character_id, turn_id=turn_id,
            final_id=final_id, owner_qq=scope.policy.master_qq, owner_label=label, channel=scope.channel, targets=targets,
        )

    def notify_completion(self):
        if self._wake is not None and self._loop is not None and not self._loop.is_closed() and not self._closing:
            try:
                self._loop.call_soon_threadsafe(self._wake.set)
            except RuntimeError:
                # The durable receipt survives an event-loop shutdown race.
                pass

    def status(self, session):
        try:
            return self.receipts.status(self.bot_id, session)
        except Exception as exc:
            return {"unavailable": True, "reason": exc.__class__.__name__}

    async def start(self):
        if not self.enabled or self._pump_task is not None or self._closing:
            return
        self._loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
        self._pump_task = asyncio.create_task(self._pump(), name="private-memory-mirror")
        self._wake.set()

    async def _pump(self):
        while not self._closing:
            self._wake.clear()
            if self.enabled:
                try:
                    sessions = await asyncio.to_thread(self.receipts.due_sessions, self.bot_id)
                    for session in sessions:
                        if session not in self._workers:
                            task = asyncio.create_task(self._run_group(session))
                            self._workers[session] = task
                            task.add_done_callback(lambda completed, key=session: self._worker_done(key, completed))
                except Exception as exc:
                    logger.warning("mirror queue unavailable: %s", exc.__class__.__name__)
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=2)
            except asyncio.TimeoutError:
                pass

    def _worker_done(self, session, task):
        self._workers.pop(session, None)
        if not task.cancelled() and task.exception() is not None:
            logger.warning("mirror group task failed: %s", task.exception().__class__.__name__)

    async def _run_group(self, session):
        if not _GROUP.fullmatch(session):
            return
        async with self.coordinator.hold(
            session, session, turn_kind="private_memory_import", accepts_steering=False,
        ):
            await self._drain_async(session)

    async def before_turn(self, *, profile_user_id, session_id, turn_kind, **kwargs):
        if turn_kind != "private_memory_import" and profile_user_id == session_id and _GROUP.fullmatch(session_id):
            await self._drain_async(session_id)

    async def _drain_async(self, session):
        if not self.enabled or self._closing:
            return
        task = asyncio.create_task(asyncio.to_thread(self._drain, session))
        self._imports.add(task)
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            # Do not release the group boundary while its atomic writer runs.
            await asyncio.shield(task)
            raise
        except Exception as exc:
            logger.warning("mirror drain failed: %s", exc.__class__.__name__)
        finally:
            self._imports.discard(task)

    def _drain(self, session):
        group_id = _GROUP.fullmatch(session).group(1)
        manager = self.engine.memcore_manager
        try:
            jobs = self.receipts.pending(self.bot_id, session)
        except Exception as exc:
            logger.warning("mirror pending read failed: %s", exc.__class__.__name__)
            return
        for job in jobs:
            if self._closing or not self.enabled:
                break
            key = job["source_key"]
            try:
                if job["owner_qq"] != self.gateway.master_qq:
                    self.receipts.finish(key, session, "skipped_owner_changed")
                    continue
                with self.gateway._preferences.private_memory_import_guard(group_id, job["generation"]) as allowed:
                    if not allowed:
                        self.receipts.finish(key, session, "skipped_disabled")
                        continue
                    if job["available_at"] > time.time():
                        break
                    if not self.engine.store.has_trusted_qq_group_session(session, character_pack_id=job["character_id"]):
                        self.receipts.finish(key, session, "skipped_invalid_target")
                        continue
                    snapshot = manager.read_completed_private_exchange(job)
                    if snapshot["status"] == "open":
                        self.receipts.finish(key, session, "pending", delay=2)
                        break
                    if snapshot["status"] != "closed":
                        self.receipts.finish(key, session, "skipped_no_final", reason=snapshot["status"])
                        continue
                    result = manager.import_private_exchange(job, snapshot, session=session)
                    if not result.completed:
                        raise RuntimeError("mirror_import_not_completed")
                    self.receipts.finish(key, session, "applied")
            except Exception as exc:
                # Keep only a safe reason, never raw provider output or host paths.
                self.receipts.finish(key, session, "retry_pending", reason=exc.__class__.__name__,
                                     delay=min(60, 2 ** min(job["attempts"] + 1, 6)))
                logger.warning("private memory import retry: %s", exc.__class__.__name__)
                break

    async def close(self, *, timeout=10):
        self._closing = True
        if self._pump_task is not None:
            self._pump_task.cancel()
            await asyncio.gather(self._pump_task, return_exceptions=True)
        for task in list(self._workers.values()):
            task.cancel()
        pending = set(self._workers.values()) | self._imports
        if pending:
            _, remaining = await asyncio.wait(pending, timeout=timeout)
            return not remaining
        return True
