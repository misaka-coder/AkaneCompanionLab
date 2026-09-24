"""Local UI QA fixture; all preferences and MemCore records are temporary."""
from __future__ import annotations

import atexit
import asyncio
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import uvicorn

import config
from companion_v01.routes.capabilities import build_capabilities_router
from tests.test_tool_exposure_wire import ToolExposureWireTests
from tests.test_capability_exposure import CountingTool


fixture = ToolExposureWireTests()
fixture.setUp()
atexit.register(fixture.doCleanups)
fixture.engine.tool_handlers.update({
    "demo.weather": CountingTool("demo.weather"), "demo.notes": CountingTool("demo.notes")})
fixture.provider("openai")
fixture.begin(1)
fixture.request()
app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["http://127.0.0.1:1426"], allow_methods=["*"], allow_headers=["*"])

@app.get("/health")
def health():
    return {"status": "ok", "root_binding": "valid", "instance_id": "controlled-ui-fixture"}

@app.get("/capabilities")
def capabilities():
    return {"ok": True, "status": "available", "schemaVersion": 1,
        "capabilities": [{"id": name, "kind": "tool", "type": "tool", "adapter": "tool_runtime",
            "name": name, "enabled": True, "status": "ready", "risk": "low"}
            for name in fixture.engine.tool_handlers]}

@app.middleware("http")
async def slow_save(request: Request, call_next):
    if request.method == "POST" and request.url.path == "/capabilities/tool-exposure":
        await asyncio.sleep(1)
    return await call_next(request)

router = build_capabilities_router(engine=fixture.engine, config_module=config, capability_config_base_dir=fixture.root)
app.router.routes.extend(route for route in router.routes if route.path == "/capabilities/tool-exposure")

@app.post("/preview/model-request")
def model_request():
    prepared, _ = fixture.request()
    return prepared["tool_exposure_lifecycle"]

@app.get("/desktop-pet/diagnostics")
def diagnostics():
    return {"status": "ok", "resources": {}, "capabilities": {"declared": [], "effective_modules": [],
        "tool_names": list(fixture.engine.tool_handlers)}, "workspace": {}, "safety": {"secrets_exposed": False}}

@app.get("/desktop-pet/workspace/summary")
def workspace():
    return {"ok": True, "counts": {"files": 0, "outputs": 0}, "sections": {}}

@app.get("/resource-manifest")
def manifest():
    return {"schema_version": 2, "defaults": {}, "characters": {"outfits": []}}

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=18746, log_level="warning")
