"""FastAPI entrypoint for the standalone CosDrive local service."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .db import close_pools
from .routers import cosdrive
from .settings import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    close_pools()


app = FastAPI(
    title="CosDrive Local Service",
    version=settings.git_sha,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(cosdrive.router)

_web_root = Path(__file__).resolve().parents[1] / "web"
app.mount("/css", StaticFiles(directory=_web_root / "css"), name="css")
app.mount("/js", StaticFiles(directory=_web_root / "js"), name="js")


@app.get("/")
async def web_index():
    return FileResponse(_web_root / "public" / "index.html")


@app.get("/cosdrive.html")
async def web_compat():
    return FileResponse(_web_root / "public" / "index.html")


@app.get("/healthz")
async def healthz():
    return {"ok": True, "service": settings.service_name}
