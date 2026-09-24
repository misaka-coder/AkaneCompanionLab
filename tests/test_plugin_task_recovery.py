from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from types import SimpleNamespace

from companion_v01.host_jobs import HostJobOwner, HostJobStore
from companion_v01.plugin_tasks import HostTaskProvider


def _invocation(
    plugin_id: str,
    generation_id: str,
    *,
    profile: str = "profile",
    session: str = "session",
    character: str = "",
):
    return SimpleNamespace(
        active=True,
        plugin_id=plugin_id,
        generation_id=generation_id,
        context=SimpleNamespace(
            global_scope=False,
            profile_user_id=profile,
            session_id=session,
            character_pack_id=character,
        ),
    )


class PluginTaskRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def _create(self, provider: HostTaskProvider, invocation, *, key: str, pause_mode: str = "unavailable"):
        created = await provider.request(
            "create",
            {
                "task_type": "game.match",
                "payload": {"game_id": key},
                "idempotency_key": f"game:{key}",
                "pause_mode": pause_mode,
            },
            invocation=invocation,
        )
        self.assertEqual(created["status"], "created", created)
        started = await provider.request(
            "update", {"task_id": created["task_id"], "status": "running"}, invocation=invocation
        )
        self.assertEqual(started["status"], "running", started)
        return created["task_id"]

    async def test_checkpoint_survives_store_reopen_and_rejects_reordering(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "jobs.db"
            owner = HostJobOwner("profile", "session")
            provider = HostTaskProvider(HostJobStore(path))
            invocation = _invocation("plugin.game", "generation-1")
            task_id = await self._create(provider, invocation, key="persisted")

            first = await provider.request(
                "checkpoint",
                {"task_id": task_id, "value": {"state_version": 1, "enemy_hp": 10}, "version": 1},
                invocation=invocation,
            )
            duplicate = await provider.request(
                "checkpoint",
                {"task_id": task_id, "value": {"state_version": 1, "enemy_hp": 10}, "version": 1},
                invocation=invocation,
            )
            conflict = await provider.request(
                "checkpoint",
                {"task_id": task_id, "value": {"state_version": 1, "enemy_hp": 9}, "version": 1},
                invocation=invocation,
            )
            newer = await provider.request(
                "checkpoint",
                {"task_id": task_id, "value": {"state_version": 2, "enemy_hp": 7}, "version": 2},
                invocation=invocation,
            )
            stale = await provider.request(
                "checkpoint",
                {"task_id": task_id, "value": {"state_version": 1, "enemy_hp": 8}, "version": 1},
                invocation=invocation,
            )

            self.assertEqual(first["status"], "running")
            self.assertEqual(first["reason"], "task_checkpoint_saved")
            self.assertEqual(duplicate["status"], "running")
            self.assertEqual(duplicate["reason"], "task_checkpoint_already_saved")
            self.assertEqual(conflict["status"], "running")
            self.assertEqual(conflict["reason"], "task_checkpoint_version_conflict")
            self.assertFalse(conflict["complete"])
            self.assertEqual(newer["status"], "running")
            self.assertEqual(newer["reason"], "task_checkpoint_saved")
            self.assertEqual(stale["status"], "running")
            self.assertEqual(stale["reason"], "task_checkpoint_version_stale")
            self.assertFalse(stale["complete"])

            reopened = HostTaskProvider(HostJobStore(path))
            status = await reopened.request("status", {"task_id": task_id}, invocation=invocation)
            self.assertEqual(status["checkpoint"], {"state_version": 2, "enemy_hp": 7})
            self.assertEqual(status["checkpoint_version"], 2)
            self.assertEqual(reopened.store.get(task_id, owner=owner).checkpoint_version, 2)

    async def test_checkpoint_is_scoped_and_recovery_requires_explicit_adoption(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "jobs.db"
            store = HostJobStore(path)
            old = _invocation("plugin.game", "generation-old")
            task_id = await self._create(HostTaskProvider(store), old, key="recovery")
            saved = await HostTaskProvider(store).request(
                "checkpoint", {"task_id": task_id, "value": {"state_version": 1}, "version": 1}, invocation=old
            )
            self.assertEqual(saved["status"], "running")
            self.assertEqual(saved["reason"], "task_checkpoint_saved")

            wrong_session = _invocation("plugin.game", "generation-old", session="other-session")
            missing = await HostTaskProvider(store).request("status", {"task_id": task_id}, invocation=wrong_session)
            self.assertEqual(missing["reason"], "task_not_found")

            wrong_plugin = _invocation("plugin.other", "generation-old")
            denied = await HostTaskProvider(store).request("status", {"task_id": task_id}, invocation=wrong_plugin)
            self.assertEqual(denied["reason"], "task_access_denied")

            self.assertEqual(store.recover_abandoned_claims(), 1)

            current = _invocation("plugin.game", "generation-new")
            current_provider = HostTaskProvider(
                store,
                generation_active=lambda plugin_id, generation_id: (plugin_id, generation_id) == ("plugin.game", "generation-new"),
            )
            opened = await current_provider.request(
                "open", {"task_id": task_id, "recover": True}, invocation=current
            )
            self.assertEqual(opened["status"], "recovery_unknown")
            self.assertEqual(opened["checkpoint"], {"state_version": 1})

            queued = await current_provider.request("resume", {"task_id": task_id}, invocation=current)
            self.assertEqual(queued["status"], "created")
            restarted = await current_provider.request(
                "update", {"task_id": task_id, "status": "running"}, invocation=current
            )
            self.assertEqual(restarted["status"], "running")
            self.assertEqual(restarted["checkpoint_version"], 1)

            late_old = await current_provider.request(
                "status", {"task_id": task_id}, invocation=old
            )
            self.assertEqual(late_old["status"], "stale")
            late_old_without_generation_callback = await HostTaskProvider(store).request(
                "status", {"task_id": task_id}, invocation=old
            )
            self.assertEqual(late_old_without_generation_callback["status"], "stale")
            late_checkpoint = await HostTaskProvider(store).request(
                "checkpoint",
                {"task_id": task_id, "value": {"state_version": 3}, "version": 3},
                invocation=old,
            )
            self.assertEqual(late_checkpoint["status"], "stale")
            recovered_job = store.get(task_id, owner=HostJobOwner("profile", "session"))
            self.assertEqual(recovered_job.recovery_generation_id, "generation-new")
            self.assertFalse(recovered_job.scope_revoked_reason)

    async def test_pause_reports_real_cooperation_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = HostJobStore(Path(temp_dir) / "jobs.db")
            provider = HostTaskProvider(store)
            unavailable = _invocation("plugin.game", "generation-unavailable")
            task_id = await self._create(provider, unavailable, key="not-pausable")
            rejected = await provider.request("pause", {"task_id": task_id}, invocation=unavailable)
            self.assertEqual(rejected["status"], "pause_unavailable")
            self.assertEqual((await provider.request("status", {"task_id": task_id}, invocation=unavailable))["status"], "running")

            cooperative = _invocation("plugin.game", "generation-cooperative")
            task_id = await self._create(provider, cooperative, key="pausable", pause_mode="cooperative")
            no_checkpoint = await provider.request("pause_boundary", {"task_id": task_id}, invocation=cooperative)
            self.assertEqual(no_checkpoint["status"], "pause_unavailable")
            await provider.request(
                "checkpoint", {"task_id": task_id, "value": {"state_version": 1}, "version": 1}, invocation=cooperative
            )
            paused = await provider.request("pause_boundary", {"task_id": task_id}, invocation=cooperative)
            self.assertEqual(paused["status"], "paused")
            resumed = await provider.request("resume", {"task_id": task_id}, invocation=cooperative)
            self.assertEqual(resumed["status"], "running")

    async def test_recovery_can_be_adopted_again_after_a_second_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "jobs.db"
            store = HostJobStore(path)
            old = _invocation("plugin.game", "generation-1")
            task_id = await self._create(HostTaskProvider(store), old, key="two-restarts")
            await HostTaskProvider(store).request(
                "checkpoint",
                {"task_id": task_id, "value": {"state_version": 1}, "version": 1},
                invocation=old,
            )

            self.assertEqual(store.recover_abandoned_claims(), 1)
            active = {("plugin.game", "generation-2")}
            g2 = _invocation("plugin.game", "generation-2")
            provider2 = HostTaskProvider(
                store,
                generation_active=lambda plugin_id, generation_id: (plugin_id, generation_id) in active,
            )
            adopted = await provider2.request("open", {"task_id": task_id, "recover": True}, invocation=g2)
            self.assertEqual(adopted["status"], "recovery_unknown")
            self.assertEqual((await provider2.request("resume", {"task_id": task_id}, invocation=g2))["status"], "created")
            self.assertEqual(
                (await provider2.request("update", {"task_id": task_id, "status": "running"}, invocation=g2))["status"],
                "running",
            )

            self.assertEqual(store.recover_abandoned_claims(), 1)
            active.clear()
            active.add(("plugin.game", "generation-3"))
            g3 = _invocation("plugin.game", "generation-3")
            provider3 = HostTaskProvider(
                store,
                generation_active=lambda plugin_id, generation_id: (plugin_id, generation_id) in active,
            )
            adopted_again = await provider3.request("open", {"task_id": task_id, "recover": True}, invocation=g3)
            self.assertEqual(adopted_again["status"], "recovery_unknown")
            self.assertEqual(adopted_again["checkpoint"], {"state_version": 1})
            self.assertEqual((await provider3.request("resume", {"task_id": task_id}, invocation=g3))["status"], "created")
            self.assertEqual(
                (await provider3.request("update", {"task_id": task_id, "status": "running"}, invocation=g3))["status"],
                "running",
            )
            recovered = store.get(task_id, owner=HostJobOwner("profile", "session"))
            self.assertEqual(recovered.recovery_generation_id, "generation-3")
            self.assertEqual(recovered.recovery_generation_epoch, 2)
            self.assertEqual(recovered.recovery_epoch, 2)

    async def test_task_scope_includes_character_pack_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = HostJobStore(Path(temp_dir) / "jobs.db")
            provider = HostTaskProvider(store)
            akane = _invocation("plugin.game", "generation-1", character="akane")
            task_id = await self._create(provider, akane, key="character-scope")
            saved = await provider.request(
                "checkpoint",
                {"task_id": task_id, "value": {"state_version": 1}, "version": 1},
                invocation=akane,
            )
            self.assertEqual(saved["status"], "running")

            same_character = await provider.request("status", {"task_id": task_id}, invocation=akane)
            self.assertEqual(same_character["checkpoint"], {"state_version": 1})
            wrong_character = _invocation("plugin.game", "generation-1", character="kaju")
            denied = await provider.request("checkpoint_status", {"task_id": task_id}, invocation=wrong_character)
            self.assertEqual(denied["status"], "rejected")
            self.assertEqual(denied["reason"], "task_access_denied")
            self.assertIsNone(denied["checkpoint"])

    async def test_running_cancel_waits_for_executor_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = HostJobStore(Path(temp_dir) / "jobs.db")
            provider = HostTaskProvider(store)
            invocation = _invocation("plugin.game", "generation-1")
            task_id = await self._create(provider, invocation, key="cooperative-cancel")

            requested = await provider.request("cancel", {"task_id": task_id}, invocation=invocation)
            self.assertEqual(requested["status"], "cancelling")
            self.assertFalse(requested["complete"])
            self.assertTrue(requested["cancel_requested"])
            self.assertEqual(
                (await provider.request("status", {"task_id": task_id}, invocation=invocation))["status"],
                "cancelling",
            )

            completed = await provider.request(
                "update", {"task_id": task_id, "status": "completed", "result": {"ok": True}}, invocation=invocation
            )
            self.assertEqual(completed["status"], "completed")
            self.assertTrue(completed["complete"])

            second_id = await self._create(provider, invocation, key="cooperative-cancel-confirmed")
            await provider.request("cancel", {"task_id": second_id}, invocation=invocation)
            confirmed = await provider.request(
                "update", {"task_id": second_id, "status": "cancelled"}, invocation=invocation
            )
            self.assertEqual(confirmed["status"], "cancelled")

    async def test_generation_withdrawal_fences_late_worker_completion(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = HostJobStore(Path(temp_dir) / "jobs.db")
            provider = HostTaskProvider(store)
            invocation = _invocation("plugin.game", "generation-1")
            task_id = await self._create(provider, invocation, key="late-worker")
            before = store.get(task_id, owner=HostJobOwner("profile", "session"))
            claim_token = before.claim_token

            provider.revoke_generation("plugin.game", "generation-1")
            late = store.succeed(task_id, claim_token=claim_token, result={"late": True})
            self.assertEqual(late["status"], "stale")
            after = store.get(task_id, owner=HostJobOwner("profile", "session"))
            self.assertEqual(after.status, "running")
            self.assertEqual(after.scope_revoked_reason, "plugin_task_generation_revoked")

    async def test_generation_withdrawal_after_recovery_revokes_current_generation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "jobs.db"
            store = HostJobStore(path)
            old = _invocation("plugin.game", "generation-old")
            task_id = await self._create(HostTaskProvider(store), old, key="recovered-revocation")
            await HostTaskProvider(store).request(
                "checkpoint",
                {"task_id": task_id, "value": {"state_version": 1}, "version": 1},
                invocation=old,
            )
            self.assertEqual(store.recover_abandoned_claims(), 1)

            current = _invocation("plugin.game", "generation-new")
            provider = HostTaskProvider(store)
            adopted = await provider.request("open", {"task_id": task_id, "recover": True}, invocation=current)
            self.assertEqual(adopted["status"], "recovery_unknown")
            self.assertEqual((await provider.request("resume", {"task_id": task_id}, invocation=current))["status"], "created")
            self.assertEqual(
                (await provider.request("update", {"task_id": task_id, "status": "running"}, invocation=current))["status"],
                "running",
            )

            revoked = provider.revoke_generation("plugin.game", "generation-new")
            self.assertEqual([item.job_id for item in revoked], [task_id])
            late = await provider.request("status", {"task_id": task_id}, invocation=current)
            self.assertEqual(late["status"], "stale")
            recovered = store.get(task_id, owner=HostJobOwner("profile", "session"))
            self.assertEqual(recovered.scope_revoked_reason, "plugin_task_generation_revoked")
            self.assertTrue(recovered.cancel_requested)

    async def test_checkpoint_and_recovery_cross_a_real_python_process(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "jobs.db"
            store = HostJobStore(path)
            old = _invocation("plugin.game", "generation-process")
            task_id = await self._create(HostTaskProvider(store), old, key="process-restart")
            saved = await HostTaskProvider(store).request(
                "checkpoint",
                {"task_id": task_id, "value": {"state_version": 4, "turn": 9}, "version": 4},
                invocation=old,
            )
            self.assertEqual(saved["reason"], "task_checkpoint_saved")

            code = textwrap.dedent(
                """
                import json
                import sys
                from companion_v01.host_jobs import HostJobOwner, HostJobStore

                store = HostJobStore(sys.argv[1])
                before = store.get(sys.argv[2], owner=HostJobOwner("profile", "session"))
                recovered_count = store.recover_abandoned_claims()
                after = store.get(sys.argv[2], owner=HostJobOwner("profile", "session"))
                print(json.dumps({
                    "before_status": before.status,
                    "before_checkpoint": before.checkpoint,
                    "recovered_count": recovered_count,
                    "after_status": after.status,
                    "after_recovery_state": after.recovery_state,
                    "after_checkpoint": after.checkpoint,
                    "after_checkpoint_version": after.checkpoint_version,
                }, ensure_ascii=False))
                """
            )
            completed = subprocess.run(
                [sys.executable, "-c", code, str(path), task_id],
                cwd=Path(__file__).resolve().parent.parent,
                capture_output=True,
                text=True,
                check=True,
                timeout=20,
            )
            payload = json.loads(completed.stdout.strip())
            self.assertEqual(payload["before_status"], "running")
            self.assertEqual(payload["before_checkpoint"], {"state_version": 4, "turn": 9})
            self.assertEqual(payload["recovered_count"], 1)
            self.assertEqual(payload["after_status"], "failed")
            self.assertEqual(payload["after_recovery_state"], "unknown")
            self.assertEqual(payload["after_checkpoint"], {"state_version": 4, "turn": 9})
            self.assertEqual(payload["after_checkpoint_version"], 4)

    async def test_adoption_that_never_claimed_the_work_can_be_handed_over_once(self) -> None:
        """A generation that adopts a round but never re-claims it must not block.

        The reported defect: after the first restart a second generation adopted
        the round and crashed before claiming, so no second loss occurred and the
        recorded owner epoch could never be overtaken again. The task stayed
        ``recovery_unknown`` forever. A live generation must be able to take the
        round over, and taking it over must still be fenced.
        """

        with tempfile.TemporaryDirectory() as temp_dir:
            store = HostJobStore(Path(temp_dir) / "jobs.db")
            owner = HostJobOwner("profile", "session")
            active = {("plugin.game", "generation-1")}
            live = lambda plugin_id, generation_id: (plugin_id, generation_id) in active  # noqa: E731

            g1 = _invocation("plugin.game", "generation-1")
            task_id = await self._create(
                HostTaskProvider(store, generation_active=live), g1, key="adopt-without-claim"
            )
            await HostTaskProvider(store).request(
                "checkpoint", {"task_id": task_id, "value": {"state_version": 1}, "version": 1}, invocation=g1
            )
            self.assertEqual(store.recover_abandoned_claims(), 1)

            active.clear()
            active.add(("plugin.game", "generation-2"))
            g2 = _invocation("plugin.game", "generation-2")
            provider2 = HostTaskProvider(store, generation_active=live)
            adopted = await provider2.request("open", {"task_id": task_id, "recover": True}, invocation=g2)
            self.assertEqual(adopted["status"], "recovery_unknown")
            # g2 was given the round and then crashed before claiming anything.
            self.assertEqual(store.recover_abandoned_claims(), 0)
            self.assertEqual(store.get(task_id, owner=owner).recovery_state, "unknown")

            active.clear()
            active.add(("plugin.game", "generation-3"))
            g3 = _invocation("plugin.game", "generation-3")
            provider3 = HostTaskProvider(store, generation_active=live)
            handed_over = await provider3.request("open", {"task_id": task_id, "recover": True}, invocation=g3)
            self.assertEqual(handed_over["status"], "recovery_unknown", handed_over)
            self.assertEqual(handed_over["checkpoint"], {"state_version": 1})
            self.assertEqual(handed_over["reason"], "task_recovery_checkpoint_loaded")

            # The handoff is exclusive: no fourth generation may take the same round.
            active.add(("plugin.game", "generation-4"))
            active.add(("plugin.game", "generation-2"))  # g2 is loaded again, but no longer owns the round
            g4 = _invocation("plugin.game", "generation-4")
            provider4 = HostTaskProvider(store, generation_active=live)
            taken = await provider4.request("open", {"task_id": task_id, "recover": True}, invocation=g4)
            self.assertEqual(taken["status"], "rejected")
            self.assertEqual(taken["reason"], "task_recovery_already_adopted")
            self.assertFalse(taken["complete"])

            # g3 can still finish the round it owns.
            self.assertEqual((await provider3.request("resume", {"task_id": task_id}, invocation=g3))["status"], "created")
            self.assertEqual(
                (await provider3.request("update", {"task_id": task_id, "status": "running"}, invocation=g3))["status"],
                "running",
            )
            recovered = store.get(task_id, owner=owner)
            self.assertEqual(recovered.recovery_generation_id, "generation-3")
            self.assertEqual(recovered.status, "running")
            # g2 lost the handoff and can no longer act on the task: its generation
            # is no longer the recovery owner, so the host reports the real reason.
            late_g2 = await provider2.request("status", {"task_id": task_id}, invocation=g2)
            self.assertEqual(late_g2["status"], "stale")
            self.assertEqual(late_g2["reason"], "task_generation_expired")
            self.assertFalse(late_g2["complete"])
            self.assertIsNone(late_g2["checkpoint"])

    async def test_recovery_handoff_requires_a_real_loss_or_an_unclaimed_round(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = HostJobStore(Path(temp_dir) / "jobs.db")
            owner = HostJobOwner("profile", "session")
            active = {("plugin.game", "generation-1")}
            live = lambda plugin_id, generation_id: (plugin_id, generation_id) in active  # noqa: E731

            g1 = _invocation("plugin.game", "generation-1")
            task_id = await self._create(HostTaskProvider(store, generation_active=live), g1, key="loss-required")
            self.assertEqual(store.recover_abandoned_claims(), 1)

            active.clear()
            active.add(("plugin.game", "generation-2"))
            g2 = _invocation("plugin.game", "generation-2")
            provider2 = HostTaskProvider(store, generation_active=live)
            self.assertEqual(
                (await provider2.request("open", {"task_id": task_id, "recover": True}, invocation=g2))["status"],
                "recovery_unknown",
            )
            self.assertEqual((await provider2.request("resume", {"task_id": task_id}, invocation=g2))["status"], "created")
            self.assertEqual(
                (await provider2.request("update", {"task_id": task_id, "status": "running"}, invocation=g2))["status"],
                "running",
            )

            # g2 holds a live claim with no loss since: a live owner is never preempted.
            active.add(("plugin.game", "generation-3"))
            g3 = _invocation("plugin.game", "generation-3")
            provider3 = HostTaskProvider(store, generation_active=live)
            stolen = await provider3.request("open", {"task_id": task_id, "recover": True}, invocation=g3)
            self.assertEqual(stolen["status"], "rejected")
            self.assertEqual(stolen["reason"], "task_recovery_already_adopted")
            self.assertFalse(stolen["complete"])
            self.assertEqual(store.get(task_id, owner=owner).recovery_generation_id, "generation-2")

            # A real loss finally justifies the takeover.
            self.assertEqual(store.recover_abandoned_claims(), 1)
            taken = await provider3.request("open", {"task_id": task_id, "recover": True}, invocation=g3)
            self.assertEqual(taken["status"], "recovery_unknown", taken)
            self.assertEqual((await provider3.request("resume", {"task_id": task_id}, invocation=g3))["status"], "created")
            recovered = store.get(task_id, owner=owner)
            self.assertEqual(recovered.recovery_generation_id, "generation-3")
            self.assertEqual(recovered.recovery_generation_epoch, 2)
            self.assertEqual(recovered.recovery_epoch, 2)

    async def test_two_real_process_restarts_hand_the_task_over_and_keep_the_epoch_monotonic(self) -> None:
        """Full lifecycle: two real process restarts, then a live generation resumes."""

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "jobs.db"
            store = HostJobStore(path)
            owner = HostJobOwner("profile", "session")
            g1 = _invocation("plugin.game", "generation-1")
            task_id = await self._create(HostTaskProvider(store), g1, key="two-process-restarts")
            await HostTaskProvider(store).request(
                "checkpoint",
                {"task_id": task_id, "value": {"state_version": 7, "turn": 3}, "version": 7},
                invocation=g1,
            )

            def restart(generation: str) -> dict:
                code = textwrap.dedent(
                    """
                    import json
                    import sys
                    from companion_v01.host_jobs import HostJobOwner, HostJobStore

                    store = HostJobStore(sys.argv[1])
                    owner = HostJobOwner("profile", "session")
                    recovered = store.recover_abandoned_claims()
                    job = store.get(sys.argv[2], owner=owner)
                    print(json.dumps({
                        "recovered": recovered,
                        "status": job.status,
                        "recovery_state": job.recovery_state,
                        "recovery_epoch": job.recovery_epoch,
                        "recovery_generation_epoch": job.recovery_generation_epoch,
                        "checkpoint": job.checkpoint,
                    }, ensure_ascii=False))
                    """
                )
                completed = subprocess.run(
                    [sys.executable, "-c", code, str(path), task_id],
                    cwd=Path(__file__).resolve().parent.parent,
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=20,
                )
                return json.loads(completed.stdout.strip())

            first = restart("generation-1")
            self.assertEqual(first["recovered"], 1)
            self.assertEqual(first["recovery_state"], "unknown")
            self.assertEqual(first["recovery_epoch"], 1)

            active = {("plugin.game", "generation-2")}
            live = lambda plugin_id, generation_id: (plugin_id, generation_id) in active  # noqa: E731
            g2 = _invocation("plugin.game", "generation-2")
            provider2 = HostTaskProvider(store, generation_active=live)
            adopted = await provider2.request("open", {"task_id": task_id, "recover": True}, invocation=g2)
            self.assertEqual(adopted["status"], "recovery_unknown")
            self.assertEqual(adopted["checkpoint"], {"state_version": 7, "turn": 3})
            self.assertEqual((await provider2.request("resume", {"task_id": task_id}, invocation=g2))["status"], "created")
            self.assertEqual(
                (await provider2.request("update", {"task_id": task_id, "status": "running"}, invocation=g2))["status"],
                "running",
            )

            second = restart("generation-2")
            self.assertEqual(second["recovered"], 1)
            self.assertEqual(second["recovery_epoch"], 2)
            self.assertEqual(second["checkpoint"], {"state_version": 7, "turn": 3})

            active.clear()
            active.add(("plugin.game", "generation-3"))
            g3 = _invocation("plugin.game", "generation-3")
            provider3 = HostTaskProvider(store, generation_active=live)
            adopted_again = await provider3.request("open", {"task_id": task_id, "recover": True}, invocation=g3)
            self.assertEqual(adopted_again["status"], "recovery_unknown", adopted_again)
            self.assertEqual(adopted_again["checkpoint"], {"state_version": 7, "turn": 3})
            self.assertEqual((await provider3.request("resume", {"task_id": task_id}, invocation=g3))["status"], "created")
            self.assertEqual(
                (await provider3.request("update", {"task_id": task_id, "status": "running"}, invocation=g3))["status"],
                "running",
            )
            final = store.get(task_id, owner=owner)
            self.assertEqual(final.recovery_generation_id, "generation-3")
            self.assertEqual(final.recovery_generation_epoch, 2)
            self.assertEqual(final.recovery_epoch, 2)
            self.assertEqual(final.checkpoint, {"state_version": 7, "turn": 3})
            self.assertEqual(final.checkpoint_version, 7)

    async def test_expired_scope_is_not_a_terminal_task_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = HostJobStore(Path(temp_dir) / "jobs.db")
            provider = HostTaskProvider(store)
            invocation = _invocation("plugin.game", "generation-1")
            task_id = await self._create(provider, invocation, key="expired-scope")

            # An explicitly inactive generation is what a replaced generation looks
            # like to the provider. The first such request withdraws the task from
            # that generation and reports the real reason, without claiming any
            # terminal task state.
            replaced = HostTaskProvider(store, generation_active=lambda plugin_id, generation_id: False)
            late = await replaced.request("status", {"task_id": task_id}, invocation=invocation)
            self.assertEqual(late["status"], "stale")
            self.assertEqual(late["reason"], "task_generation_expired")
            self.assertTrue(late["scope_expired"])
            self.assertFalse(late["complete"])
            late_update = await replaced.request(
                "update", {"task_id": task_id, "status": "completed"}, invocation=invocation
            )
            self.assertFalse(late_update["complete"])
            self.assertTrue(late_update["scope_expired"])

            # The task was withdrawn but never rewritten into a terminal state.
            withdrawn = store.get(task_id, owner=HostJobOwner("profile", "session"))
            self.assertEqual(withdrawn.status, "running")
            self.assertEqual(withdrawn.scope_revoked_reason, "plugin_task_generation_revoked")
            # Withdrawal records a stop request, but the row is not rewritten into a
            # terminal state and the running claim is left for its real owner.
            self.assertTrue(withdrawn.cancel_requested)
            self.assertTrue(withdrawn.claim_token)

            second = await replaced.request("status", {"task_id": task_id}, invocation=invocation)
            self.assertEqual(second["status"], "stale")
            self.assertFalse(second["complete"])

            after_revoke = provider.revoke_generation("plugin.game", "generation-1")
            self.assertEqual(after_revoke, ())

    async def test_cancel_reports_a_request_until_the_plugin_confirms_the_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = HostJobStore(Path(temp_dir) / "jobs.db")
            provider = HostTaskProvider(store)
            invocation = _invocation("plugin.game", "generation-1")
            task_id = await self._create(provider, invocation, key="cancel-boundary")
            owner = HostJobOwner("profile", "session")

            requested = await provider.request("cancel", {"task_id": task_id}, invocation=invocation)
            self.assertEqual(requested["status"], "cancelling")
            self.assertTrue(requested["cancel_requested"])
            self.assertFalse(requested["complete"])
            # Cancellation is a durable request, not evidence that execution stopped.
            row = store.get(task_id, owner=owner)
            self.assertEqual(row.status, "running")
            self.assertEqual(row.control_state, "stopping")
            self.assertTrue(row.claim_token)
            self.assertEqual(row.cancel_requested, 1)

            # Only the plugin's own confirmed boundary turns that into a terminal task.
            confirmed = await provider.request(
                "update", {"task_id": task_id, "status": "cancelled"}, invocation=invocation
            )
            self.assertEqual(confirmed["status"], "cancelled")
            self.assertTrue(confirmed["complete"])
            self.assertEqual(store.get(task_id, owner=owner).status, "cancelled")


if __name__ == "__main__":
    unittest.main()
