from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from companion_v01.care_runtime import CareModulePort, CareRuntimeStore
from companion_v01.client_protocol import ClientMode, ClientProtocolContext
from companion_v01.engine_services.desktop_care_pet import DesktopCareAndPetMixin
from companion_v01.owner_private_session import OwnerPrivateSessionPolicy
from companion_v01.plugin_conversation_refs import PluginConversationReferenceAuthority


class _CareHost(DesktopCareAndPetMixin):
    def _resolve_payload_character_pack_id(self, payload):
        return str(payload.get("character_pack_id") or "")

    def _load_desktop_care_config(self, character_pack_id):
        return {
            "enabled": True,
            "shop_items": [
                {"id": "tea", "name": "茶", "price": 6, "effects": {"hunger": 20, "energy": 3, "affection": 2}}
            ],
        }


class CareOwnerBindingTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.store = CareRuntimeStore(root / "care.json")
        self.refs = PluginConversationReferenceAuthority(root / "refs.key", instance_id="care-test")
        self.host = _CareHost()
        self.host.care_module = CareModulePort(enabled=True, _runtime=self.store)
        self.policy = OwnerPrivateSessionPolicy(
            enabled=True,
            master_qq="12345",
            references=self.refs,
            manager=SimpleNamespace(),
        )
        self.host.owner_private_policy = self.policy
        self.base = dict(
            profile_user_id="master",
            character_pack_id="cecilia",
            relation_user_id="qq:12345",
            conversation_id="master",
            now_ms=1000,
        )
        self.store.sync_from_client(
            **self.base, client_mode="qq_text", care_payload={"hunger": 35, "energy": 40, "affection": 15}
        )

    def desktop(self, session="desktop-one", *, character="cecilia", authenticated=True):
        payload = dict(user_id=session, real_user_id="master", character_pack_id=character, client_mode="desktop_pet")
        self.policy.stamp_desktop(payload, owner_authenticated=authenticated, character=character)
        return payload

    def desktop_snapshot(self, payload, **kwargs):
        return self.host.build_desktop_care_snapshot(
            profile_user_id=payload["real_user_id"],
            session_id=payload["user_id"],
            character_pack_id=payload["character_pack_id"],
            conversation_ref=payload["_memory_desktop_ref"],
            now_ms=1000,
            **kwargs,
        )["snapshot"]

    def qq_snapshot(self, *, session="master", profile="master", user=12345, group=0):
        payload = dict(
            user_id=session,
            real_user_id=profile,
            character_pack_id="cecilia",
            qq_delivery_context={"user_id": user, "group_id": group},
        )
        payload["_memory_qq_ref"] = self.refs.issue_qq(
            profile_user_id=profile,
            session_id=session,
            character_pack_id="cecilia",
            user_id=user,
            group_id=group,
        )
        context = ClientProtocolContext(requested_mode=ClientMode.QQ_TEXT, effective_mode=ClientMode.QQ_TEXT)
        with self.policy.scope(payload):
            return self.host._prepare_care_context_for_turn(
                payload,
                context,
                profile_user_id=profile,
                character_pack_id="cecilia",
                now_ts=1,
            )["desktop_care"]

    def test_verified_desktops_and_owner_private_share_feeding_without_legacy_overwrite(self):
        first, second = self.desktop(), self.desktop("desktop-two")
        snapshot = self.desktop_snapshot(first, legacy_state={"hunger": 1, "energy": 1, "coins": 9999})
        self.assertEqual((snapshot["hunger"], snapshot["energy"], snapshot["coins"]), (35, 40, 0))
        self.store.grant_to_inventory(
            **self.base, item_id="tea", item_name="茶", item_effects={"hunger": 20, "energy": 3, "affection": 2}
        )
        arguments = dict(
            profile_user_id="master",
            character_pack_id="cecilia",
            action="feed",
            item_id="tea",
            request_id="shared-feed",
            now_ms=1000,
        )
        fed = self.host.manage_desktop_care_action(
            **arguments,
            session_id=first["user_id"],
            conversation_ref=first["_memory_desktop_ref"],
        )
        replayed = self.host.manage_desktop_care_action(
            **arguments,
            session_id=second["user_id"],
            conversation_ref=second["_memory_desktop_ref"],
        )
        self.assertEqual(replayed, fed)
        for observed in (self.qq_snapshot(), self.desktop_snapshot(first), self.desktop_snapshot(second)):
            self.assertEqual((observed["hunger"], observed["energy"], observed["affection"]), (55, 43, 17))
            self.assertEqual(observed["inventory"], {})

    def test_group_and_other_private_messages_keep_their_own_body(self):
        for arguments in (
            dict(session="group-87", group=87),
            dict(session="other-private", profile="guest", user=54321),
        ):
            with self.subTest(arguments=arguments):
                snapshot = self.qq_snapshot(**arguments)
                self.assertEqual((snapshot["hunger"], snapshot["energy"]), (55, 70))
        self.assertEqual((self.qq_snapshot()["hunger"], self.qq_snapshot()["energy"]), (35, 40))

    def test_unverified_replayed_or_other_character_desktops_keep_separate_bodies(self):
        replayed = {**self.desktop(), "user_id": "other-session"}
        for payload in (self.desktop(authenticated=False), replayed, self.desktop(character="reimu")):
            with self.subTest(character=payload["character_pack_id"], session=payload["user_id"]):
                snapshot = self.desktop_snapshot(payload)
                self.assertEqual((snapshot["hunger"], snapshot["energy"]), (55, 70))
        self.assertEqual((self.qq_snapshot()["hunger"], self.qq_snapshot()["energy"]), (35, 40))

    def test_disabling_binding_preserves_private_authority_and_keeps_desktop_separate(self):
        payload = self.desktop()
        self.policy.enabled = False
        desktop = self.desktop_snapshot(payload)
        private = self.qq_snapshot()
        self.assertEqual((desktop["hunger"], desktop["energy"]), (55, 70))
        self.assertEqual((private["hunger"], private["energy"]), (35, 40))

    def test_desktop_reply_settlement_updates_shared_body_only_once(self):
        payload = self.desktop()
        context = ClientProtocolContext(requested_mode=ClientMode.DESKTOP_PET, effective_mode=ClientMode.DESKTOP_PET)
        for _ in range(2):
            output = {"speech": "hello", "state_request": {"affinity": 3}}
            with self.policy.scope(payload):
                self.host._apply_care_state_request(
                    output,
                    context,
                    profile_user_id="master",
                    character_pack_id="cecilia",
                    payload=payload,
                    now_ts=1,
                    event_id="desktop-reply",
                )
            self.assertNotIn("care_state_error", output)
            self.assertEqual((output["care_state"]["energy"], output["care_state"]["affection"]), (39, 18))
        private = self.qq_snapshot()
        self.assertEqual((private["energy"], private["affection"], private["anchors"]["total_turns"]), (39, 18, 1))


if __name__ == "__main__":
    unittest.main()
