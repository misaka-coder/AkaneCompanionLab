import json
from pathlib import Path
from ...care_runtime import normalize_desktop_care_config
from ...desktop_pet_character_resources import load_character_care_config
from ..contracts import CareState, Identity, ShopItem


class CarePort:
    def __init__(self, module, resources):
        self.module = module
        self.resources = resources

    def config(self, identity: Identity) -> dict:
        return normalize_desktop_care_config(load_character_care_config(self.resources, identity.character_pack_id))

    def snapshot(self, identity: Identity) -> CareState:
        config = self.config(identity)
        if not self.module.enabled or not config["enabled"]:
            return CareState(enabled=False, reason="care_disabled")
        result = self.module.runtime.snapshot_for_desktop(
            profile_user_id=identity.profile_user_id, character_pack_id=identity.character_pack_id,
            care_config=config,
        )
        state = result["snapshot"]
        return CareState(enabled=True, **{key: state[key] for key in
            ("coins", "hunger", "energy", "affection", "inventory")})

    def shop(self, identity: Identity) -> list[ShopItem]:
        return [ShopItem(**{k: item[k] for k in ("id", "name", "price", "effects")},
                         description=item.get("description", "")) for item in self.config(identity)["shop_items"]]

    def add_shop_item(self, identity: Identity, *, name: str, price: int, description: str, effects: dict[str, int], icon: str = "cookie", item_id: str = "") -> dict:
        characters_dir = getattr(self.resources, "characters_dir", None)
        if not characters_dir:
            raise ValueError("character_pack_unavailable")
        root = Path(characters_dir).resolve()
        pack_id = identity.character_pack_id
        target = (root / pack_id / "character.json").resolve()
        target.relative_to(root)
        if not target.is_file():
            raise ValueError("character_pack_unavailable")
        meta = json.loads(target.read_text(encoding="utf-8"))
        care = meta.setdefault("care", {})
        care.setdefault("enabled", True)
        shop_items = care.setdefault("shop_items", [])
        import hashlib
        if not item_id:
            item_id = f"item_{hashlib.sha256(name.encode()).hexdigest()[:8]}"
        # If an item with the same id or name exists, update it; otherwise append
        existing = next((i for i in shop_items if (item_id and i.get("id") == item_id) or i.get("name") == name), None)
        item_entry = {
            "id": item_id if not existing else existing["id"],
            "name": name,
            "price": max(0, min(price, 999999)),
            "description": description or f"美味的小点心 · 饱食 +{effects.get('hunger', 0)}",
            "effects": {k: int(v) for k, v in effects.items() if k in {"hunger", "energy", "affection"}},
            "icon": icon,
            "usable_in": ["desktop_pet"],
        }
        if existing:
            existing.update(item_entry)
        else:
            shop_items.append(item_entry)
        # Atomic write
        temp_path = target.with_suffix(".tmp")
        temp_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_path.replace(target)
        return item_entry

    def action(self, identity: Identity, *, kind: str, target: str, count: int = 1, request_id: str = "") -> dict:
        config = self.config(identity)
        if not self.module.enabled or not config["enabled"]:
            return {"ok": False, "reason": "care_disabled"}
        return self.module.runtime.perform_desktop_action(
            profile_user_id=identity.profile_user_id, character_pack_id=identity.character_pack_id,
            care_config=config, action=kind, item_id=target, count=count, request_id=request_id,
        )
