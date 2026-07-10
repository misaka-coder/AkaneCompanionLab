from __future__ import annotations

from contextlib import asynccontextmanager
import secrets
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from services.market_data.types import MarketDataValidationError

from .runtime import EmQuantBridgeRuntime


LOOPBACK_CLIENT_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "testclient"})


class NewsQueryBody(BaseModel):
    codes: list[str] = Field(min_length=1, max_length=100)
    content_types: list[str] = Field(min_length=1, max_length=64)
    mode: int = 2
    options: str = ""


class QuoteSnapshotBody(BaseModel):
    codes: list[str] = Field(min_length=1, max_length=100)
    indicators: list[str] = Field(
        default_factory=lambda: ["TIME", "PRECLOSE", "OPEN", "HIGH", "LOW", "NOW", "VOLUME", "AMOUNT"],
        min_length=1,
        max_length=64,
    )
    options: str = "Ispandas=0"


class PriceSeriesBody(BaseModel):
    codes: list[str] = Field(min_length=1, max_length=10)
    indicators: list[str] = Field(
        default_factory=lambda: ["OPEN", "HIGH", "LOW", "CLOSE", "VOLUME", "AMOUNT"],
        min_length=1,
        max_length=16,
    )
    start_date: str
    end_date: str
    options: str = "Period=1,AdjustFlag=1,Order=1,RowIndex=1,Ispandas=0"


class SubscriptionBody(BaseModel):
    subscription_id: str
    kind: str
    codes: list[str] = Field(min_length=1, max_length=100)
    fields: list[str] = Field(min_length=1, max_length=64)
    options: str = ""
    enabled: bool = True


def create_emquant_bridge_app(
    runtime: EmQuantBridgeRuntime,
    *,
    access_token: str = "",
) -> FastAPI:
    expected_token = str(access_token or "").strip()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        runtime.stop()

    app = FastAPI(title="Akane EmQuant Bridge", version="1.0", lifespan=lifespan)
    app.state.emquant_runtime = runtime

    @app.middleware("http")
    async def require_loopback(request: Request, call_next):
        client_host = str(request.client.host if request.client else "").strip().lower()
        if client_host and client_host not in LOOPBACK_CLIENT_HOSTS:
            return JSONResponse(
                {"ok": False, "status": "forbidden", "reason": "EmQuant bridge is loopback-only"},
                status_code=403,
            )
        if expected_token:
            authorization = str(request.headers.get("authorization") or "").strip()
            bearer = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
            header_token = str(request.headers.get("x-emquant-bridge-token") or "").strip()
            supplied = bearer or header_token
            if not supplied or not secrets.compare_digest(supplied, expected_token):
                return JSONResponse(
                    {"ok": False, "status": "unauthorized", "reason": "bridge token is required"},
                    status_code=401,
                )
        return await call_next(request)

    @app.exception_handler(MarketDataValidationError)
    async def handle_validation_error(_request: Request, exc: MarketDataValidationError) -> JSONResponse:
        return JSONResponse(exc.to_public_dict(), status_code=400)

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return runtime.health().to_public_dict()

    @app.post("/start")
    async def start() -> JSONResponse:
        result = runtime.start()
        return JSONResponse(result.to_public_dict(), status_code=200 if result.ok else 503)

    @app.post("/stop")
    async def stop() -> JSONResponse:
        result = runtime.stop()
        return JSONResponse(result.to_public_dict(), status_code=200 if result.ok else 503)

    @app.post("/news/query")
    async def query_news(body: NewsQueryBody) -> JSONResponse:
        result = runtime.query_news(
            codes=body.codes,
            content_types=body.content_types,
            mode=body.mode,
            options=body.options,
        )
        return JSONResponse(result.to_public_dict(), status_code=200 if result.ok else 503)

    @app.post("/quotes/snapshot")
    async def quote_snapshot(body: QuoteSnapshotBody) -> JSONResponse:
        result = runtime.quote_snapshot(
            codes=body.codes,
            indicators=body.indicators,
            options=body.options,
        )
        return JSONResponse(result.to_public_dict(), status_code=200 if result.ok else 503)

    @app.post("/prices/series")
    async def price_series(body: PriceSeriesBody) -> JSONResponse:
        result = runtime.price_series(
            codes=body.codes,
            indicators=body.indicators,
            start_date=body.start_date,
            end_date=body.end_date,
            options=body.options,
        )
        return JSONResponse(result.to_public_dict(), status_code=200 if result.ok else 503)

    @app.get("/quota")
    async def quota() -> JSONResponse:
        result = runtime.data_statistics()
        return JSONResponse(result.to_public_dict(), status_code=200 if result.ok else 503)

    @app.get("/subscriptions")
    async def list_subscriptions() -> dict[str, Any]:
        return {
            "ok": True,
            "subscriptions": [state.to_public_dict() for state in runtime.list_subscriptions()],
        }

    @app.post("/subscriptions")
    async def register_subscription(body: SubscriptionBody) -> JSONResponse:
        state = runtime.register_subscription(
            subscription_id=body.subscription_id,
            kind=body.kind,
            codes=body.codes,
            fields=body.fields,
            options=body.options,
            enabled=body.enabled,
        )
        started = runtime.health().logged_in
        ok = not body.enabled or not started or state.active
        return JSONResponse(
            {"ok": ok, "subscription": state.to_public_dict()},
            status_code=200 if ok else 503,
        )

    @app.delete("/subscriptions/{subscription_id}")
    async def disable_subscription(subscription_id: str) -> JSONResponse:
        state = runtime.disable_subscription(subscription_id)
        if state is None:
            return JSONResponse(
                {"ok": False, "status": "not_found", "reason": "subscription does not exist"},
                status_code=404,
            )
        ok = state.status != "cancel_failed"
        return JSONResponse(
            {"ok": ok, "subscription": state.to_public_dict()},
            status_code=200 if ok else 503,
        )

    @app.get("/events")
    async def poll_events(limit: int = 100) -> dict[str, Any]:
        events = runtime.poll_events(limit=limit)
        return {"ok": True, "events": [event.to_public_dict() for event in events]}

    return app
