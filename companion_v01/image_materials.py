from __future__ import annotations

import base64
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SUPPORTED_IMAGE_MEDIA_TYPES = frozenset({"image/jpeg", "image/png", "image/gif", "image/webp"})


@dataclass(frozen=True)
class ResolvedImageMaterial:
    source_type: str
    source_id: str
    handle: str
    title: str
    media_type: str
    path: Path
    file_size: int

    def safe_dict(self) -> dict[str, Any]:
        return {
            "source_type": self.source_type,
            "source_id": self.source_id,
            "handle": self.handle,
            "title": self.title,
            "media_type": self.media_type,
            "file_size": self.file_size,
        }


class SessionImageMaterialResolver:
    """Resolve current-session attachment/generated handles to managed image bytes."""

    def __init__(self, *, attachment_service: Any, generated_file_service: Any) -> None:
        self.attachment_service = attachment_service
        self.generated_file_service = generated_file_service

    def resolve_many(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        targets: list[str],
        max_count: int = 5,
        max_bytes_per_image: int = 8 * 1024 * 1024,
        max_total_bytes: int = 20 * 1024 * 1024,
    ) -> dict[str, Any]:
        normalized_targets = list(
            dict.fromkeys(str(item or "").strip() for item in list(targets or []) if str(item or "").strip())
        )[: max(1, min(5, int(max_count or 5)))]
        if not normalized_targets:
            return {"ok": False, "status": "invalid_arguments", "materials": [], "unresolved": []}

        per_image_limit = max(128 * 1024, int(max_bytes_per_image or 0))
        total_limit = max(per_image_limit, int(max_total_bytes or 0))
        materials: list[ResolvedImageMaterial] = []
        unresolved: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        total_bytes = 0
        for target in normalized_targets:
            resolved = self._resolve_one(
                profile_user_id=profile_user_id,
                session_id=session_id,
                target=target,
            )
            if resolved is None:
                unresolved.append({"target": target, "reason": "image_not_found"})
                continue
            key = (resolved.source_type, resolved.source_id)
            if key in seen:
                continue
            seen.add(key)
            if resolved.file_size <= 0 or resolved.file_size > per_image_limit:
                unresolved.append({"target": target, "reason": "image_size_limit"})
                continue
            if total_bytes + resolved.file_size > total_limit:
                unresolved.append({"target": target, "reason": "image_total_size_limit"})
                continue
            total_bytes += resolved.file_size
            materials.append(resolved)

        return {
            "ok": bool(materials),
            "status": "ready" if materials and not unresolved else "partial" if materials else "unavailable",
            "materials": materials,
            "unresolved": unresolved,
            "total_bytes": total_bytes,
        }

    def build_model_image_inputs(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        targets: list[str],
        max_count: int = 5,
        max_bytes_per_image: int = 8 * 1024 * 1024,
        max_total_bytes: int = 20 * 1024 * 1024,
    ) -> dict[str, Any]:
        result = self.resolve_many(
            profile_user_id=profile_user_id,
            session_id=session_id,
            targets=targets,
            max_count=max_count,
            max_bytes_per_image=max_bytes_per_image,
            max_total_bytes=max_total_bytes,
        )
        images: list[dict[str, Any]] = []
        loaded_materials: list[ResolvedImageMaterial] = []
        for material in list(result.get("materials") or []):
            if not isinstance(material, ResolvedImageMaterial):
                continue
            try:
                image_bytes = material.path.read_bytes()
            except OSError:
                result.setdefault("unresolved", []).append({"target": material.handle, "reason": "image_unreadable"})
                continue
            images.append(
                {
                    "attachment_id": material.source_id,
                    "attachment_handle": material.handle,
                    "title": material.title,
                    "media_type": material.media_type,
                    "data_url": f"data:{material.media_type};base64,{base64.b64encode(image_bytes).decode('ascii')}",
                }
            )
            loaded_materials.append(material)
        safe_materials = [item.safe_dict() for item in loaded_materials]
        return {
            "ok": bool(images),
            "status": "ready" if images and not result.get("unresolved") else "partial" if images else "unavailable",
            "images": images,
            "materials": safe_materials,
            "unresolved": list(result.get("unresolved") or []),
            "total_bytes": sum(item.file_size for item in loaded_materials),
        }

    def _resolve_one(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        target: str,
    ) -> ResolvedImageMaterial | None:
        normalized = str(target or "").strip()
        if not normalized:
            return None
        if normalized.lower().startswith(("gen_", "generated::")):
            generated = self.generated_file_service.resolve_generated_artifact(
                profile_user_id=profile_user_id,
                session_id=session_id,
                target=normalized,
            )
            return self._from_generated(generated)

        attachment = self.attachment_service.resolve_attachment(
            profile_user_id=profile_user_id,
            session_id=session_id,
            target=normalized,
            kind="image",
        )
        material = self._from_attachment(attachment)
        if material is not None:
            return material
        generated = self.generated_file_service.resolve_generated_artifact(
            profile_user_id=profile_user_id,
            session_id=session_id,
            target=normalized,
        )
        return self._from_generated(generated)

    def _from_attachment(self, item: Any) -> ResolvedImageMaterial | None:
        if not isinstance(item, dict) or str(item.get("kind") or "").strip().lower() != "image":
            return None
        path = self.attachment_service.resolve_storage_path(item)
        if path is None:
            return None
        return self._build_material(
            source_type="attachment",
            source_id=str(item.get("attachment_id") or "").strip(),
            handle=str(item.get("attachment_handle") or item.get("attachment_id") or "").strip(),
            title=str(item.get("summary_title") or item.get("origin_name") or "图片材料").strip(),
            media_type=str(item.get("mime_type") or "").strip(),
            path=path,
        )

    def _from_generated(self, item: Any) -> ResolvedImageMaterial | None:
        if not isinstance(item, dict):
            return None
        output_format = str(item.get("output_format") or item.get("file_ext") or "").strip().lower().lstrip(".")
        media_type = str(item.get("mime_type") or "").strip().lower()
        if output_format not in {"png", "jpg", "jpeg", "webp", "gif"} and not media_type.startswith("image/"):
            return None
        path = self.generated_file_service.absolute_path(item)
        return self._build_material(
            source_type="generated",
            source_id=str(item.get("generated_id") or "").strip(),
            handle=str(item.get("generated_handle") or item.get("generated_id") or "").strip(),
            title=str(item.get("output_title") or "生成图片").strip(),
            media_type=media_type,
            path=path,
        )

    def _build_material(
        self,
        *,
        source_type: str,
        source_id: str,
        handle: str,
        title: str,
        media_type: str,
        path: Path,
    ) -> ResolvedImageMaterial | None:
        target = Path(path)
        if not source_id or not handle or not target.is_file():
            return None
        try:
            file_size = int(target.stat().st_size)
        except OSError:
            return None
        normalized_media_type = str(media_type or mimetypes.guess_type(target.name)[0] or "").strip().lower()
        if normalized_media_type == "image/jpg":
            normalized_media_type = "image/jpeg"
        if normalized_media_type not in SUPPORTED_IMAGE_MEDIA_TYPES:
            return None
        return ResolvedImageMaterial(
            source_type=source_type,
            source_id=source_id,
            handle=handle,
            title=title[:160] or handle,
            media_type=normalized_media_type,
            path=target,
            file_size=file_size,
        )
