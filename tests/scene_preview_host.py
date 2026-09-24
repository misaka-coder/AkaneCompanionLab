"""Isolated UI acceptance host. Real room/Care services, no model impersonation.

python -m tests.scene_preview_host --port 14320
Data lives in work/scene-preview, never the user's companion data.
"""
import argparse
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import uvicorn
from charpack_core.character_resources import CharacterPackResourceService
from companion_v01.care_runtime import CareModulePort
from companion_v01.deployment_security import AdminWriteAuth
from companion_v01.routes.scene import build_scene_router
from companion_v01.scene.actions.service import RoomService
from companion_v01.scene.adapters.care import CarePort
from companion_v01.scene.repositories.room import RoomRepository
from companion_v01.scene.resources.catalog import ResourceCatalog


def app():
    root = Path(__file__).resolve().parents[1]
    data = root / "work/scene-preview"
    characters = data / "characters"
    preview_pack = data / "characters/akane_v1"
    if not (preview_pack / 'assets').exists():
        shutil.copytree(root / "desktop_pet_creator_kit/characters/akane_v1", preview_pack, dirs_exist_ok=True)
    resources = CharacterPackResourceService(characters_dir=characters)
    preview_pack.mkdir(parents=True, exist_ok=True)
    (preview_pack / "character.json").write_text(json.dumps({"care": {
        "enabled": True, "initial_coins": 20,
        "allowance": {"enabled": True, "coins": 4, "cooldown_seconds": 300, "max_coins": 10},
        "shop_items": [
            {"id": "dango", "name": "三色团子", "price": 7, "effects": {"hunger": 20, "energy": 4, "affection": 2}},
            {"id": "tea", "name": "温热玄米茶", "price": 6, "effects": {"hunger": 5, "energy": 12, "affection": 1}},
        ],
    }}), encoding="utf-8")
    care = CareModulePort.from_feature(enabled=True, storage_path=data / "care.json")
    service = RoomService(repository=RoomRepository(data / "rooms.sqlite3"),
                          care=CarePort(care, SimpleNamespace(characters_dir=data / "characters")),
                          resources=ResourceCatalog(resources, assets_root=root / "web/assets"),
                          record_event=lambda _: {"ok": False, "reason": "preview_has_no_memcore"})
    api = FastAPI(title="Scene UI acceptance: isolated state, model unavailable")
    api.add_middleware(CORSMiddleware, allow_origin_regex=r"^http://(localhost|127\.0\.0\.1)(:\d+)?$",
                       allow_methods=["GET", "POST"], allow_headers=["*"])
    api.include_router(build_scene_router(service=service, admin_auth=AdminWriteAuth.local_compatibility()))
    api.mount("/desktop-pet-character-packs", StaticFiles(directory=characters))

    return api


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=14320)
    uvicorn.run(app(), host="127.0.0.1", port=parser.parse_args().port)
