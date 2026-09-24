"""Thin HTTP boundary for the immersive room."""
import asyncio

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from ..scene.contracts import (
    ActionRequest,
    ActionResult,
    Identity,
    Snapshot,
    Presentation,
    Receipt,
    TurnRequest,
    ImportRequest,
    CancelRequest,
    AddShopItemRequest,
    StoryCatalogResponse,
    StoryRunState,
    StoryStartRequest,
    StoryStepRequest,
    StoryCompleteEndingRequest,
    StoryImportRequest,
)
from ..scene.resources.importer import ResourceImporter


def build_scene_router(*, service, admin_auth) -> APIRouter:
    router = APIRouter(prefix="/scene", tags=["scene"])

    def authorize(request):
        authorization = admin_auth.authorize(request)
        if not authorization.ok:
            raise HTTPException(authorization.status_code, authorization.reason)

    def presentation_service():
        service_port = getattr(service, "presentation", None)
        if service_port is None:
            raise HTTPException(503, "scene_model_unavailable")
        return service_port

    @router.post("/shop/items/add", response_model=Snapshot)
    async def add_shop_item(payload: AddShopItemRequest, request: Request):
        authorize(request)
        try:
            care_port = getattr(service, "care", None)
            if care_port is None or not hasattr(care_port, "add_shop_item"):
                raise HTTPException(503, "care_unavailable")
            await asyncio.to_thread(
                care_port.add_shop_item,
                payload.identity,
                name=payload.name,
                price=payload.price,
                description=payload.description,
                effects=payload.effects,
                icon=payload.icon,
                item_id=payload.item_id,
            )
            return await asyncio.to_thread(service.snapshot, payload.identity)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.post("/resources/import", response_model=Snapshot)
    async def import_resource(payload: ImportRequest, request: Request):
        authorize(request)
        try:
            return await asyncio.to_thread(ResourceImporter(service).publish, payload)
        except ValueError as exc:
            public = {"character_pack_unavailable", "invalid_image", "image_too_large", "unsupported_image",
                      "invalid_image_dimensions", "portrait_needs_transparency",
                      "invalid_audio", "audio_too_large", "unsupported_audio"}
            raise HTTPException(400, str(exc) if str(exc) in public else "resource_import_rejected") from exc

    @router.post("/turn", response_model=Presentation)
    async def turn(payload: TurnRequest, request: Request):
        authorize(request)
        try:
            return await presentation_service().turn(payload)
        except asyncio.CancelledError as exc:
            raise HTTPException(409, "presentation_cancelled") from exc
        except ValueError as exc:
            # Validation may include generated private text; only public codes escape.
            public = {"model_busy", "request_id_conflict", "event_not_found", "empty_scene_turn",
                      "presentation_generating", "presentation_failed", "presentation_cancelled", "model_response_failed"}
            reason = str(exc) if type(exc) is ValueError and str(exc) in public else "model_presentation_invalid"
            raise HTTPException(409, reason) from exc
        except Exception as exc:
            raise HTTPException(503, "model_response_failed") from exc

    @router.post("/cancel")
    async def cancel(payload: CancelRequest, request: Request):
        authorize(request)
        return presentation_service().cancel(payload)

    @router.post("/receipts")
    async def receipt(payload: Receipt, request: Request):
        authorize(request)
        try:
            return await asyncio.to_thread(presentation_service().receipt, payload)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/snapshot", response_model=Snapshot)
    async def snapshot(identity: Identity):
        try:
            return await asyncio.to_thread(service.snapshot, identity)
        except ValueError as exc:
            raise HTTPException(409, "character_pack_unavailable") from exc

    @router.post("/actions", response_model=ActionResult)
    async def action(payload: ActionRequest, request: Request):
        authorization = admin_auth.authorize(request)
        if not authorization.ok:
            raise HTTPException(authorization.status_code, authorization.reason)
        try:
            return await asyncio.to_thread(service.action, payload)
        except ValueError as exc:
            raise HTTPException(409, "resource_unavailable") from exc

    @router.post("/story/catalog", response_model=StoryCatalogResponse)
    async def story_catalog(identity: Identity, request: Request):
        return await asyncio.to_thread(service.story.catalog, identity)

    @router.post("/story/start", response_model=StoryRunState)
    async def story_start(payload: StoryStartRequest, request: Request):
        authorize(request)
        try:
            return await asyncio.to_thread(service.story.start, payload.identity, payload.story_id, payload.force_restart)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.post("/story/complete_ending", response_model=StoryRunState)
    async def story_complete_ending(payload: StoryCompleteEndingRequest, request: Request):
        authorize(request)
        try:
            result = await asyncio.to_thread(service.story.complete_ending, payload.identity, payload.run_id, payload.ending_id)
            await asyncio.to_thread(service.flush_events)
            return result
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.post("/story/step", response_model=StoryRunState)
    async def story_step(payload: StoryStepRequest, request: Request):
        authorize(request)
        try:
            result = await service.story.step(payload)
            await asyncio.to_thread(service.flush_events)
            return result
        except asyncio.CancelledError as exc:
            raise HTTPException(409, "story_cancelled") from exc
        except ValueError as exc:
            public = {"model_busy", "model_generation_failed", "model_generation_unavailable", "model_empty_response",
                      "story_pack_not_found", "story_run_not_found", "story_restart_required", "invalid_current_node", "missing_choice_id",
                      "invalid_choice_option", "invalid_target_node"}
            err_str = str(exc)
            if any(err_str.startswith(p) for p in ("condition_not_met", "action_failed", "max_conversation_turns_reached")) or err_str in public:
                reason = err_str
            else:
                reason = "model_presentation_invalid"
            raise HTTPException(400, reason) from exc

    @router.post("/story/cancel")
    async def story_cancel(payload: StoryStepRequest, request: Request):
        authorize(request)
        return service.story.cancel(payload)

    @router.post("/story/reset")
    async def story_reset(payload: StoryStartRequest, request: Request):
        authorize(request)
        return {"ok": await asyncio.to_thread(service.story.reset, payload.identity, payload.story_id)}

    @router.post("/story/import", response_model=StoryCatalogResponse)
    async def story_import(payload: StoryImportRequest, request: Request):
        authorize(request)
        try:
            return await asyncio.to_thread(service.story.import_story, payload.identity, payload.file_name, payload.content)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.get("/assets/{relative:path}")
    async def asset(relative: str):
        try:
            path = service.resources.file(relative)
        except (ValueError, OSError) as exc:
            raise HTTPException(404, "resource_unavailable") from exc
        return FileResponse(path, headers={"Vary": "Origin"})

    return router
