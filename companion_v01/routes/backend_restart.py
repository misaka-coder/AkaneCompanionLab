"""Graceful preparation only. The trusted desktop launcher owns process restart."""
import os

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse


def build_backend_restart_router(*, registry, admin_auth):
    router = APIRouter()
    state = {"status": "idle", "reason": ""}

    @router.post("/control-center/backend-restart/prepare")
    async def prepare(request: Request):
        authorization = admin_auth.authorize(request)
        if not authorization.ok:
            return JSONResponse({"ok": False, "reason": authorization.reason}, status_code=authorization.status_code)
        def result(ok, status, reason=""):
            return JSONResponse({"ok": ok, "status": status, "reason": reason, "processId": os.getpid()},
                                headers={"Cache-Control": "no-store"})
        if state["status"] != "idle":
            return result(state["status"] == "ready", state["status"], state["reason"])
        paused = []
        for runtime in registry.values():
            coordinator = getattr(runtime.engine, "turn_coordinator", None)
            queue = getattr(runtime, "session_work_queue", None)
            if ((queue is not None and not queue.is_idle_for_restart())
                    or coordinator is None or not coordinator.pause_if_idle_for_restart()):
                for previous in paused:
                    previous.resume_after_restart_check()
                return result(False, "busy", "conversation_in_progress")
            paused.append(coordinator)
        state["status"] = "preparing"
        try:
            stopped = await registry.stop_all(timeout_seconds=45)
            state.update(status="ready" if stopped.get("status") == "stopped" else "failed",
                         reason="" if stopped.get("status") == "stopped" else "backend_restart_drain_failed")
        except Exception:
            state.update(status="failed", reason="backend_restart_drain_failed")
        return result(state["status"] == "ready", state["status"], state["reason"])

    return router
