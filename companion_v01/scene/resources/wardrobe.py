"""Publish a bundled outfit into the pack before it becomes equipped."""
import json
import shutil
import uuid


def publish_bundled_outfit(catalog, pack_id, outfit_id):
    if pack_id != "akane_v1" or outfit_id not in {"builtin-水手服", "builtin-睡衣"}:
        return
    pack = catalog.characters._resolve_pack_dir(pack_id).resolve()
    target = (pack / "assets/characters" / outfit_id).resolve()
    target.relative_to(pack)
    if target.exists():
        return
    name = outfit_id.removeprefix("builtin-")
    source = (catalog.assets_root / "备份" / name).resolve()
    source.relative_to(catalog.assets_root)
    stage = (pack / "_local/scene-staging" / uuid.uuid4().hex).resolve()
    stage.relative_to(pack)
    stage.mkdir(parents=True)
    try:
        for image in source.glob("*.png"):
            image.resolve().relative_to(catalog.assets_root)
            shutil.copyfile(image, stage / image.name)
        (stage / "meta.json").write_text(json.dumps({"id": outfit_id, "name": name}, ensure_ascii=False), encoding="utf-8")
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            stage.replace(target)
        except FileExistsError:
            pass
    finally:
        if stage.exists():
            stage.relative_to(pack / "_local/scene-staging")
            shutil.rmtree(stage)
