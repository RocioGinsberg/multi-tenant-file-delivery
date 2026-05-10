from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.core.settings import get_settings


@asynccontextmanager
async def lifespan(application: FastAPI):  # noqa: ARG001
    # startup — nothing to initialise yet (DB wired in 1.3)
    yield
    # shutdown


app = FastAPI(title="Auto Upload Control Plane", lifespan=lifespan)


@app.get("/healthz", response_class=JSONResponse)
async def healthz() -> dict:
    return {
        "ok": True,
        "service": "control-plane",
        "env": get_settings().env,
    }
