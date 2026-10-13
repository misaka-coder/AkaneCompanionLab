from __future__ import annotations

import base64
import io
import json
import unittest
from pathlib import Path
from PIL import Image

from tests import test_care_shop_settings as fixtures
from companion_v01.care_shop_settings import read_shop, update_shop_item, create_shop_item, delete_shop_item
from companion_v01.shop_artwork import artwork_file, artwork_url, resolve_artwork
from companion_v01.desktop_pet_character_resources import load_character_care_shop_items
from companion_v01.qq_gateway import _commerce_listing
from companion_v01.qq_commerce_cards import build_qq_commerce_card, render_qq_commerce_card


def upload(color="pink"):
    out = io.BytesIO()
    Image.new("RGBA", (80, 64), color).save(out, format="PNG")
    return "data:image/png;base64," + base64.b64encode(out.getvalue()).decode()


class ShopArtworkTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.CareShopSettingsTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.service = self.fixture.service

    def update(self, **kwargs):
        return update_shop_item(self.service, "reimu", self.fixture.change(**kwargs))

    def test_upload_shared_with_qq_and_survives_ordinary_edits(self):
        result = self.update(image_upload=upload(), art_kind="dessert")
        item = result["items"][0]
        path = resolve_artwork(self.service, "reimu", item["image"])
        self.assertTrue(path.is_file())
        self.assertTrue(artwork_url(self.service, "reimu", item).startswith("/desktop-pet-character-packs/reimu/assets/shop/"))
        with Image.open(path) as image:
            self.assertEqual(image.size, (80, 64))
        self.assertNotIn("base64", self.fixture.path.read_text(encoding="utf-8"))
        self.assertEqual(self.update(name="改名甜点")["items"][0]["image"], item["image"])
        rows = load_character_care_shop_items(self.service, "reimu")
        payload = _commerce_listing("shop", rows, {"coins": 30, "redeem_quota": 20}, {})["commerce_card"]
        doc = build_qq_commerce_card(payload, user_label="测试", character_label="灵梦", user_scope="test", character_pack_id="reimu", resources=self.service)
        self.assertEqual(Path(doc.entries[0].image_path), path)
        self.assertTrue(render_qq_commerce_card(doc, self.fixture.root / "cards").is_file())

    def test_change_and_remove_do_not_delete_old_inventory_artwork(self):
        first = self.update(image_upload=upload())["items"][0]
        second = self.update(image_upload=upload("green"))["items"][0]
        self.assertNotEqual(first["image"], second["image"])
        self.assertTrue(resolve_artwork(self.service, "reimu", first["image"]).is_file())
        removed = self.update(image_upload="", art_kind="drink")["items"][0]
        self.assertEqual(removed["image"], "")
        self.assertEqual(artwork_url(self.service, "reimu", removed), "/assets/shop/drink.svg")
        self.assertEqual(artwork_file(self.service, "reimu", removed).name, "drink.png")

    def test_bad_upload_and_conflict_do_not_replace_metadata(self):
        before = self.fixture.path.read_bytes()
        for bad in ("https://example.com/test.png", "data:image/svg+xml;base64,PHN2Zz4=", upload()[:-5], 42):
            with self.subTest(bad=str(bad)[:35]), self.assertRaises(ValueError):
                self.update(image_upload=bad)
        with self.assertRaises(ValueError):
            self.update(art_kind="unknown")
        self.assertEqual(self.fixture.path.read_bytes(), before)
        result = self.update(revision="stale", image_upload=upload())
        self.assertEqual(result["reason"], "shop_revision_conflict")
        self.assertFalse((self.fixture.root / "reimu/assets/shop").exists())

    def test_paths_cannot_escape_pack_and_missing_image_falls_back(self):
        for path in ("assets/../../secret.png", "C:/secret.png", "/tmp/a.png", "assets\\secret.png", "https://example.com/a.png"):
            self.assertIsNone(resolve_artwork(self.service, "reimu", path))
        self.assertEqual(artwork_file(self.service, "reimu", {"name": "热牛奶", "image": "assets/missing.png"}).name, "drink.png")

    def test_create_upload_delete_and_backpack_snapshot(self):
        result = create_shop_item(self.service, "reimu", self.fixture.creation(image_upload=upload()))
        item = next(i for i in result["items"] if i["id"] == result["item_id"])
        from companion_v01.care_runtime import CareRuntimeStore
        runtime = CareRuntimeStore(self.fixture.root / "care.json")
        args = dict(profile_user_id="owner", character_pack_id="reimu", relation_user_id="qq:1")
        runtime.claim_daily_checkin(**args, date_key="2026-10-02", coins=100)
        runtime.donate_to_shrine(**args, amount=100)
        bought = runtime.buy_to_inventory(**args, item_id=item["id"], item_name=item["name"], price=item["price"], item_effects=item["effects"], item_metadata=item)
        self.assertEqual(bought["status"], "ok")
        delete_shop_item(self.service, "reimu", {"revision": result["revision"], "item_id": item["id"]})
        # The stored image remains readable after delisting, without fetching a URL.
        self.assertTrue(resolve_artwork(self.service, "reimu", item["image"]).is_file())
        persisted = json.loads((self.fixture.root / "care.json").read_text(encoding="utf-8"))
        self.assertIn(item["image"], json.dumps(persisted, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
