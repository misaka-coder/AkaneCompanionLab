from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse


def build_web_static_router(
    *,
    web_dir: Path,
    modules_dir: Path,
    vendor_dir: Path,
) -> APIRouter:
    router = APIRouter()

    @router.get("/")
    async def index():
        return FileResponse(web_dir / "index.html")

    @router.get("/monogatari")
    async def monogatari():
        return FileResponse(web_dir / "monogatari.html")

    @router.get("/monogatari.css")
    async def monogatari_css():
        return FileResponse(
            web_dir / "monogatari.css",
            media_type="text/css",
            headers={"Cache-Control": "no-store"},
        )

    @router.get("/preview")
    async def preview():
        return FileResponse(web_dir / "index.html")

    @router.get("/resource-preview")
    async def resource_preview():
        return FileResponse(web_dir / "preview.html")

    @router.get("/live2d-preview")
    async def live2d_preview():
        return FileResponse(web_dir / "live2d-preview.html")

    @router.get("/styles.css")
    async def styles():
        return FileResponse(web_dir / "styles.css")

    @router.get("/app.js")
    async def app_js():
        return FileResponse(
            web_dir / "app.js",
            media_type="application/javascript",
            headers={"Cache-Control": "no-store"},
        )

    @router.get("/modules/{module_path:path}")
    async def module_js(module_path: str):
        if not modules_dir.exists():
            raise HTTPException(status_code=404, detail="module directory not found")

        requested = (modules_dir / unquote(module_path)).resolve()
        try:
            requested.relative_to(modules_dir.resolve())
        except ValueError:
            raise HTTPException(status_code=404, detail="module not found")
        if not requested.is_file():
            raise HTTPException(status_code=404, detail="module not found")

        media_type = "application/javascript" if requested.suffix.lower() == ".js" else "application/octet-stream"
        return FileResponse(
            requested,
            media_type=media_type,
            headers={"Cache-Control": "no-store"},
        )

    @router.get("/vendor/{vendor_path:path}")
    async def vendor_file(vendor_path: str):
        if not vendor_dir.exists():
            raise HTTPException(status_code=404, detail="vendor directory not found")

        requested = (vendor_dir / unquote(vendor_path)).resolve()
        try:
            requested.relative_to(vendor_dir.resolve())
        except ValueError:
            raise HTTPException(status_code=404, detail="vendor file not found")
        if not requested.is_file():
            raise HTTPException(status_code=404, detail="vendor file not found")

        media_type = "application/javascript" if requested.suffix.lower() == ".js" else "application/octet-stream"
        return FileResponse(
            requested,
            media_type=media_type,
            headers={"Cache-Control": "public, max-age=31536000, immutable"},
        )

    @router.get("/preview.css")
    async def preview_css():
        return FileResponse(web_dir / "preview.css")

    @router.get("/preview.js")
    async def preview_js():
        return FileResponse(web_dir / "preview.js", media_type="application/javascript")

    @router.get("/live2d-preview.css")
    async def live2d_preview_css():
        return FileResponse(web_dir / "live2d-preview.css")

    @router.get("/live2d-preview.js")
    async def live2d_preview_js():
        return FileResponse(web_dir / "live2d-preview.js", media_type="application/javascript")

    return router
