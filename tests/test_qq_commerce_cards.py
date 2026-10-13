from __future__ import annotations

import json
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from companion_v01.care_shop_settings import create_shop_item, read_shop, update_shop_item
from companion_v01.desktop_pet_character_resources import (
    DesktopPetCharacterResourceService,
    load_character_care_shop_items,
)
from companion_v01.qq_commerce_cards import (
    QQ_COMMERCE_PAGE_SIZE,
    build_qq_commerce_card,
    build_qq_shop_change_card,
    render_qq_commerce_card,
    render_qq_shop_change_card,
    build_qq_purchase_card,
    render_qq_purchase_card,
)
from tests import test_qq_commands as command_fixtures
from tests.qq_card_fixtures import install_stationery_fixture


class QQCommerceRouteTests(unittest.TestCase):
    _post = command_fixtures.QQCommandRouteTests._post
    _text = command_fixtures.QQCommandRouteTests._text
    _enable_care = command_fixtures.QQCommandRouteTests._enable_care

    def _event(self, *args, **kwargs):
        event = command_fixtures.QQCommandRouteTests._event(self, *args, **kwargs)
        event["time"] = int(time.time()) + self.sequence
        event["sender"].update(nickname="账号昵称", card="本群的小伙伴")
        return event

    def setUp(self):
        command_fixtures.QQCommandRouteTests.setUp(self)
        self.runtime, _renderer = self._enable_care()
        self.shop_renderer = self.enterContext(
            patch("companion_v01.qq_command_cards.render_qq_commerce_card", return_value=self.render.return_value)
        )
        self.purchase_renderer = self.enterContext(
            patch("companion_v01.qq_command_cards.render_qq_purchase_card", return_value=self.render.return_value)
        )
        chars = Path(self.temporary.name) / "characters"
        pack = chars / "cecilia"
        pack.mkdir(parents=True)
        # The desktop supports camelCase packs too; QQ must read those same edits.
        (pack / "character.json").write_text(json.dumps({"care": {"enabled": True, "shopItems": []}}), encoding="utf-8")
        self.service = object.__new__(DesktopPetCharacterResourceService)
        self.service.characters_dir = chars
        self.engine.desktop_pet_character_resources = self.service
        self.enterContext(
            patch.object(self.gateway, "_resolve_pack_identity", return_value={"assistant_name": "塞西莉亚"})
        )
        self.runtime.claim_daily_checkin(
            profile_user_id="fixture",
            character_pack_id="cecilia",
            relation_user_id="qq:10002",
            date_key="2026-10-01",
            coins=100,
        )
        self.runtime.donate_to_shrine(
            profile_user_id="fixture", character_pack_id="cecilia", relation_user_id="qq:10002", amount=100
        )

    def create(self, **overrides):
        return create_shop_item(
            self.service,
            "cecilia",
            {
                "revision": read_shop(self.service, "cecilia")["revision"],
                "name": "蜂蜜布丁",
                "price": 9,
                "effects": {"hunger": 14, "energy": 3, "affection": 6},
                "description": "新做的甜点。",
                "usable_in": ["desktop_pet", "qq"],
                **overrides,
            },
        )

    def edit(self, item_id, **overrides):
        return update_shop_item(
            self.service,
            "cecilia",
            {
                "revision": read_shop(self.service, "cecilia")["revision"],
                "item_id": item_id,
                "price": 15,
                "effects": {"hunger": 8, "energy": 4, "affection": 2},
                **overrides,
            },
        )

    def shelf(self):
        result = self._post("/商店", group=True, target=True)
        self.assertEqual(result["card_format"], "image")
        document = self.shop_renderer.call_args.args[0]
        rows = list(document.entries)
        for page in range(2, document.pages + 1):
            self._post(f"/商店 {page}", group=True, target=True)
            rows.extend(self.shop_renderer.call_args.args[0].entries)
        return rows

    def snapshot(self, user=10002, conversation_id=""):
        return self.runtime.snapshot_for_client(
            profile_user_id="fixture", conversation_id=conversation_id, character_pack_id="cecilia", client_mode="qq_text", relation_user_id=f"qq:{user}"
        )

    def test_desktop_created_item_reaches_real_qq_purchase_inventory_and_feed(self):
        created = self.create()
        self.assertTrue(created["ok"])
        rows = self.shelf()
        document = self.shop_renderer.call_args.args[0]
        self.assertEqual(document.user_label, "本群的小伙伴")
        item = next(row for row in rows if row.item_id == created["item_id"])
        self.assertEqual((item.name, item.description, item.value), ("蜂蜜布丁", "新做的甜点。", 9))
        self.assertEqual(document.redeem_quota, 100)
        result = self._post("/购买 蜂蜜布丁 ×2", group=True, target=True)
        self.assertTrue(result["command_ok"])
        self.assertEqual(result["card_format"], "image")
        receipt = self.purchase_renderer.call_args.args[0]
        self.assertEqual((receipt.quantity, receipt.cost, receipt.quota_after, receipt.inventory_count), (2, 18, 82, 2))
        self.assertEqual(receipt.user_label, "本群的小伙伴")
        self.assertEqual(receipt.item["name"], "蜂蜜布丁")
        self.assertEqual(self.snapshot()["redeem_quota"], 82)
        self._post("/背包", group=True, target=True)
        backpack = self.shop_renderer.call_args.args[0]
        self.assertEqual(
            (backpack.kind, backpack.entries[0].name, backpack.entries[0].value), ("backpack", "蜂蜜布丁", 2)
        )
        self.assertFalse(any(part["type"] == "reply" for msg in self.sent for part in msg["message"]))
        context = self.gateway.build_message_context(self._event("/投喂 蜂蜜布丁", group=True, target=True))
        before = self.snapshot(conversation_id=context.session_id)
        feed = self.gateway.handle_economy_command(
            context,
            care_module=self.engine.get_care_module(),
            shop_items=load_character_care_shop_items(self.service, "cecilia"),
        )
        self.assertTrue(feed["_llm_passthrough"])
        self.assertIn("商品介绍：新做的甜点。", feed["qq_action_note"])
        self.assertIn("实际效果：", feed["qq_action_note"])
        self.assertNotIn("商品介绍", feed["turn_message"])
        after = self.snapshot(conversation_id=context.session_id)
        self.assertEqual(after["inventory"][created["item_id"]]["count"], 1)
        for key, delta in (("hunger", 14), ("energy", 3), ("affection", 6)):
            self.assertEqual(after[key], min(100, before[key] + delta))

    def test_edits_and_qq_shelf_toggle_apply_without_restart_and_keep_inventory(self):
        created = self.create()
        self._post("/购买 蜂蜜布丁")
        self.edit(created["item_id"], name="新名字布丁", description="改过的说明。")
        updated = next(row for row in self.shelf() if row.item_id == created["item_id"])
        self.assertEqual((updated.name, updated.value, updated.description), ("新名字布丁", 15, "改过的说明。"))
        self._post("/背包")
        self.assertEqual(self.shop_renderer.call_args.args[0].entries[0].name, "新名字布丁")
        self.assertTrue(self._post("/购买 新名字布丁")["command_ok"])
        self.edit(created["item_id"], usable_in=["desktop_pet"])
        self.assertFalse(any(row.item_id == created["item_id"] for row in self.shelf()))
        blocked = self._post("/购买 新名字布丁")
        self.assertEqual(blocked["command_status"], "not_usable_in_qq")
        self._post("/背包")
        self.assertEqual(self.shop_renderer.call_args.args[0].entries[0].value, 2)

        context = self.gateway.build_message_context(self._event("/投喂 新名字布丁"))
        feed = self.gateway.handle_economy_command(
            context,
            care_module=self.engine.get_care_module(),
            shop_items=load_character_care_shop_items(self.service, "cecilia"),
        )
        self.assertTrue(feed["ok"])
        self.assertIn("商品介绍：改过的说明。", feed["qq_action_note"])
        self.assertNotIn("新做的甜点", feed["qq_action_note"])

    def test_camelcase_channel_flags_and_deleted_item_inventory_fallback(self):
        created = self.create(usable_in=["desktop_pet"])
        path = self.service.characters_dir / "cecilia/character.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        item = data["care"]["shopItems"][0]
        item["usableIn"] = item.pop("usable_in")
        path.write_text(json.dumps(data), encoding="utf-8")
        self.assertEqual(self._post("/购买 蜂蜜布丁")["command_status"], "not_usable_in_qq")
        self.edit(created["item_id"], usable_in=["desktop_pet", "qq"])
        self.assertTrue(self._post("/购买 蜂蜜布丁")["command_ok"])
        data = json.loads(path.read_text(encoding="utf-8"))
        data["care"]["shopItems"] = []
        path.write_text(json.dumps(data), encoding="utf-8")
        self._post("/背包")
        self.assertEqual(self.shop_renderer.call_args.args[0].entries[0].name, "蜂蜜布丁")
        context = self.gateway.build_message_context(self._event(f"/投喂 {created['item_id']}"))
        feed = self.gateway.handle_economy_command(
            context,
            care_module=self.engine.get_care_module(),
            shop_items=load_character_care_shop_items(self.service, "cecilia"),
        )
        self.assertTrue(feed["ok"])
        self.assertIn("商品介绍：新做的甜点。", feed["qq_action_note"])
        self.assertNotIn(created["item_id"], self.snapshot()["inventory"])

    def test_feed_without_description_omits_empty_intro_and_failed_feed_stays_local(self):
        self.create(description="")
        context = self.gateway.build_message_context(self._event("/投喂 蜂蜜布丁"))
        result = self.gateway.handle_economy_command(
            context,
            care_module=self.engine.get_care_module(),
            shop_items=load_character_care_shop_items(self.service, "cecilia"),
        )
        self.assertFalse(result["ok"])
        self.assertNotIn("_llm_passthrough", result)
        self.assertNotIn("qq_action_note", result)
        self.assertTrue(self._post("/购买 蜂蜜布丁")["command_ok"])
        feed = self.gateway.handle_economy_command(
            context,
            care_module=self.engine.get_care_module(),
            shop_items=load_character_care_shop_items(self.service, "cecilia"),
        )
        self.assertTrue(feed["_llm_passthrough"])
        self.assertNotIn("商品介绍", feed["qq_action_note"])
        self.assertIn("实际效果：", feed["qq_action_note"])

    def test_text_pagination_group_targeting_and_invalid_pages_stay_local(self):
        self.create()
        self.assertEqual(self._post("/商店", group=True)["reason"], "group_command_requires_explicit_mention")
        self.shop_renderer.assert_not_called()
        result = self._post("/商店 2 文字", group=True, target=True)
        self.assertEqual(result["card_format"], "text")
        self.assertIn("商店  2/", self._text())
        self.shop_renderer.assert_not_called()
        for command in ("/商店 99", "/背包 2", "/商店 0", "/商店 文字 文字"):
            with self.subTest(command=command):
                result = self._post(command)
                self.assertFalse(result["command_ok"])
        self.assertEqual(self.broker.calls, [])

    def test_backpack_is_private_to_user_and_duplicate_purchase_charges_once(self):
        created = self.create()
        event = self._event("/购买 蜂蜜布丁", group=True, target=True)
        self.assertTrue(self.client.post("/api/qq/napcat/event", json=event).json()["command_ok"])
        duplicate = self.client.post("/api/qq/napcat/event", json=event).json()
        self.assertEqual(duplicate["reason"], "duplicate_event")
        self.purchase_renderer.assert_called_once()
        self.assertEqual(self.snapshot()["redeem_quota"], 91)
        self.assertEqual(self.snapshot()["inventory"][created["item_id"]]["count"], 1)
        self._post("/背包", group=True, target=True, user_id=10003)
        other = self.shop_renderer.call_args.args[0]
        self.assertEqual(other.entries, ())
        self.assertIn("qq:10003", other.user_scope)
        self.assertEqual(other.redeem_quota, 0)

    def test_purchase_render_failure_preserves_purchase_and_falls_back(self):
        created = self.create()
        self.purchase_renderer.side_effect = RuntimeError("chinese_font_unavailable")
        result = self._post("/购买 蜂蜜布丁 ×2")
        self.assertTrue(result["command_ok"])
        self.assertEqual(result["card_format"], "text_fallback")
        self.assertEqual(result["image_failure_reason"], "chinese_font_unavailable")
        self.assertIn("购买成功", self._text())
        self.assertIn("剩余 82 额度", self._text())
        self.assertEqual(self.snapshot()["inventory"][created["item_id"]]["count"], 2)
        self.assertEqual(self.broker.calls, [])

    def test_purchase_private_nickname_and_insufficient_quota_stays_text(self):
        self.create(price=60)
        self._post("/购买 蜂蜜布丁")
        self.assertEqual(self.purchase_renderer.call_args.args[0].user_label, "账号昵称")
        result = self._post("/购买 蜂蜜布丁")
        self.assertFalse(result["command_ok"])
        self.assertEqual(result["card_format"], "text")
        self.purchase_renderer.assert_called_once()
        self.assertEqual(self.snapshot()["redeem_quota"], 40)

    def test_purchase_image_delivery_failure_does_not_buy_again(self):
        created = self.create()
        with patch.object(self.gateway, "send_image", return_value={"ok": False, "reason": "image_send_failed"}):
            result = self._post("/购买 蜂蜜布丁")
        self.assertEqual(result["card_format"], "text_fallback")
        self.assertIn("购买成功", self._text())
        self.assertEqual(self.snapshot()["redeem_quota"], 91)
        self.assertEqual(self.snapshot()["inventory"][created["item_id"]]["count"], 1)

    def test_image_failure_falls_back_to_current_page_text(self):
        self.create()
        self.shop_renderer.side_effect = RuntimeError("commerce_background_unavailable")
        result = self._post("/商店 2")
        self.assertEqual(result["card_format"], "text_fallback")
        self.assertEqual(result["image_failure_reason"], "commerce_background_unavailable")
        self.assertIn("商店  2/", self._text())
        self.assertNotIn("三色团子", self._text())

    def test_duplicate_product_names_show_ids_and_exact_id_wins_over_another_name(self):
        first = self.create(name="草莓蛋糕")
        second = self.create(name="草莓蛋糕", price=12)
        cakes = [row for row in self.shelf() if row.name == "草莓蛋糕"]
        self.assertEqual(len(cakes), 2)
        self.assertTrue(all(row.same_name for row in cakes))
        self._post(f"/购买 {second['item_id']}")
        self.assertEqual(self.purchase_renderer.call_args.args[0].effects_label, f"/投喂 {second['item_id']}")
        self.edit(first["item_id"], name=second["item_id"])
        result = self._post(f"/购买 {second['item_id']}")
        self.assertTrue(result["command_ok"])
        snapshot = self.snapshot()
        self.assertEqual(snapshot["redeem_quota"], 76)
        self.assertEqual(snapshot["inventory"][second["item_id"]]["count"], 2)
        self.assertNotIn(first["item_id"], snapshot["inventory"])


class QQCommerceCardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        install_stationery_fixture(self, self.root)

    def test_purchase_receipt_renders_long_names_and_caches_per_buyer_and_balance(self):
        from PIL import Image

        doc = build_qq_purchase_card(
            {"item": {"id": "cake", "name": "草莓蛋糕"}, "quantity": 2,
             "cost": 18, "quota_after": 82, "inventory_count": 3},
            user_label="小伙伴", character_label="塞西莉亚", user_scope="cecilia|qq:10002",
            character_pack_id="cecilia",
        )
        self.assertIn("草莓蛋糕 ×2", doc.as_text())
        self.assertIn("消耗 18 额度 · 剩余 82 额度", doc.as_text())
        path = render_qq_purchase_card(doc, self.root)
        self.assertEqual(render_qq_purchase_card(doc, self.root), path)
        for changed in (replace(doc, quota_after=64), replace(doc, user_scope="cecilia|qq:10003"),
                        replace(doc, theme="neutral", character_label="灵梦")):
            self.assertNotEqual(render_qq_purchase_card(changed, self.root), path)
        long = replace(doc, item={"id": "cake", "name": "长名称甜点" * 32}, user_label="长昵称" * 10)
        for variant in (doc, long):
            with Image.open(render_qq_purchase_card(variant, self.root)) as image:
                self.assertEqual(image.width, 900)
                self.assertLessEqual(image.height, 2400)
                image.verify()

    def document(self, **overrides):
        payload = {
            "kind": "shop",
            "coins": 40,
            "redeem_quota": 99,
            "page": 1,
            "items": [
                {
                    "id": f"custom_{index:012x}",
                    "name": f"甜点{index}",
                    "price": 7,
                    "description": "温柔的味道。",
                    "effect_summary": "饥饿+12  精力+2",
                    "category": "custom",
                }
                for index in range(12)
            ],
            **overrides,
        }
        return build_qq_commerce_card(
            payload,
            user_label="小伙伴",
            character_label="塞西莉亚",
            user_scope="cecilia|qq:10002",
            character_pack_id="cecilia",
        )

    def test_compact_confirmation_covers_current_values_delete_and_desktop_only(self):
        from PIL import Image

        item = {"id": "custom_example", "name": "长名字草莓蛋糕" * 5,
                "description": "用新鲜草莓与轻盈奶油做成，适合分享。" * 6,
                "price": 999999, "effects": {"hunger": 100, "energy": -10, "affection": 5},
                "usable_in": ["desktop_pet"]}
        paths = []
        for action, pack_id, name in (("create", "cecilia", "塞西莉亚"), ("update", "reimu", "灵梦"), ("delete", "cecilia", "塞西莉亚")):
            doc = build_qq_shop_change_card(action, item, character_label=name, user_scope="test", character_pack_id=pack_id)
            self.assertIn(f"{name}的商店", doc.as_text())
            self.assertIn("桌宠 999999 金币", doc.as_text())
            self.assertNotIn("QQ", doc.as_text())
            if action == "delete":
                self.assertIn("已下架", doc.as_text())
            path = render_qq_shop_change_card(doc, self.root)
            paths.append(path)
            self.assertEqual(render_qq_shop_change_card(doc, self.root), path)
            with Image.open(path) as image:
                self.assertEqual(image.width, 900)
                self.assertLessEqual(image.height, 900)
                image.verify()
        self.assertEqual(len(set(paths)), 3)

    def test_pagination_and_validation_use_real_values(self):
        doc = self.document(page=2)
        self.assertEqual((doc.pages, len(doc.entries), doc.entries[0].name), (3, QQ_COMMERCE_PAGE_SIZE, "甜点5"))
        self.assertIn("饱腹+12", doc.as_text())
        self.assertNotIn("甜点0", doc.as_text())
        for payload in ({"page": 4}, {"coins": None}, {"items": [{"name": "坏数据"}]}):
            with self.subTest(payload=payload), self.assertRaises(RuntimeError):
                self.document(**payload)

    def test_rendering_long_products_empty_inventory_and_cache_invalidation(self):
        from PIL import Image

        doc = self.document()
        try:
            first = render_qq_commerce_card(doc, self.root)
        except RuntimeError as exc:
            if str(exc) == "chinese_font_unavailable":
                self.skipTest("No CJK font; route fallback is covered separately.")
            raise
        self.assertEqual(render_qq_commerce_card(doc, self.root), first)
        for variant in (
            replace(doc, redeem_quota=88),
            replace(doc, user_scope="cecilia|qq:10003"),
            replace(doc, entries=(replace(doc.entries[0], name="改名甜点", value=18), *doc.entries[1:])),
            self.document(page=2),
        ):
            self.assertNotEqual(render_qq_commerce_card(variant, self.root), first)
        long = replace(
            doc,
            user_label="很长的名字" * 6,
            entries=tuple(
                replace(
                    item,
                    name="长" * 40,
                    description="说明" * 60,
                    effects="饱腹+100 精力+100 好感+100 饱腹⇄精力",
                    value=999999,
                    same_name=True,
                )
                for item in doc.entries
            ),
        )
        for variant in (long, self.document(kind="backpack", items=[])):
            path = render_qq_commerce_card(variant, self.root)
            with Image.open(path) as image:
                self.assertEqual(image.width, 900)
                self.assertLessEqual(image.height, 2400)
                image.verify()
        self.assertFalse(list(self.root.glob("*.tmp")))


if __name__ == "__main__":
    unittest.main()
