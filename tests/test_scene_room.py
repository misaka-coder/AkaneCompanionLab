import tempfile
import unittest
import json
import shutil
from types import SimpleNamespace
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from charpack_core.character_resources import CharacterPackResourceService
from companion_v01.care_runtime import CareModulePort, CareRuntimeStore
from companion_v01.scene.actions.service import RoomService
from companion_v01.scene.adapters.care import CarePort
from companion_v01.scene.contracts import ActionRequest, Identity
from companion_v01.scene.repositories.room import RoomRepository
from companion_v01.scene.resources.catalog import ResourceCatalog

ROOT = Path(__file__).resolve().parents[1]


class SceneRoomTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self.identity = Identity(profile_user_id="scene-test", session_id="room-test", character_pack_id="akane_v1")
        self.events = []
        pack = self.path / "characters/akane_v1"
        pack.mkdir(parents=True)
        (pack / "character.json").write_text(json.dumps({"care": {
            "enabled": True, "shop_items": [{"id": "dango", "name": "团子", "price": 7,
                "effects": {"hunger": 20, "energy": 4, "affection": 2}}],
        }}), encoding="utf-8")
        art = pack / "assets/characters/default"
        art.mkdir(parents=True)
        for emotion in ("normal", "smug", "shy"):
            shutil.copyfile(ROOT / "desktop_pet_creator_kit/characters/akane_v1/assets/characters/default" / (emotion + ".png"), art / (emotion + ".png"))
        self.make_service()

    def make_service(self):
        resources = CharacterPackResourceService(characters_dir=self.path / "characters")
        self.care = CareRuntimeStore(self.path / "care.json")
        module = CareModulePort.from_feature(enabled=True, storage_path=self.path / "care.json")
        self.service = RoomService(
            repository=RoomRepository(self.path / "room.db"),
            care=CarePort(module, SimpleNamespace(characters_dir=self.path / "characters")),
            resources=ResourceCatalog(resources, assets_root=ROOT / "web/assets"),
            record_event=lambda event: self.events.append(event) or {"ok": True},
        )

    def request(self, kind, target, request_id, revision=0, count=1):
        return ActionRequest(identity=self.identity, kind=kind, target=target, count=count,
                             request_id=request_id, expected_revision=revision)

    def test_purchase_retry_survives_restart_and_feed_only_once(self):
        before = self.service.snapshot(self.identity)
        item = next(i for i in before.shop if i.price <= before.care.coins)
        request = self.request("buy", item.id, "purchase-0001")
        bought = self.service.action(request)
        self.assertTrue(bought.ok)
        self.assertEqual(bought.snapshot.care.coins, before.care.coins - item.price)
        self.make_service()
        repeated = self.service.action(request)
        self.assertTrue(repeated.duplicate)
        self.assertEqual(repeated.snapshot.care.coins, bought.snapshot.care.coins)
        feed = self.request("feed", item.id, "feeding-00001", bought.snapshot.room.revision)
        fed = self.service.action(feed)
        self.assertTrue(fed.ok)
        self.assertNotIn(item.id, fed.snapshot.care.inventory)
        self.assertEqual(self.service.action(feed).snapshot.care.inventory, fed.snapshot.care.inventory)
        self.assertEqual(len(self.events), 2)

    def test_conflicting_windows_do_not_overwrite_equipment(self):
        outfits = self.service.snapshot(self.identity).catalog.outfits
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(self.service.action, [
                self.request("equip", outfits[0].id, "equip-first1"),
                self.request("equip", outfits[-1].id, "equip-second"),
            ]))
        self.assertEqual(sum(r.ok for r in results), 1)
        self.assertIn("revision_conflict", [r.reason for r in results])

    def test_same_request_id_cannot_purchase_another_item(self):
        item = self.service.snapshot(self.identity).shop[0]
        self.service.action(self.request("buy", item.id, "purchase-fixed"))
        result = self.service.action(self.request("buy", "another-item", "purchase-fixed"))
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "request_id_conflict")

    def test_failed_feed_does_not_change_inventory_or_revision(self):
        before = self.service.snapshot(self.identity)
        result = self.service.action(self.request("feed", before.shop[0].id, "empty-feed01"))
        self.assertFalse(result.ok)
        self.assertEqual(result.snapshot.room.revision, 0)
        self.assertEqual(result.snapshot.care.inventory, before.care.inventory)

    def test_catalog_is_stable_and_paths_are_confined(self):
        catalog = self.service.resources
        self.assertEqual(catalog.build("akane_v1"), catalog.build("akane_v1"))
        self.assertGreaterEqual(len(catalog.build("akane_v1").outfits), 3)
        for name in ("../../config.py", "../assets/../../.env", "characters/not-an-image.txt"):
            with self.assertRaises(ValueError):
                catalog.file(name)

    def test_authority_receipt_and_inventory_are_atomic_on_restart(self):
        args = dict(profile_user_id="test", character_pack_id="akane_v1", action="buy",
                    item_id="dango", request_id="authority-retry", care_config=self.service.care.config(self.identity))
        first = self.care.perform_desktop_action(**args)
        self.assertTrue(first["ok"])
        restored = CareRuntimeStore(self.path / "care.json")
        self.assertEqual(restored.perform_desktop_action(**args), first)
        conflict = restored.perform_desktop_action(**{**args, "item_id": "warm_genmaicha"})
        self.assertEqual(conflict["reason"], "request_id_conflict")

    def test_batch_feed_scales_effects_and_consumes_inventory(self):
        item = self.service.snapshot(self.identity).shop[0]
        rev = self.service.snapshot(self.identity).room.revision
        # Buy 2 items (total cost 14 <= 20 coins)
        for i in range(2):
            res = self.service.action(self.request("buy", item.id, f"buy-batch-00{i}", revision=rev))
            self.assertTrue(res.ok)
            rev = res.snapshot.room.revision
        before = self.service.snapshot(self.identity)
        self.assertEqual(before.care.inventory.get(item.id), 2)

        feed_request = self.request("feed", item.id, "feed-batch-001", revision=before.room.revision, count=2)
        result = self.service.action(feed_request)
        self.assertTrue(result.ok)
        self.assertNotIn(item.id, result.snapshot.care.inventory)
        self.assertEqual(result.event.quantity, 2)
        # item effects: hunger=20, energy=4, affection=2; x2 gives hunger delta +40, energy delta +8, affection +4
        self.assertEqual(result.event.effects, {"hunger": 40, "energy": 8, "affection": 4})
        self.assertEqual(result.snapshot.care.hunger, min(100, before.care.hunger + 40))

    def test_import_music_resource(self):
        from companion_v01.scene.resources.importer import ResourceImporter
        from companion_v01.scene.contracts import ImportRequest
        import base64

        test_assets = self.path / "test_assets"
        (test_assets / "bgm").mkdir(parents=True)
        importer_service = RoomService(
            repository=RoomRepository(self.path / "room_import.db"),
            care=self.service.care,
            resources=ResourceCatalog(self.service.resources.characters, assets_root=test_assets),
            record_event=lambda event: self.events.append(event) or {"ok": True},
        )
        importer = ResourceImporter(importer_service)

        # 1. Invalid audio throws unsupported_audio
        with self.assertRaises(ValueError) as ctx:
            importer.publish(ImportRequest(
                identity=self.identity,
                name="坏音频",
                kind="music",
                data=base64.b64encode(b"not an audio stream").decode(),
            ))
        self.assertEqual(str(ctx.exception), "unsupported_audio")

        # 2. Valid MP3 audio with ID3 tag imports cleanly
        valid_audio = b"ID3" + b"\x04\x00\x00\x00\x00\x00\x00" + b"\x00" * 100
        snap = importer.publish(ImportRequest(
            identity=self.identity,
            name="星之所向",
            kind="music",
            data=base64.b64encode(valid_audio).decode(),
        ))
        # Verify track is cataloged
        track = next((m for m in snap.catalog.music if m.name == "星之所向"), None)
        self.assertIsNotNone(track)
        self.assertTrue(track.id.startswith("bgm/custom/"))
        self.assertTrue(track.id.endswith(".mp3"))
        # Verify file is readable via catalog.file()
        p = importer_service.resources.file(track.id)
        self.assertTrue(p.is_file())
        self.assertEqual(p.read_bytes(), valid_audio)


if __name__ == "__main__":
    unittest.main()
