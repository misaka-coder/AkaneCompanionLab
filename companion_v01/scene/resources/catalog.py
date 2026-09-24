from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.parse import quote

from ..contracts import Asset, Catalog, Outfit


class ResourceCatalog:
    def __init__(self, character_resources, *, assets_root: Path, characters_dir: Path | None = None, route_prefix: str = ""):
        self.characters = character_resources
        self.assets_root = assets_root.resolve()
        self.characters_dir = characters_dir.resolve() if characters_dir else None
        self.prefix = route_prefix.rstrip("/")

    def build(self, pack_id: str) -> Catalog:
        manifest = self.characters.build_runtime_manifest(pack_id)
        if not manifest:
            raise ValueError("character_pack_unavailable")
        outfits = [Outfit(
            id=item["id"], name=item.get("name") or item["id"],
            emotions=[Asset(id=e["id"], name=e.get("name") or e["id"], url=e["path"])
                      for e in item.get("emotions", []) if e.get("path")],
        ) for item in manifest.get("characters", {}).get("outfits", [])]
        outfits = [o for o in outfits if o.emotions]
        backgrounds = []
        for major in manifest.get("scenes", {}).get("majors", []):
            for minor in major.get("minors", []):
                for background in minor.get("backgrounds", []):
                    if background.get("path"):
                        backgrounds.append(Asset(id=f"pack:{major['id']}/{minor['id']}/{background['id']}",
                                                 name=background.get("name") or minor.get("name") or background["id"],
                                                 url=background["path"]))
        for path in sorted((self.assets_root / "scenes").rglob("*.png")):
            rel = path.relative_to(self.assets_root).as_posix()
            backgrounds.append(Asset(id=rel, name=path.stem.strip(), url=self._url(rel)))
        # Existing alternate outfits belong to Akane only, never another pack.
        if pack_id == "akane_v1":
            for name in ("水手服", "睡衣"):
                if any(o.id == f"builtin-{name}" for o in outfits):
                    continue
                folder = self.assets_root / "备份" / name
                emotions = [Asset(id=p.stem, name=p.stem, url=self._url(p.relative_to(self.assets_root).as_posix()))
                            for p in sorted(folder.glob("*.png"))]
                if emotions:
                    outfits.append(Outfit(id=f"builtin-{name}", name=name, emotions=emotions))
        music = []
        bgm_root = self.assets_root / "bgm"
        if bgm_root.is_dir():
            for p in sorted(bgm_root.rglob("*")):
                if p.is_file() and p.suffix.lower() in {".mp3", ".flac", ".ogg", ".wav", ".m4a"}:
                    display_name = p.stem
                    meta_p = p.with_name(f"{p.name}.meta.json")
                    if meta_p.is_file():
                        try:
                            meta_data = json.loads(meta_p.read_text(encoding="utf-8"))
                            display_name = meta_data.get("name") or display_name
                        except Exception:
                            pass
                    rel = p.relative_to(self.assets_root).as_posix()
                    music.append(Asset(id=rel, name=display_name, url=self._url(rel)))
        data = dict(backgrounds=backgrounds, outfits=outfits, music=music)
        revision = hashlib.sha256(json.dumps(
            {k: [v.model_dump() for v in values] for k, values in data.items()},
            ensure_ascii=False, sort_keys=True,
        ).encode()).hexdigest()[:20]
        return Catalog(revision=revision, **data)

    def file(self, relative: str) -> Path:
        # 1. Check assets_root first (scenes, 备份, bgm)
        try:
            path = (self.assets_root / relative).resolve()
            path.relative_to(self.assets_root)
            if (path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".flac", ".mp3", ".ogg", ".wav", ".m4a"}
                    and relative.split("/")[0] in {"scenes", "备份", "bgm"}):
                return path
        except (ValueError, OSError):
            pass

        # 2. Check characters_dir (characters/<pack_id>/...)
        if self.characters_dir and relative.startswith("characters/"):
            try:
                char_rel = relative[len("characters/"):]
                path = (self.characters_dir / char_rel).resolve()
                path.relative_to(self.characters_dir)
                if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".flac", ".mp3", ".ogg", ".wav", ".m4a"}:
                    return path
            except (ValueError, OSError):
                pass

        raise ValueError("resource_unavailable")

    def _url(self, relative: str) -> str:
        return f"{self.prefix}/scene/assets/{quote(relative, safe='/')}"
