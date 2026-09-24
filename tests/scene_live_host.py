"""Opt-in full-host acceptance run using configured models and isolated data.

Does not copy private histories, inventories, plugin state or runtime secrets.
The normal config loader resolves model credentials without printing them.
"""
import json
import argparse
import os
from pathlib import Path
import shutil


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-config", default="", help="Read existing model settings without copying credentials")
    options = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    data = root / "work/scene-live"
    os.environ.update({"AKANE_DATA_ROOT": str(data), "AKANE_INSTANCE_ID": "",
                       "AKANE_ADMIN_TOKEN": "", "QQ_BRIDGE_ENABLED": "false",
                       "EXECUTION_ENABLED": "false", "EXECUTION_QQ_ENABLED": "false"})
    target = data / "characters/akane_v1"
    if not target.exists():
        shutil.copytree(root / "desktop_pet_creator_kit/characters/akane_v1", target)
        meta = json.loads((target / "character.json").read_text(encoding="utf-8"))
        meta["care"] = {
            "enabled": True,
            "initial_coins": 20,
            "allowance": {"enabled": True, "coins": 4, "cooldown_seconds": 300, "max_coins": 10},
            "shop_items": [
                {"id": "dango", "name": "三色团子", "price": 7,
                 "effects": {"hunger": 20, "energy": 4, "affection": 2}},
                {"id": "tea", "name": "温热玄米茶", "price": 6,
                 "effects": {"hunger": 5, "energy": 12, "affection": 1}},
            ],
        }
        (target / "character.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    model_cfg = options.model_config
    if not model_cfg:
        default_model = data / "users_data/_local/model_service.json"
        if default_model.exists():
            model_cfg = str(default_model)
    if model_cfg:
        import config
        from companion_v01.model_service_config import ModelServiceConfigStore, apply_model_service_settings
        settings = ModelServiceConfigStore(Path(model_cfg)).load()
        if settings is not None and settings.configured:
            apply_model_service_settings(config, settings)
    import uvicorn
    uvicorn.run("companion_v01.app:app", host="127.0.0.1", port=14321, log_level="warning")


if __name__ == "__main__":
    main()
