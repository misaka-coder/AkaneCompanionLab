"""Admin editing of a character pack's shop items: prices, effects, names and custom entries."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
import uuid
from pathlib import Path

from .desktop_pet_character_resources import MAX_CHARACTER_METADATA_BYTES, sanitize_character_pack_id
from .care_runtime import MAX_DESKTOP_INVENTORY_ITEMS, normalize_desktop_care_config

_lock = threading.RLock()

MAX_SHOP_ITEM_NAME_CHARS = 40
MAX_SHOP_ITEM_DESCRIPTION_CHARS = 120
MAX_SHOP_PRICE = 999999
MAX_SHOP_EFFECT = 100
CUSTOM_ITEM_ID_PREFIX = "custom_"
SHOP_MODES = ("desktop_pet", "qq")

# Reasons callers may rely on. Anything else is reported as a generic failure so that
# filesystem details never leave the backend.
SHOP_ERROR_REASONS = frozenset(
    {
        "character_care_unavailable",
        "character_metadata_too_large",
        "character_metadata_unavailable",
        "invalid_character_pack",
        "invalid_character_metadata",
        "invalid_shop_description",
        "invalid_shop_effects",
        "invalid_shop_name",
        "invalid_shop_price",
        "invalid_shop_request",
        "invalid_shop_usable_in",
        "shop_item_ambiguous",
        "shop_item_id_unavailable",
        "shop_item_limit_reached",
        "shop_item_not_found",
    }
)

_UNSAFE_TEXT = re.compile(r"[\x00-\x1f\x7f]")


def _read(service, pack_id):
    clean = sanitize_character_pack_id(pack_id)
    if not clean or clean != pack_id:
        raise ValueError("invalid_character_pack")
    pack = service._resolve_pack_dir(clean)
    if pack is None:
        raise ValueError("invalid_character_pack")
    pack = Path(pack).resolve()
    path = pack / "character.json"
    if path.is_symlink() or path.resolve().parent != pack:
        raise ValueError("invalid_character_metadata")
    if not path.is_file() or path.stat().st_size > MAX_CHARACTER_METADATA_BYTES:
        raise ValueError("character_metadata_unavailable")
    raw = path.read_bytes()
    data = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(data, dict) or not isinstance(data.get("care"), dict):
        raise ValueError("character_care_unavailable")
    return path, raw, data


def _clean_name(value) -> str:
    if not isinstance(value, str) or _UNSAFE_TEXT.search(value):
        raise ValueError("invalid_shop_name")
    name = value.strip()
    if not name or len(name) > MAX_SHOP_ITEM_NAME_CHARS:
        raise ValueError("invalid_shop_name")
    return name


def _clean_description(value) -> str:
    if not isinstance(value, str) or _UNSAFE_TEXT.search(value):
        raise ValueError("invalid_shop_description")
    description = value.strip()
    if len(description) > MAX_SHOP_ITEM_DESCRIPTION_CHARS:
        raise ValueError("invalid_shop_description")
    return description


def _clean_price(value) -> int:
    if type(value) is not int or not 0 <= value <= MAX_SHOP_PRICE:
        raise ValueError("invalid_shop_price")
    return value


def _clean_effects(value) -> dict:
    if not isinstance(value, dict) or set(value) != {"hunger", "energy", "affection"}:
        raise ValueError("invalid_shop_effects")
    if any(
        type(item) is not int or not -MAX_SHOP_EFFECT <= item <= MAX_SHOP_EFFECT
        for item in value.values()
    ):
        raise ValueError("invalid_shop_effects")
    return value


def _clean_usable_in(value) -> list:
    if not isinstance(value, list):
        raise ValueError("invalid_shop_usable_in")
    modes: list[str] = []
    for entry in value:
        mode = str(entry or "").strip().lower()
        if mode not in SHOP_MODES or mode in modes:
            raise ValueError("invalid_shop_usable_in")
        modes.append(mode)
    if "desktop_pet" not in modes:
        raise ValueError("invalid_shop_usable_in")
    return modes


def _shop_items_key(care: dict) -> str:
    return "shopItems" if "shopItems" in care and "shop_items" not in care else "shop_items"


def _write(path: Path, raw: bytes, data: dict):
    """Replace character.json atomically. Returns the new bytes, or None on a lost race."""
    encoded = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if len(encoded) > MAX_CHARACTER_METADATA_BYTES:
        raise ValueError("character_metadata_too_large")
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=".care-shop-", suffix=".tmp", delete=False
        ) as output:
            temp_path = Path(output.name)
            output.write(encoded)
            output.flush()
            os.fsync(output.fileno())
        if path.read_bytes() != raw:
            return None
        os.replace(temp_path, path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
    return encoded


def _shop_view(data: dict) -> list:
    """Desktop-facing view of the shelf, including fields the editor needs to round-trip."""
    care = data["care"]
    raw_items = care.get("shop_items", care.get("shopItems"))
    details: dict = {}
    if isinstance(raw_items, list):
        for entry in raw_items[:MAX_DESKTOP_INVENTORY_ITEMS]:
            if not isinstance(entry, dict):
                continue
            item_id = str(entry.get("id") or "").strip()
            if item_id:
                details.setdefault(item_id, entry)
    items = []
    for item in normalize_desktop_care_config(care)["shop_items"]:
        entry = details.get(item["id"], {})
        raw_modes = entry.get("usable_in", entry.get("usableIn"))
        modes = (
            [str(mode).strip().lower() for mode in raw_modes if str(mode).strip()]
            if isinstance(raw_modes, list)
            else []
        )
        items.append(
            {
                "id": item["id"],
                "name": item["name"],
                "price": item["price"],
                "effects": dict(item["effects"]),
                "description": str(entry.get("description") or "").strip()[
                    :MAX_SHOP_ITEM_DESCRIPTION_CHARS
                ],
                "usable_in": modes or list(SHOP_MODES),
                "custom": bool(entry.get("custom")),
            }
        )
    return items


def _result(raw: bytes, data: dict) -> dict:
    config = normalize_desktop_care_config(data["care"])
    return {
        "ok": True,
        "revision": hashlib.sha256(raw).hexdigest(),
        "items": _shop_view(data),
        "enabled": config["enabled"],
    }


def read_shop(service, pack_id):
    with _lock:
        _, raw, data = _read(service, pack_id)
        return _result(raw, data)


def update_shop_item(service, pack_id, payload):
    with _lock:
        path, raw, data = _read(service, pack_id)
        if payload.get("revision") != hashlib.sha256(raw).hexdigest():
            return {"ok": False, "status": "conflict", "reason": "shop_revision_conflict"}
        item_id = payload.get("item_id")
        care = data["care"]
        items = care.get(_shop_items_key(care), [])
        if not isinstance(items, list) or item_id not in {
            item["id"] for item in normalize_desktop_care_config(care)["shop_items"]
        }:
            raise ValueError("shop_item_not_found")
        matches = [
            entry for entry in items if isinstance(entry, dict) and entry.get("id") == item_id
        ]
        if len(matches) != 1:
            raise ValueError("shop_item_ambiguous")
        entry = matches[0]
        price = _clean_price(payload.get("price"))
        effects = _clean_effects(payload.get("effects"))
        if "name" in payload:
            entry["name"] = _clean_name(payload.get("name"))
        if "description" in payload:
            description = _clean_description(payload.get("description"))
            if description:
                entry["description"] = description
            else:
                entry.pop("description", None)
        if "usable_in" in payload:
            modes = _clean_usable_in(payload.get("usable_in"))
            entry["usableIn" if "usable_in" not in entry else "usable_in"] = modes
        entry["price"] = price
        # Merge so that special-effect keys the editor does not manage stay intact.
        entry["effects"] = {**entry.get("effects", {}), **effects}
        encoded = _write(path, raw, data)
        if encoded is None:
            return {"ok": False, "status": "conflict", "reason": "shop_revision_conflict"}
        return _result(encoded, data)


def create_shop_item(service, pack_id, payload):
    with _lock:
        path, raw, data = _read(service, pack_id)
        if payload.get("revision") != hashlib.sha256(raw).hexdigest():
            return {"ok": False, "status": "conflict", "reason": "shop_revision_conflict"}
        care = data["care"]
        key = _shop_items_key(care)
        items = care.setdefault(key, [])
        if not isinstance(items, list):
            raise ValueError("character_care_unavailable")
        if len(items) >= MAX_DESKTOP_INVENTORY_ITEMS:
            raise ValueError("shop_item_limit_reached")
        name = _clean_name(payload.get("name"))
        price = _clean_price(payload.get("price"))
        effects = _clean_effects(payload.get("effects"))
        description = _clean_description(payload.get("description", ""))
        modes = _clean_usable_in(payload.get("usable_in", list(SHOP_MODES)))
        existing = {str(entry.get("id") or "") for entry in items if isinstance(entry, dict)}
        item_id = ""
        for _ in range(16):
            candidate = f"{CUSTOM_ITEM_ID_PREFIX}{uuid.uuid4().hex[:12]}"
            if candidate not in existing:
                item_id = candidate
                break
        if not item_id:
            raise ValueError("shop_item_id_unavailable")
        entry = {
            "id": item_id,
            "name": name,
            "price": price,
            "category": "custom",
            "preference_tags": [],
            "usable_in": modes,
            "custom": True,
            "effects": {
                "hunger": effects["hunger"],
                "energy": effects["energy"],
                "affection": effects["affection"],
            },
        }
        if description:
            entry["description"] = description
        items.append(entry)
        encoded = _write(path, raw, data)
        if encoded is None:
            return {"ok": False, "status": "conflict", "reason": "shop_revision_conflict"}
        result = _result(encoded, data)
        result["item_id"] = item_id
        return result


def delete_shop_item(service, pack_id, payload):
    """Remove one shelf entry. Inventory counts for it stop being accepted afterwards."""
    with _lock:
        path, raw, data = _read(service, pack_id)
        if payload.get("revision") != hashlib.sha256(raw).hexdigest():
            return {"ok": False, "status": "conflict", "reason": "shop_revision_conflict"}
        item_id = str(payload.get("item_id") or "").strip()
        care = data["care"]
        items = care.get(_shop_items_key(care))
        if not isinstance(items, list):
            raise ValueError("character_care_unavailable")
        matches = [
            entry for entry in items if isinstance(entry, dict) and str(entry.get("id") or "") == item_id
        ]
        if not item_id or not matches:
            raise ValueError("shop_item_not_found")
        if len(matches) != 1:
            raise ValueError("shop_item_ambiguous")
        items.remove(matches[0])
        encoded = _write(path, raw, data)
        if encoded is None:
            return {"ok": False, "status": "conflict", "reason": "shop_revision_conflict"}
        return _result(encoded, data)
