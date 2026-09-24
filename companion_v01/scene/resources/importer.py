import base64
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import uuid

from PIL import Image


class ResourceImporter:
    def __init__(self, room):
        self.room = room

    @staticmethod
    def _detect_audio_suffix(raw: bytes) -> str | None:
        if len(raw) < 4:
            return None
        if raw.startswith(b"ID3") or (len(raw) >= 2 and raw[0] == 0xFF and (raw[1] & 0xE0) == 0xE0):
            return ".mp3"
        if raw.startswith(b"OggS"):
            return ".ogg"
        if raw.startswith(b"RIFF") and len(raw) >= 12 and raw[8:12] == b"WAVE":
            return ".wav"
        if raw.startswith(b"fLaC"):
            return ".flac"
        if len(raw) >= 12 and (raw[4:8] in {b"ftyp", b"moov"} or raw[4:12].startswith(b"M4A")):
            return ".m4a"
        return None

    def publish(self, request):
        resources = self.room.resources.characters
        # Reuse the character service's pack-id and containment authority.
        pack = resources._resolve_pack_dir(request.identity.character_pack_id)
        if pack is None or not (pack / "character.json").is_file():
            raise ValueError("character_pack_unavailable")
        pack = pack.resolve()
        assets = (pack / "assets").resolve()
        assets.relative_to(pack)

        if request.kind == "music":
            try:
                raw = base64.b64decode(request.data, validate=True)
            except ValueError as exc:
                raise ValueError("invalid_audio") from exc
            if not raw:
                raise ValueError("invalid_audio")
            if len(raw) > 20 * 1024 * 1024:
                raise ValueError("audio_too_large")
            suffix = self._detect_audio_suffix(raw)
            if not suffix:
                raise ValueError("unsupported_audio")

            digest = hashlib.sha256(request.kind.encode() + request.name.encode() + raw).hexdigest()[:20]
            resource_id = f"import-{digest}"
            clean_name = re.sub(r'[\\/*?:"<>|]', "", request.name).strip() or "track"
            target_dir = (self.room.resources.assets_root / "bgm" / "custom").resolve()
            target_dir.relative_to(self.room.resources.assets_root)
            target = (target_dir / f"{clean_name}_{digest[:8]}{suffix}").resolve()
            target.relative_to(target_dir)
            meta_file = target.with_name(f"{target.name}.meta.json")

            if not target.exists():
                stage = (pack / "_local/scene-staging" / uuid.uuid4().hex).resolve()
                stage.relative_to(pack)
                stage.mkdir(parents=True)
                try:
                    temp_audio = stage / target.name
                    temp_audio.write_bytes(raw)
                    temp_meta = stage / meta_file.name
                    temp_meta.write_text(json.dumps({"id": resource_id, "name": request.name}, ensure_ascii=False), encoding="utf-8")
                    target_dir.mkdir(parents=True, exist_ok=True)
                    try:
                        shutil.move(str(temp_audio), str(target))
                        shutil.move(str(temp_meta), str(meta_file))
                    except (FileExistsError, shutil.Error):
                        pass
                finally:
                    if stage.exists():
                        shutil.rmtree(stage, ignore_errors=True)
            event_id = f"resource:{request.identity.character_pack_id}:{resource_id}"
            fact = {"event_type": "scene.resource_added", "fields": {
                "resource_id": resource_id, "kind": request.kind, "name": request.name, "equipped": False,
            }}
            with self.room.repository.transaction() as db:
                db.execute("INSERT OR IGNORE INTO outbox(event_id,identity,fact) VALUES (?,?,?)",
                           (event_id, request.identity.model_dump_json(), json.dumps(fact, ensure_ascii=False)))
            self.room.flush_events()
            return self.room.snapshot(request.identity)

        try:
            raw = base64.b64decode(request.data, validate=True)
        except ValueError as exc:
            raise ValueError("invalid_image") from exc
        if not raw or len(raw) > 12 * 1024 * 1024:
            raise ValueError("image_too_large")
        try:
            with Image.open(io.BytesIO(raw)) as image:
                if image.format not in {"PNG", "JPEG", "WEBP"}:
                    raise ValueError("unsupported_image")
                if image.width < 128 or image.height < 128 or image.width * image.height > 24_000_000:
                    raise ValueError("invalid_image_dimensions")
                suffix = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}[image.format]
                image.verify()
            if request.kind == "outfit":
                with Image.open(io.BytesIO(raw)) as image:
                    if "A" not in image.getbands():
                        raise ValueError("portrait_needs_transparency")
                    low, high = image.getchannel("A").getextrema()
                    if low == 255 or high == 0:
                        raise ValueError("portrait_needs_transparency")
        except (OSError, Image.DecompressionBombError) as exc:
            raise ValueError("invalid_image") from exc
        digest = hashlib.sha256(request.kind.encode() + request.name.encode() + raw).hexdigest()[:20]
        resource_id = f"import-{digest}"
        relative = Path("characters") / resource_id if request.kind == "outfit" else Path("scenes") / "room" / resource_id
        target = (assets / relative).resolve()
        target.relative_to(assets)
        if not target.exists():
            stage = (pack / "_local/scene-staging" / uuid.uuid4().hex).resolve()
            stage.relative_to(pack)
            stage.mkdir(parents=True)
            try:
                stem = "normal" if request.kind == "outfit" else "background"
                (stage / (stem + suffix)).write_bytes(raw)
                (stage / "meta.json").write_text(json.dumps({"id": resource_id, "name": request.name}, ensure_ascii=False), encoding="utf-8")
                if request.kind == "background":
                    (stage / "background.meta.json").write_text(json.dumps({"name": request.name}, ensure_ascii=False), encoding="utf-8")
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    stage.replace(target)
                except FileExistsError:
                    pass  # Concurrent identical import already published the same immutable bytes.
            finally:
                if stage.exists():
                    stage.relative_to(pack / "_local/scene-staging")
                    shutil.rmtree(stage)
        event_id = f"resource:{request.identity.character_pack_id}:{resource_id}"
        fact = {"event_type": "scene.resource_added", "fields": {
            "resource_id": resource_id, "kind": request.kind, "name": request.name, "equipped": False,
        }}
        with self.room.repository.transaction() as db:
            db.execute("INSERT OR IGNORE INTO outbox(event_id,identity,fact) VALUES (?,?,?)",
                       (event_id, request.identity.model_dump_json(), json.dumps(fact, ensure_ascii=False)))
        self.room.flush_events()
        return self.room.snapshot(request.identity)
