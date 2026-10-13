from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from companion_v01.care_runtime import CareModulePort
from companion_v01.qq_care_cards import build_qq_care_card, render_qq_care_card
from companion_v01.qq_gateway import NapCatQQGateway, QQMessageContext
from tests.qq_card_fixtures import install_stationery_fixture


class QQCareCardTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        install_stationery_fixture(self, self.root)
        self.care = CareModulePort.from_feature(enabled=True, storage_path=self.root / "care.json")
        self.gateway = NapCatQQGateway(state_path=self.root / "qq.json", default_character_pack_id="cecilia")
        self.context = QQMessageContext(
            True,
            "group_mention",
            is_group=True,
            user_id=10002,
            group_id=20001,
            profile_user_id="shared_group",
            session_id="group_session",
            character_pack_id="cecilia",
            sender_label="小伙伴",
            clean_message="/养成",
        )

    def _document(self, context=None):
        context = context or self.context
        result = self.gateway.handle_economy_command(context, care_module=self.care, now_ms=1800000000000)
        self.assertTrue(result["ok"])
        return build_qq_care_card(
            result["care_card"],
            user_label=context.sender_label,
            character_label="塞西莉亚",
            user_scope=f"cecilia|qq:{context.user_id}",
            character_pack_id="cecilia",
        )

    def test_real_authority_keeps_each_users_assets_and_affection_separate(self) -> None:
        runtime = self.care.runtime
        runtime.claim_daily_checkin(
            profile_user_id="shared_group",
            conversation_id="group_session",
            character_pack_id="cecilia",
            relation_user_id="qq:10002",
            date_key="2027-01-15",
            coins=40,
            now_ms=1800000000000,
        )
        runtime.donate_to_shrine(
            profile_user_id="shared_group",
            conversation_id="group_session",
            character_pack_id="cecilia",
            relation_user_id="qq:10002",
            amount=7,
            now_ms=1800000000000,
        )
        runtime.apply_affinity_delta(
            profile_user_id="shared_group",
            conversation_id="group_session",
            character_pack_id="cecilia",
            relation_user_id="qq:10002",
            client_mode="qq_text",
            delta=5,
            now_ms=1800000000000,
        )
        first = self._document()
        other = self._document(replace(self.context, user_id=10003, sender_label="另一位伙伴"))
        self.assertEqual((first.coins, first.redeem_quota, first.affection, first.checkin_streak), (33, 7, 15, 1))
        self.assertEqual((other.coins, other.redeem_quota, other.affection, other.checkin_streak), (0, 0, 10, 0))
        self.assertEqual((first.hunger, first.energy), (other.hunger, other.energy))
        self.assertTrue(first.shared_vitals)

    def test_same_person_in_another_session_has_the_same_personal_assets(self) -> None:
        self.care.runtime.claim_daily_checkin(
            profile_user_id="shared_group",
            conversation_id="group_session",
            character_pack_id="cecilia",
            relation_user_id="qq:10002",
            date_key="2027-01-15",
            coins=12,
            now_ms=1800000000000,
        )
        first = self._document()
        private = self._document(
            replace(
                self.context,
                is_group=False,
                group_id=0,
                profile_user_id="private_profile",
                session_id="private_session",
            )
        )
        self.assertEqual(first.coins, private.coins)
        self.assertEqual(first.affection, private.affection)

    def test_missing_data_is_unavailable_instead_of_rendered_as_zero(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "care_snapshot_unavailable"):
            build_qq_care_card({}, user_label="用户", character_label="角色", user_scope="actor", character_pack_id="")

    def test_real_png_handles_zero_long_name_and_cache_separation(self) -> None:
        from PIL import Image

        document = replace(self._document(), user_label="很长的名字" * 6)
        try:
            first = render_qq_care_card(document, self.root / "images")
        except RuntimeError as exc:
            if str(exc) == "chinese_font_unavailable":
                self.skipTest("No CJK font; route fallback has separate coverage.")
            raise
        with Image.open(first) as image:
            self.assertEqual(image.width, 900)
            self.assertLessEqual(image.height, 2400)
            image.verify()
        self.assertEqual(render_qq_care_card(document, self.root / "images"), first)
        different_user = render_qq_care_card(replace(document, user_scope="cecilia|qq:10003"), self.root / "images")
        changed = render_qq_care_card(replace(document, affection=20), self.root / "images")
        self.assertNotEqual(first, different_user)
        self.assertNotEqual(first, changed)
        self.assertFalse(list((self.root / "images").glob("*.tmp")))


if __name__ == "__main__":
    unittest.main()
