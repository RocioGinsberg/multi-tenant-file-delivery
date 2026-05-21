from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.db import async_engine
from app.core.settings import get_settings
from app.models.base import Base
from app.services.progress_bus import create_progress_bus
from app.services.redis_client import create_redis_client


@asynccontextmanager
async def lifespan(application: FastAPI):
    from app.api.tasks import init_progress_bus

    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    bus = create_progress_bus(get_settings())
    init_progress_bus(bus)

    try:
        yield
    finally:
        await bus.aclose()
        await async_engine.dispose()


app = FastAPI(title="Auto Upload Control Plane", lifespan=lifespan)

_settings = get_settings()
_cors_origins = (
    ["*"]
    if _settings.cors_origins.strip() == "*"
    else [o.strip() for o in _settings.cors_origins.split(",")]
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from app.api import tasks as tasks_module  # noqa: E402

app.include_router(tasks_module.router, prefix="/api/v1")


@app.get("/healthz", response_class=JSONResponse)
async def healthz() -> dict:
    settings = get_settings()
    checks = {"redis": "disabled"}
    if settings.redis_healthcheck_enabled:
        redis_client = create_redis_client(settings)
        try:
            await redis_client.ping()
            checks["redis"] = "ok"
        finally:
            await redis_client.close()

    return {
        "ok": True,
        "service": "control-plane",
        "env": settings.env,
        "checks": checks,
    }
