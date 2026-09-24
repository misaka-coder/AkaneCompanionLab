from __future__ import annotations
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
from fastapi import FastAPI
from fastapi.testclient import TestClient
from companion_v01.care_shop_settings import (
    create_shop_item,
    delete_shop_item,
    read_shop,
    update_shop_item,
)
from companion_v01.care_runtime import MAX_DESKTOP_INVENTORY_ITEMS, CareRuntimeStore
from companion_v01.desktop_pet_character_resources import DesktopPetCharacterResourceService, load_character_care_config
from companion_v01.deployment_security import AdminWriteAuth
from companion_v01.routes.desktop_pet import build_desktop_pet_router

class CareShopSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        pack = self.root / "reimu"
        pack.mkdir()
        self.path = pack / "character.json"
        self.original = {"identity": {"name": "fixture"}, "care": {"enabled": True,
            "initial_coins": 100, "initial_hunger": 20, "initial_energy": 30, "initial_affection": 10,
            "shop_items": [{"id": "tea", "name": "茶", "price": 7, "description": "keep",
                "effects": {"hunger": 2, "energy": 4, "affection": 1}}]}}
        self.path.write_text(json.dumps(self.original), encoding="utf-8")
        self.service = object.__new__(DesktopPetCharacterResourceService)
        self.service.characters_dir = self.root

    def change(self, **overrides):
        return {"revision": read_shop(self.service, "reimu")["revision"], "item_id": "tea",
                "price": 11, "effects": {"hunger": 12, "energy": 18, "affection": -3}, **overrides}

    def test_persisted_values_used_by_real_buy_and_feed(self):
        result = update_shop_item(self.service, "reimu", self.change())
        self.assertTrue(result["ok"])
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(data["identity"], self.original["identity"])
        self.assertEqual(data["care"]["shop_items"][0]["description"], "keep")
        config = load_character_care_config(self.service, "reimu")
        runtime = CareRuntimeStore(self.root / "state.json")
        args = dict(profile_user_id="owner", character_pack_id="reimu", care_config=config, now_ms=1000000)
        runtime.snapshot_for_desktop(**args)
        bought = runtime.perform_desktop_action(**args, action="buy", item_id="tea")
        self.assertTrue(bought["ok"])
        self.assertEqual(bought["snapshot"]["coins"], 89)
        fed = runtime.perform_desktop_action(**args, action="feed", item_id="tea")
        self.assertTrue(fed["ok"])
        self.assertEqual(fed["effects_applied"], {"hunger": 12, "energy": 18, "affection": -3})
        self.assertEqual(read_shop(self.service, "reimu")["items"][0]["price"], 11)

    def test_concurrent_edit_conflict_preserves_newer_values(self):
        payload = self.change()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda price: update_shop_item(self.service, "reimu", {**payload, "price": price}), [12, 13]))
        self.assertEqual(sum(bool(r["ok"]) for r in results), 1)
        self.assertEqual(next(r for r in results if not r["ok"])["reason"], "shop_revision_conflict")
        self.assertFalse(list(self.root.rglob(".care-shop-*.tmp")))

    def test_invalid_values_and_paths_do_not_write(self):
        original = self.path.read_bytes()
        for changes in ({"price": -1}, {"price": 1.5}, {"price": True}, {"effects": {"hunger": 101, "energy": 0, "affection": 0}}, {"item_id": "missing"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                update_shop_item(self.service, "reimu", self.change(**changes))
        with self.assertRaises(ValueError): read_shop(self.service, "../reimu")
        self.assertEqual(self.path.read_bytes(), original)

    def test_route_requires_admin_and_returns_conflict(self):
        app = FastAPI()
        engine = SimpleNamespace(desktop_pet_character_resources=self.service,
            get_care_module=lambda: SimpleNamespace(enabled=True))
        app.include_router(build_desktop_pet_router(engine=engine, config_module=SimpleNamespace(),
            runtime_metrics=SimpleNamespace(observe_request=lambda *a, **k: None), log_event=lambda *a, **k: None,
            resolve_identity_from_query=lambda request: ("s", "p"), resolve_identity_from_payload=lambda p: ("s", "p"),
            admin_auth=AdminWriteAuth(token="test-admin", require_token=True, allow_loopback_without_token=False)))
        with TestClient(app) as client:
            endpoint = "/desktop-pet/care/shop"
            self.assertEqual(client.post(endpoint, json={"character_pack_id": "reimu"}).status_code, 401)
            headers = {"Authorization": "Bearer test-admin"}
            read = client.post(endpoint, headers=headers, json={"character_pack_id": "reimu"}).json()
            payload = {**self.change(revision=read["revision"]), "action": "update", "character_pack_id": "reimu"}
            self.assertEqual(client.post(endpoint, headers=headers, json=payload).status_code, 200)
            self.assertEqual(client.post(endpoint, headers=headers, json=payload).status_code, 409)

    def creation(self, **overrides):
        return {"revision": read_shop(self.service, "reimu")["revision"], "name": "抹茶大福",
                "price": 9, "effects": {"hunger": 14, "energy": 3, "affection": 6},
                "description": "新做的和果子。", "usable_in": ["desktop_pet", "qq"], **overrides}

    def test_created_item_is_listed_and_used_by_real_buy_and_feed(self):
        result = create_shop_item(self.service, "reimu", self.creation())
        self.assertTrue(result["ok"])
        item_id = result["item_id"]
        self.assertTrue(item_id.startswith("custom_"))
        stored = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(stored["identity"], self.original["identity"])
        self.assertEqual(stored["care"]["shop_items"][0]["description"], "keep")
        created = stored["care"]["shop_items"][-1]
        self.assertEqual(created["name"], "抹茶大福")
        self.assertEqual(created["usable_in"], ["desktop_pet", "qq"])
        self.assertTrue(created["custom"])
        view = next(item for item in result["items"] if item["id"] == item_id)
        self.assertEqual(view["price"], 9)
        self.assertTrue(view["custom"])
        config = load_character_care_config(self.service, "reimu")
        runtime = CareRuntimeStore(self.root / "state.json")
        args = dict(profile_user_id="owner", character_pack_id="reimu", care_config=config, now_ms=1000000)
        runtime.snapshot_for_desktop(**args)
        bought = runtime.perform_desktop_action(**args, action="buy", item_id=item_id)
        self.assertTrue(bought["ok"], bought)
        self.assertEqual(bought["snapshot"]["coins"], 91)
        fed = runtime.perform_desktop_action(**args, action="feed", item_id=item_id)
        self.assertTrue(fed["ok"], fed)
        self.assertEqual(fed["effects_applied"], {"hunger": 14, "energy": 3, "affection": 6})

    def test_created_item_can_stay_desktop_only_and_rejects_bad_input(self):
        desktop_only = create_shop_item(self.service, "reimu",
            self.creation(usable_in=["desktop_pet"], description=""))
        self.assertTrue(desktop_only["ok"])
        created = json.loads(self.path.read_text(encoding="utf-8"))["care"]["shop_items"][-1]
        self.assertEqual(created["usable_in"], ["desktop_pet"])
        self.assertNotIn("description", created)
        original = self.path.read_bytes()
        for changes in ({"name": ""}, {"name": "   "}, {"name": "字" * 41}, {"name": "两\n行"},
                        {"name": 5}, {"price": -1}, {"price": True}, {"price": 10 ** 7},
                        {"effects": {"hunger": 101, "energy": 0, "affection": 0}},
                        {"effects": {"hunger": 1, "energy": 0}},
                        {"effects": {"hunger": 1, "energy": 0, "affection": False}},
                        {"usable_in": ["qq"]}, {"usable_in": ["desktop_pet", "wechat"]},
                        {"usable_in": "desktop_pet"}, {"description": "字" * 121}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                create_shop_item(self.service, "reimu", self.creation(**changes))
        with self.assertRaises(ValueError):
            create_shop_item(self.service, "../reimu", self.creation())
        self.assertEqual(self.path.read_bytes(), original)
        self.assertFalse(list(self.root.rglob(".care-shop-*.tmp")))

    def test_creation_stops_at_item_limit(self):
        data = json.loads(self.path.read_text(encoding="utf-8"))
        data["care"]["shop_items"] = [{"id": f"item_{index}", "name": f"商品{index}", "price": 1,
            "effects": {"hunger": 1, "energy": 1, "affection": 1}}
            for index in range(MAX_DESKTOP_INVENTORY_ITEMS)]
        self.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(ValueError) as caught:
            create_shop_item(self.service, "reimu", self.creation())
        self.assertEqual(str(caught.exception), "shop_item_limit_reached")

    def test_rename_and_channel_toggle_keep_special_effect_keys(self):
        data = json.loads(self.path.read_text(encoding="utf-8"))
        data["care"]["shop_items"].append({"id": "trick", "name": "旧名字", "price": 3,
            "usable_in": ["desktop_pet", "qq"],
            "effects": {"hunger": 1, "energy": 0, "affection": 0, "random_vitals": True}})
        self.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        def edit(**overrides):
            return {"revision": read_shop(self.service, "reimu")["revision"], "item_id": "trick",
                    "price": 4, "effects": {"hunger": 2, "energy": 1, "affection": 0}, **overrides}

        result = update_shop_item(self.service, "reimu", edit(name="新名字", description="试试运气。",
            usable_in=["desktop_pet"]))
        self.assertTrue(result["ok"])
        stored = {item["id"]: item for item in json.loads(self.path.read_text(encoding="utf-8"))["care"]["shop_items"]}
        self.assertEqual(stored["trick"]["name"], "新名字")
        self.assertEqual(stored["trick"]["description"], "试试运气。")
        self.assertEqual(stored["trick"]["usable_in"], ["desktop_pet"])
        self.assertTrue(stored["trick"]["effects"]["random_vitals"])
        self.assertEqual(stored["trick"]["effects"]["hunger"], 2)
        self.assertEqual(stored["trick"]["effects"]["energy"], 1)
        self.assertEqual(next(item for item in result["items"] if item["id"] == "trick")["description"], "试试运气。")

        update_shop_item(self.service, "reimu", edit(description="   "))
        stored = {item["id"]: item for item in json.loads(self.path.read_text(encoding="utf-8"))["care"]["shop_items"]}
        self.assertNotIn("description", stored["trick"])
        with self.assertRaises(ValueError):
            update_shop_item(self.service, "reimu", edit(name=""))

    def test_route_supports_create_and_reports_input_reason(self):
        token = "shop-admin-token"
        app = FastAPI()
        engine = SimpleNamespace(desktop_pet_character_resources=self.service,
            get_care_module=lambda: SimpleNamespace(enabled=True))
        app.include_router(build_desktop_pet_router(engine=engine, config_module=SimpleNamespace(),
            runtime_metrics=SimpleNamespace(observe_request=lambda *a, **k: None), log_event=lambda *a, **k: None,
            resolve_identity_from_query=lambda request: ("s", "p"), resolve_identity_from_payload=lambda p: ("s", "p"),
            admin_auth=AdminWriteAuth(token=token, require_token=True, allow_loopback_without_token=False)))
        with TestClient(app) as client:
            endpoint = "/desktop-pet/care/shop"
            headers = {"Authorization": f"Bearer {token}"}
            read = client.post(endpoint, headers=headers, json={"character_pack_id": "reimu"}).json()
            payload = {**self.creation(revision=read["revision"]), "action": "create",
                       "character_pack_id": "reimu"}
            created = client.post(endpoint, headers=headers, json=payload)
            self.assertEqual(created.status_code, 200)
            self.assertTrue(created.json()["item_id"].startswith("custom_"))
            self.assertEqual(client.post(endpoint, headers=headers, json=payload).status_code, 409)
            rejected = client.post(endpoint, headers=headers, json={
                **payload, "revision": read_shop(self.service, "reimu")["revision"], "name": ""})
            self.assertEqual(rejected.status_code, 400)
            self.assertEqual(rejected.json()["reason"], "invalid_shop_name")

    def removal(self, **overrides):
        return {"revision": read_shop(self.service, "reimu")["revision"], "item_id": "tea", **overrides}

    def test_delete_removes_only_that_item_and_keeps_other_fields(self):
        create_shop_item(self.service, "reimu", self.creation())
        result = delete_shop_item(self.service, "reimu", self.removal())
        self.assertTrue(result["ok"])
        self.assertNotIn("tea", {item["id"] for item in result["items"]})
        stored = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(stored["identity"], self.original["identity"])
        self.assertEqual([item["id"] for item in stored["care"]["shop_items"]],
                         [item["id"] for item in result["items"]])
        self.assertTrue(any(item["custom"] for item in stored["care"]["shop_items"]))
        self.assertFalse(list(self.root.rglob(".care-shop-*.tmp")))

    def test_delete_rejects_unknown_item_stale_revision_and_bad_pack(self):
        original = self.path.read_bytes()
        with self.assertRaises(ValueError) as caught:
            delete_shop_item(self.service, "reimu", self.removal(item_id="missing"))
        self.assertEqual(str(caught.exception), "shop_item_not_found")
        with self.assertRaises(ValueError) as empty:
            delete_shop_item(self.service, "reimu", self.removal(item_id=""))
        self.assertEqual(str(empty.exception), "shop_item_not_found")
        with self.assertRaises(ValueError):
            delete_shop_item(self.service, "../reimu", self.removal())
        self.assertEqual(self.path.read_bytes(), original)

        stale = self.removal()
        self.assertTrue(update_shop_item(self.service, "reimu",
            {"revision": stale["revision"], "item_id": "tea", "price": 5,
             "effects": {"hunger": 1, "energy": 1, "affection": 1}})["ok"])
        conflicted = delete_shop_item(self.service, "reimu", stale)
        self.assertFalse(conflicted["ok"])
        self.assertEqual(conflicted["reason"], "shop_revision_conflict")
        self.assertEqual(read_shop(self.service, "reimu")["items"][0]["price"], 5)

    def test_deleted_item_drops_out_of_care_config_and_inventory(self):
        create_shop_item(self.service, "reimu", self.creation())
        created_id = read_shop(self.service, "reimu")["items"][-1]["id"]
        self.assertTrue(delete_shop_item(self.service, "reimu", self.removal(item_id=created_id))["ok"])
        config = load_character_care_config(self.service, "reimu")
        self.assertNotIn(created_id, {item["id"] for item in config["shop_items"]})
        runtime = CareRuntimeStore(self.root / "state.json")
        args = dict(profile_user_id="owner", character_pack_id="reimu", care_config=config, now_ms=1000000)
        runtime.snapshot_for_desktop(**args)
        missing = runtime.perform_desktop_action(**args, action="buy", item_id=created_id)
        self.assertFalse(missing["ok"])

    def test_route_supports_delete(self):
        token = "shop-admin-token"
        app = FastAPI()
        engine = SimpleNamespace(desktop_pet_character_resources=self.service,
            get_care_module=lambda: SimpleNamespace(enabled=True))
        app.include_router(build_desktop_pet_router(engine=engine, config_module=SimpleNamespace(),
            runtime_metrics=SimpleNamespace(observe_request=lambda *a, **k: None), log_event=lambda *a, **k: None,
            resolve_identity_from_query=lambda request: ("s", "p"), resolve_identity_from_payload=lambda p: ("s", "p"),
            admin_auth=AdminWriteAuth(token=token, require_token=True, allow_loopback_without_token=False)))
        with TestClient(app) as client:
            endpoint = "/desktop-pet/care/shop"
            headers = {"Authorization": f"Bearer {token}"}
            self.assertEqual(client.post(endpoint, json={"action": "delete", "character_pack_id": "reimu"}).status_code, 401)
            read = client.post(endpoint, headers=headers, json={"character_pack_id": "reimu"}).json()
            deleted = client.post(endpoint, headers=headers, json={
                "action": "delete", "character_pack_id": "reimu",
                "revision": read["revision"], "item_id": "tea"})
            self.assertEqual(deleted.status_code, 200)
            self.assertNotIn("tea", {item["id"] for item in deleted.json()["items"]})
            again = client.post(endpoint, headers=headers, json={
                "action": "delete", "character_pack_id": "reimu",
                "revision": deleted.json()["revision"], "item_id": "tea"})
            self.assertEqual(again.status_code, 400)
            self.assertEqual(again.json()["reason"], "shop_item_not_found")
